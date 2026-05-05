"""镜头片段音轨语音识别（ASR），OpenAI 兼容 POST /v1/audio/transcriptions（如 whisper-1）。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

import httpx

from ..config import settings

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from .. import models
from . import media
from .llm import _normalize_openai_base_url

logger = logging.getLogger(__name__)

_MINIMAX_ASR_UNSUPPORTED_LOGGED = False
_ASR_DISABLED_NO_KEY_LOGGED = False


def _resolve_asr_endpoint() -> Tuple[str, str]:
    raw = (settings.asr_base_url or settings.llm_base_url or "").strip()
    if not raw:
        base = "https://api.openai.com/v1"
    else:
        base = _normalize_openai_base_url(raw).rstrip("/")
    key = settings.asr_api_key or settings.llm_api_key or ""
    return base, key


def is_asr_enabled(db: Optional["Session"] = None) -> bool:
    """
    为 True 时才会对镜头抽 wav 并请求远端转写。
    mock、无可用密钥、或基址为 MiniMax（无 OpenAI 兼容 transcriptions）时为 False——此时不切音频、不调接口，不影响切分与 VLM。
    """
    if settings.asr_provider == "mock":
        return False
    global _ASR_DISABLED_NO_KEY_LOGGED
    if db is not None and settings.use_sqlite_model_configs:
        from .model_resolve import resolve_asr_credentials

        base, key, _, _ = resolve_asr_credentials(db)
        if not (key or "").strip():
            if not _ASR_DISABLED_NO_KEY_LOGGED:
                _ASR_DISABLED_NO_KEY_LOGGED = True
                logger.info(
                    "ASR：未配置密钥（数据库 asr 行或 .env），已跳过音轨转写；切分与画面分析照常。"
                )
            return False
        if "minimaxi.com" in (base or "").lower():
            _log_minimax_asr_unsupported_once()
            return False
        return True
    base, key = _resolve_asr_endpoint()
    if not (key or "").strip():
        if not _ASR_DISABLED_NO_KEY_LOGGED:
            _ASR_DISABLED_NO_KEY_LOGGED = True
            logger.info(
                "ASR：未配置密钥（HERMES_ASR_API_KEY 或未回退到 LLM Key），已跳过音轨转写；切分与画面分析照常。"
            )
        return False
    if "minimaxi.com" in (base or "").lower():
        _log_minimax_asr_unsupported_once()
        return False
    return True


def _log_minimax_asr_unsupported_once() -> None:
    global _MINIMAX_ASR_UNSUPPORTED_LOGGED
    if _MINIMAX_ASR_UNSUPPORTED_LOGGED:
        return
    _MINIMAX_ASR_UNSUPPORTED_LOGGED = True
    logger.info(
        "ASR：当前 ASR 基址为 api.minimaxi.com，该平台无 OpenAI 兼容的 /v1/audio/transcriptions，已跳过（避免每镜 404）。"
        "需要转写请单独配置 OpenAI 兼容 ASR 的 HERMES_ASR_BASE_URL，或设 HERMES_ASR_PROVIDER=mock。"
    )


def merge_dialogue(vlm_dialogue: Optional[str], asr_text: str) -> str:
    """对白字段：优先 ASR 正文；若 VLM 另有推断则附在后便于对照。"""
    asr_text = (asr_text or "").strip()
    if not asr_text:
        return (vlm_dialogue or "").strip()
    vlm = (vlm_dialogue or "").strip()
    if not vlm:
        return asr_text
    if vlm == asr_text or vlm in asr_text or asr_text in vlm:
        return asr_text
    return f"{asr_text}\n\n（画面理解对白推断）{vlm}"


def transcribe_audio_file(
    wav_path: Path,
    *,
    language: Optional[str] = None,
    db: Optional["Session"] = None,
) -> str:
    """
    调用 OpenAI 兼容语音转写接口，返回纯文本。
    provider=mock 或无密钥时返回空字符串。
    """
    if not wav_path.is_file():
        return ""
    if not is_asr_enabled(db):
        return ""

    if db is not None and settings.use_sqlite_model_configs:
        from .model_resolve import resolve_asr_credentials

        base, api_key, model, _ = resolve_asr_credentials(db)
    else:
        base, api_key = _resolve_asr_endpoint()
        model = settings.asr_model or "whisper-1"

    url = base + "/audio/transcriptions"
    lang = language if language is not None else settings.asr_language

    try:
        with wav_path.open("rb") as audio_file:
            files = {"file": (wav_path.name, audio_file, "audio/wav")}
            data: Dict[str, Any] = {"model": model}
            if lang:
                data["language"] = lang
            headers = {"Authorization": f"Bearer {api_key}"}
            with httpx.Client(timeout=180.0) as client:
                resp = client.post(url, headers=headers, files=files, data=data)
                resp.raise_for_status()
                out = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("ASR 请求失败：%s", exc)
        return ""

    text = out.get("text") if isinstance(out, dict) else None
    return str(text).strip() if text else ""


def transcribe_segment_video(segment_mp4: Path, *, db: Optional["Session"] = None) -> Tuple[str, Optional[Path]]:
    """
    从镜头 mp4 抽取 wav 并转写。
    返回 (识别文本, 持久化 wav 路径)；无音轨或失败时文本为空，wav 可能为 None。
    """
    if not is_asr_enabled(db):
        return "", None

    segment_mp4 = Path(segment_mp4)
    if not segment_mp4.is_file():
        return "", None

    try:
        info = media.probe(str(segment_mp4))
    except Exception as exc:  # noqa: BLE001
        logger.warning("ASR：probe 失败 %s", exc)
        return "", None

    if not getattr(info, "has_audio", False):
        logger.info("ASR：片段无音轨，跳过 %s", segment_mp4.name)
        return "", None

    wav_path = segment_mp4.with_suffix(".wav")
    try:
        ok = media.extract_audio_wav(str(segment_mp4), str(wav_path))
    except Exception as exc:  # noqa: BLE001
        logger.warning("ASR：抽音频失败 %s", exc)
        return "", None

    if not ok or not wav_path.is_file():
        return "", None

    text = transcribe_audio_file(wav_path, db=db)
    return text, wav_path


def enrich_shot_fields_with_asr(
    segment_mp4: Path, fields: Dict[str, Any], *, db: Optional["Session"] = None
) -> bool:
    """就地合并 dialogue，并写入 audio_path（wav）。返回是否得到非空识别文本。"""
    text, wav = transcribe_segment_video(segment_mp4, db=db)
    if wav is not None:
        fields["audio_path"] = str(wav)
    if text:
        fields["dialogue"] = merge_dialogue(fields.get("dialogue"), text)
    return bool((text or "").strip())


def apply_asr_to_shot_row(
    shot: "models.ShotSegment", db: Optional["Session"] = None
) -> Tuple[Dict[str, Any], bool]:
    """
    对已落库的 ShotSegment 再跑 ASR，返回应写回的字段子集（dialogue、audio_path），
    以及是否得到非空 ASR 文本（便于批量统计）。
    """
    fields = {"dialogue": getattr(shot, "dialogue", None), "audio_path": getattr(shot, "audio_path", None)}
    had = enrich_shot_fields_with_asr(Path(str(shot.file_path)), fields, db=db)
    return fields, had
