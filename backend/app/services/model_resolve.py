"""根据 ModelConfig（params.api_key 等）解析各类模型客户端。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from .. import models
from ..config import settings
from .llm import LLMClient, _normalize_openai_base_url
from .tts import TTSClient
from .video_gen import VideoGenClient

logger = logging.getLogger(__name__)


def _row_has_stored_key(cfg: models.ModelConfig) -> bool:
    pk = (cfg.params or {}).get("api_key")
    return isinstance(pk, str) and bool(pk.strip())


def _sort_configs_for_resolve(cfgs: list[models.ModelConfig]) -> list[models.ModelConfig]:
    """priority 降序，其次 is_default，其次创建时间。"""

    return sorted(
        cfgs,
        key=lambda c: (-int(c.priority or 0), -int(bool(c.is_default)), str(c.id)),
    )


def resolve_model_row(db: Session, kind: str, profile: str = "fast") -> Optional[models.ModelConfig]:
    """在已启用的 kind 配置中选：先匹配 ``profile``，再放宽到其他 profile。

    同一范围内：**优先**选用 ``params.api_key`` 已保存的行（便于多密钥并存）；若没有任何带密钥行，
    仍可选用仅配置了 provider/model/base_url 的行，密钥由 ``_merge_credentials`` 从环境变量补上。
    """

    q_prof = (
        db.query(models.ModelConfig)
        .filter(
            models.ModelConfig.kind == kind,
            models.ModelConfig.profile == profile,
            models.ModelConfig.enabled.is_(True),
        )
        .all()
    )
    cand = [c for c in q_prof if _row_has_stored_key(c)]
    cand = _sort_configs_for_resolve(cand)
    if cand:
        return cand[0]
    cand = _sort_configs_for_resolve(list(q_prof))
    if cand:
        return cand[0]

    q_any = (
        db.query(models.ModelConfig)
        .filter(
            models.ModelConfig.kind == kind,
            models.ModelConfig.enabled.is_(True),
        )
        .all()
    )
    q_other = [c for c in q_any if (c.profile or "") != profile]
    cand = [c for c in q_other if _row_has_stored_key(c)]
    cand = _sort_configs_for_resolve(cand)
    if cand:
        return cand[0]
    cand = _sort_configs_for_resolve(q_other)
    return cand[0] if cand else None


def _cfg_or_none(db: Session, kind: str, profile: str = "fast") -> Optional[models.ModelConfig]:
    """若未启用 ``use_sqlite_model_configs``，忽略 DB；否则返回 ``resolve_model_row``（可无库内密钥）。"""

    if not settings.use_sqlite_model_configs:
        return None
    return resolve_model_row(db, kind, profile)


def _env_api_key_usable(key: Optional[str]) -> bool:
    return bool(key and str(key).strip())


def _merge_credentials(
    cfg: Optional[models.ModelConfig],
    *,
    default_provider: str,
    default_model: str,
    default_base_url: str,
    default_api_key: Optional[str],
    prefer_env_when_key: Optional[str] = None,
) -> Tuple[str, str, str, Optional[str]]:
    """合并策略（前端 SQLite + 环境变量 ``HERMES_*``）。

    - **无匹配配置行**或未启用 ``use_sqlite_model_configs``：完全使用 ``default_*``（来自 .env）。
    - **有配置行**：``provider`` / ``model`` / ``base_url`` 以库中非空字段为准，缺省项用 ``default_*`` 补齐；
      ``api_key`` **优先** ``params.api_key``（前端保存的密钥），未保存则用环境中的
      ``default_api_key``，仍为空时再试 ``prefer_env_when_key``（与各链路 resolve 传入的后备一致）。
    """

    if not settings.use_sqlite_model_configs or cfg is None:
        api_key = default_api_key
        if not _env_api_key_usable(api_key) and _env_api_key_usable(prefer_env_when_key):
            api_key = prefer_env_when_key
        return default_provider, default_model, default_base_url, api_key

    params = cfg.params or {}
    pk = params.get("api_key")
    db_key = pk.strip() if isinstance(pk, str) and pk.strip() else None
    api_key = db_key or default_api_key or prefer_env_when_key
    if isinstance(api_key, str):
        t = api_key.strip()
        api_key = t if t else None

    base_url = (cfg.base_url or "").strip() or default_base_url
    model = (cfg.model or "").strip() or default_model
    raw_pv = (cfg.provider or "").strip() or default_provider
    provider = raw_pv.lower()
    return provider, model, base_url, api_key


def resolve_llm_client(db: Session, profile: str = "fast", *, kind: str = "llm") -> LLMClient:
    """对话 / 文本链路：kind 多为 llm；镜头分析可走 kind=vlm（同一 Chat Completions 协议）。

    **凭证优先级**：启用 ``use_sqlite_model_configs`` 时，按 ``resolve_model_row`` 选中配置行后，
    **前端字段**（provider/model/base_url）与 **库内或 .env 密钥** 按 ``_merge_credentials`` 合并；
    无配置行或关闭读库时完全使用环境变量。

    kind=llm 且库内无 llm 行时，可复用 kind=vlm 的那一行（便于只配一套密钥）。
    """

    if kind == "vlm":
        env_gate_key = settings.vlm_api_key or settings.llm_api_key
        cfg = _cfg_or_none(db, kind, profile)
        provider, model, base_url, api_key = _merge_credentials(
            cfg,
            default_provider=settings.vlm_provider,
            default_model=settings.vlm_model,
            default_base_url=settings.vlm_base_url or settings.llm_base_url,
            default_api_key=settings.vlm_api_key or settings.llm_api_key,
            prefer_env_when_key=env_gate_key,
        )
    else:
        env_gate_key = settings.llm_api_key
        cfg = _cfg_or_none(db, kind, profile)
        if cfg is None:
            cfg = _cfg_or_none(db, "vlm", profile)
        provider, model, base_url, api_key = _merge_credentials(
            cfg,
            default_provider=settings.llm_provider,
            default_model=settings.llm_model,
            default_base_url=settings.llm_base_url,
            default_api_key=settings.llm_api_key,
            prefer_env_when_key=env_gate_key,
        )
    if provider not in ("openai", "mock", "minimax", "gemma4"):
        provider = "openai"
    return LLMClient(provider=provider, model=model, base_url=base_url, api_key=api_key)


def resolve_tts_client(db: Session, profile: str = "fast") -> TTSClient:
    env_gate_key = settings.tts_api_key or settings.llm_api_key
    cfg = _cfg_or_none(db, "tts", profile)
    provider, model, base_url, api_key = _merge_credentials(
        cfg,
        default_provider=settings.tts_provider,
        default_model=settings.tts_model,
        default_base_url=settings.tts_base_url or settings.llm_base_url,
        default_api_key=settings.tts_api_key or settings.llm_api_key,
        prefer_env_when_key=env_gate_key,
    )
    bu = (base_url or "").lower()
    if "minimaxi.com" in bu and provider not in ("minimax", "mock", "elevenlabs"):
        logger.info(
            "TTS base_url 为 MiniMax 域名，已将 provider=%s 纠正为 minimax（优先走官方 T2A）",
            provider,
        )
        provider = "minimax"
    # 未单独配置 TTS 时，若视频已配置 MiniMax，复用同一密钥并优先走官方 T2A
    vk = (settings.video_api_key or "").strip()
    vb = (settings.video_base_url or "").strip().lower()
    tk = (api_key or "").strip()
    if (not tk or provider == "mock") and vk and "minimaxi.com" in vb:
        provider = "minimax"
        api_key = vk
        base_url = settings.video_base_url or base_url
        if (model or "").strip() in ("", "tts-1"):
            model = settings.minimax_voice_clone_model
    return TTSClient(provider=provider, model=model, base_url=base_url, api_key=api_key)


def list_video_model_config_rows(db: Session, profile: str = "fast") -> List[models.ModelConfig]:
    """所有已启用、kind=video 的配置行：当前 profile 优先，其余 profile 随后；组内按 priority / is_default / id 排序。"""

    if not settings.use_sqlite_model_configs:
        return []
    all_v = (
        db.query(models.ModelConfig)
        .filter(
            models.ModelConfig.kind == "video",
            models.ModelConfig.enabled.is_(True),
        )
        .all()
    )
    same = [c for c in all_v if (c.profile or "") == profile]
    other = [c for c in all_v if (c.profile or "") != profile]
    return _sort_configs_for_resolve(same) + _sort_configs_for_resolve(other)


def resolve_video_gen_client(db: Session, profile: str = "fast") -> VideoGenClient:
    cfg = _cfg_or_none(db, "video", profile)
    env_gate_key = settings.video_api_key or settings.llm_api_key
    provider, model, base_url, api_key = _merge_credentials(
        cfg,
        default_provider=settings.video_provider,
        default_model=settings.video_model,
        default_base_url=settings.video_base_url or settings.llm_base_url,
        default_api_key=settings.video_api_key or settings.llm_api_key,
        prefer_env_when_key=env_gate_key,
    )
    bu = (base_url or "").lower()
    if "minimaxi.com" in bu and provider not in ("minimax", "mock"):
        logger.warning(
            "video base_url 为 api.minimaxi.com，已将 provider=%s 纠正为 minimax",
            provider,
        )
        provider = "minimax"

    video_model_candidates: Optional[List[str]] = None
    rows = list_video_model_config_rows(db, profile)
    if rows:
        mc: List[str] = []
        for row in rows:
            _, m, _, _ = _merge_credentials(
                row,
                default_provider=settings.video_provider,
                default_model=settings.video_model,
                default_base_url=settings.video_base_url or settings.llm_base_url,
                default_api_key=settings.video_api_key or settings.llm_api_key,
                prefer_env_when_key=env_gate_key,
            )
            t = (m or "").strip()
            if t and t not in mc:
                mc.append(t)
        if mc:
            video_model_candidates = mc

    return VideoGenClient(
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
        video_model_candidates=video_model_candidates,
    )


class ImageGenClient:
    """生图客户端：OpenAI 兼容 `images/generations`；失败时由 `image_generate` 回退占位图。"""

    def __init__(self, *, provider: str, model: str, base_url: str, api_key: Optional[str]) -> None:
        self.provider = provider
        self.model = model
        self.base_url = base_url
        self.api_key = api_key

    def generate_turnaround_sheet(
        self,
        *,
        character_label: str,
        appearance_prompt: str,
        reference_image: Optional[Path],
        out_sheet: Path,
    ) -> bool:
        from . import image_generate

        return image_generate.generate_turnaround_sheet(
            provider=self.provider,
            model=self.model,
            base_url=self.base_url,
            api_key=self.api_key,
            character_label=character_label,
            appearance_prompt=appearance_prompt,
            reference_image=reference_image,
            out_sheet=out_sheet,
        )


def resolve_image_gen_client(db: Session, profile: str = "fast") -> ImageGenClient:
    env_gate_key = settings.image_api_key or settings.llm_api_key
    cfg = _cfg_or_none(db, "image", profile)
    provider, model, base_url, api_key = _merge_credentials(
        cfg,
        default_provider=settings.image_provider,
        default_model=settings.image_model,
        default_base_url=settings.image_base_url or settings.llm_base_url,
        default_api_key=settings.image_api_key or settings.llm_api_key,
        prefer_env_when_key=env_gate_key,
    )
    # 库内默认「image = openai + dall-e」与 .env 的 MiniMax 根 URL 同时存在时，会误调
    # …/v1/images/generations（OpenAI）到 api.minimaxi.com → 404。按域名强制走官方图生图协议。
    bu = (base_url or "").lower()
    if "minimaxi.com" in bu:
        if provider not in ("minimax",):
            logger.info(
                "生图 API 域名为 MiniMax，已将 provider=%s 纠正为 minimax（避免请求 OpenAI images/generations）",
                provider,
            )
            provider = "minimax"
        if model not in ("image-01", "image-01-live"):
            fallback = settings.image_model
            model = fallback if fallback in ("image-01", "image-01-live") else "image-01"
    return ImageGenClient(provider=provider, model=model, base_url=base_url, api_key=api_key)


def resolve_asr_credentials(db: Session, profile: str = "fast") -> Tuple[str, str, str, str]:
    """ASR：返回 ``(base_url, api_key, model, provider)``，与 ``asr`` 模块 OpenAI 兼容转写一致。"""
    cfg = _cfg_or_none(db, "asr", profile)
    default_base = (settings.asr_base_url or settings.llm_base_url or "").strip() or "https://api.openai.com/v1"
    provider, model, base_url, api_key = _merge_credentials(
        cfg,
        default_provider=settings.asr_provider,
        default_model=settings.asr_model,
        default_base_url=default_base,
        default_api_key=settings.asr_api_key or settings.llm_api_key,
        prefer_env_when_key=settings.asr_api_key or settings.llm_api_key,
    )
    raw = (base_url or "").strip() or default_base
    base_norm = _normalize_openai_base_url(raw).rstrip("/")
    return base_norm, api_key or "", model, provider
