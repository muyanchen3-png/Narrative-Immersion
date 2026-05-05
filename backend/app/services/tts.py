"""TTS / 配音提供商抽象。Mock 模式下生成可听的占位音频。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

import httpx

from ..config import settings
from . import media
from .llm import _normalize_openai_base_url

logger = logging.getLogger(__name__)

# OpenAI 兼容音色名 / 占位 id，无法直接作为 MiniMax voice_id 使用
_MINIMAX_VOICE_FALLBACK_MARKERS = frozenset(
    {
        "",
        "alloy",
        "echo",
        "fable",
        "onyx",
        "nova",
        "shimmer",
        "mock-voice-1",
    }
)


def _minimax_api_origin(base_url: Optional[str]) -> str:
    """从 base_url 提取 MiniMax API 根地址；无 minimaxi 域名时用官方 ``api.minimaxi.com``。"""
    raw = (base_url or "").strip()
    if "minimaxi.com" in raw.lower():
        if "://" not in raw:
            raw = "https://" + raw
        p = urlparse(raw)
        if p.netloc:
            return f"{p.scheme or 'https'}://{p.netloc}"
    return "https://api.minimaxi.com"


class TTSClient:
    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        self.provider = provider or settings.tts_provider
        self.model = model or settings.tts_model
        self.api_key = api_key or settings.tts_api_key or settings.llm_api_key
        raw = base_url or settings.tts_base_url or settings.llm_base_url
        # 与 resolve_tts_client 一致：仅配了视频 MiniMax 时，TTS 也走同一密钥并优先 T2A
        bu = (raw or "").lower()
        if "minimaxi.com" in bu and self.provider not in ("minimax", "mock", "elevenlabs"):
            self.provider = "minimax"
        vk = (settings.video_api_key or "").strip()
        vb = (settings.video_base_url or "").strip().lower()
        tk = (self.api_key or "").strip()
        if (not tk or self.provider == "mock") and vk and "minimaxi.com" in vb:
            self.provider = "minimax"
            self.api_key = vk
            raw = settings.video_base_url or raw
            if (self.model or "").strip() in ("", "tts-1"):
                self.model = settings.minimax_voice_clone_model
        self.base_url = _normalize_openai_base_url(raw)

    def synthesize(self, text: str, *, voice_id: str, dst: str) -> str:
        if self.provider == "mock" or not self.api_key:
            return self._mock(text, dst)
        if self.provider == "minimax":
            try:
                return self._minimax_native_t2a(text, voice_id, dst)
            except Exception as exc:  # noqa: BLE001
                logger.warning("MiniMax 官方 T2A 失败，回退 OpenAI 兼容 /audio/speech：%s", exc)
            return self._openai(text, voice_id, dst)
        if self.provider == "openai":
            return self._openai(text, voice_id, dst)
        return self._mock(text, dst)

    def _minimax_voice_id_for_t2a(self, voice_id: str) -> str:
        v = (voice_id or "").strip()
        if not v or v in _MINIMAX_VOICE_FALLBACK_MARKERS or v.lower().startswith("mock"):
            return settings.tts_minimax_default_voice_id
        return v

    def _minimax_speech_model_name(self) -> str:
        m = (self.model or "").strip()
        if m.startswith("speech-"):
            return m
        return settings.minimax_voice_clone_model

    def _minimax_native_t2a(self, text: str, voice_id: str, dst: str) -> str:
        """POST ``/v1/t2a_v2``，响应中 ``data.audio`` 为 hex 编码的 mp3 字节。"""
        origin = _minimax_api_origin(self.base_url)
        url = origin.rstrip("/") + "/v1/t2a_v2"
        body = {
            "model": self._minimax_speech_model_name(),
            "text": text,
            "stream": False,
            "voice_setting": {
                "voice_id": self._minimax_voice_id_for_t2a(voice_id),
                "speed": 1,
                "vol": 1,
                "pitch": 0,
            },
            "audio_setting": {
                "sample_rate": 32000,
                "bitrate": 128000,
                "format": "mp3",
                "channel": 1,
            },
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            payload = resp.json()
        base_resp = payload.get("base_resp") or {}
        code = base_resp.get("status_code")
        if code is not None and int(code) != 0:
            raise RuntimeError(
                f"MiniMax T2A 业务错误: {base_resp.get('status_msg', '')} (code={code})"
            )
        data = payload.get("data") or {}
        audio_hex = data.get("audio")
        if not audio_hex or not isinstance(audio_hex, str):
            raise RuntimeError("MiniMax T2A 响应缺少 data.audio")
        raw_audio = bytes.fromhex(audio_hex.strip())
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        Path(dst).write_bytes(raw_audio)
        return dst

    def _mock(self, text: str, dst: str) -> str:
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        # 估算时长：每个汉字 0.25s，每个英文 0.07s
        dur = 1.0 + sum(0.25 if "\u4e00" <= c <= "\u9fff" else 0.07 for c in text)
        media.make_tts_placeholder_audio(text, dur, dst)
        return dst

    def _openai(self, text: str, voice_id: str, dst: str) -> str:
        url = self.base_url.rstrip("/") + "/audio/speech"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            with httpx.Client(timeout=120.0) as client:
                resp = client.post(
                    url,
                    headers=headers,
                    json={"model": self.model, "voice": voice_id or "alloy", "input": text},
                )
                resp.raise_for_status()
                Path(dst).parent.mkdir(parents=True, exist_ok=True)
                Path(dst).write_bytes(resp.content)
                return dst
        except Exception as exc:  # noqa: BLE001
            logger.warning("TTS 调用失败，回退 mock：%s", exc)
            return self._mock(text, dst)


def get_tts(db: Optional["Session"] = None, profile: str = "fast") -> TTSClient:
    if db is not None:
        from .model_resolve import resolve_tts_client

        return resolve_tts_client(db, profile)
    return TTSClient()
