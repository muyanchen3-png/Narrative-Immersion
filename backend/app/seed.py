"""演示数据 seeding：生成一段“面试 + 意外”的占位主线视频，并跑通切分理解流程。

使用 FFmpeg drawtext 合成场景，避免依赖外部素材；这样即使首次运行也有可演示的视频。
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import List

from sqlalchemy.orm import Session

from . import models
from .config import settings
from .database import session_scope
from .services import ingest, media
from .services.safety import ensure_default_policies

logger = logging.getLogger(__name__)


DEMO_SCENES: List[dict] = [
    {
        "title": "01 早晨准备",
        "subtitle": "男主在窗前整理西装",
        "voice_text": "新的一天开始，他要面对决定命运的面试。",
        "duration": 6,
    },
    {
        "title": "02 出门",
        "subtitle": "男主走向地铁",
        "voice_text": "城市在他身后醒来，他大步向前。",
        "duration": 6,
    },
    {
        "title": "03 街角",
        "subtitle": "男主在街角驻足",
        "voice_text": "他听见了一阵微弱的呜咽声。",
        "duration": 6,
    },
    {
        "title": "04 巷口的小狗",
        "subtitle": "巷口蜷着一只受伤小狗",
        "voice_text": "巷口的小狗瑟瑟发抖，眼神惊恐。",
        "duration": 6,
    },
    {
        "title": "05 犹豫",
        "subtitle": "男主看了看手表",
        "voice_text": "面试还有半小时，他迟疑了。",
        "duration": 6,
    },
    {
        "title": "06 抉择",
        "subtitle": "男主继续走向地铁",
        "voice_text": "他决定先去面试，再回头处理。",
        "duration": 6,
    },
    {
        "title": "07 面试楼下",
        "subtitle": "男主到达面试地点",
        "voice_text": "面试楼下，他深吸一口气。",
        "duration": 6,
    },
    {
        "title": "08 镜头淡出",
        "subtitle": "电梯关上，故事即将展开",
        "voice_text": "故事的真正分岔点，正在此刻开启。",
        "duration": 6,
    },
]


def ensure_demo_video(db: Session) -> models.VideoAsset:
    existing = (
        db.query(models.VideoAsset)
        .filter(models.VideoAsset.title == "都市面试日（演示）")
        .first()
    )
    if existing:
        return existing

    src_path = settings.uploads_dir / "demo_source.mp4"
    if not src_path.exists():
        _build_demo_source(str(src_path))

    video, _timeline = ingest.ingest_pipeline(
        db,
        src_path=str(src_path),
        title="都市面试日（演示）",
        description=(
            "Demo 用合成视频：男主前往面试，路上遇到受伤小狗。"
            "用户可在第 30-60 秒之间发起干预，例如「让男主先救小狗」、"
            "「让风格变成喜剧」、「让他放弃面试」等。"
        ),
        config={
            "granularities": ["1s", "5s", "scene", "story"],
            "scene_threshold": 0.4,
            "sample_fps": 1.0,
            "profile": "fast",
            "demo": True,
        },
    )
    return video


def _build_demo_source(dst: str) -> None:
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    parts: List[str] = []
    work = settings.uploads_dir / "_demo_parts"
    work.mkdir(parents=True, exist_ok=True)
    for i, scene in enumerate(DEMO_SCENES):
        part = work / f"part_{i:02d}.mp4"
        media.make_color_video_with_voice(
            duration=scene["duration"],
            title=scene["title"],
            subtitle=scene["subtitle"],
            voice_text=scene["voice_text"],
            dst=str(part),
        )
        parts.append(str(part))
    media.concat_reencode(parts, dst)


def seed() -> None:
    with session_scope() as db:
        ensure_default_policies(db)
        ensure_demo_video(db)
        ensure_default_model_configs(db)


def ensure_default_model_configs(db: Session) -> None:
    if db.query(models.ModelConfig).count() > 0:
        return
    presets = [
        ("llm", "fast", "OpenAI GPT-4o-mini", "openai", "gpt-4o-mini"),
        ("llm", "quality", "OpenAI GPT-4o", "openai", "gpt-4o"),
        ("llm", "fallback", "OpenAI GPT-4o-mini（兜底）", "openai", "gpt-4o-mini"),
        ("vlm", "fast", "OpenAI Vision（镜头分析）", "openai", "gpt-4o-mini"),
        ("vlm", "quality", "OpenAI GPT-4o（镜头分析）", "openai", "gpt-4o"),
        ("image", "fast", "MiniMax 图生图（image-01）", "minimax", "image-01"),
        ("image", "quality", "Flux Pro", "replicate", "black-forest-labs/flux-pro"),
        ("video", "fast", "Runway Gen-3 Turbo", "runway", "gen3-turbo"),
        ("video", "quality", "Luma Dream Machine", "luma", "dream-machine-v1"),
        ("video", "fallback", "FFmpeg 占位（mock）", "mock", "drawtext"),
        ("tts", "fast", "OpenAI TTS-1", "openai", "tts-1"),
        ("tts", "quality", "ElevenLabs Multilingual", "elevenlabs", "eleven_multilingual_v2"),
        ("tts", "fallback", "正弦占位（mock）", "mock", "sine"),
    ]
    for kind, profile, name, provider, model in presets:
        db.add(
            models.ModelConfig(
                id=str(uuid.uuid4()),
                kind=kind,
                profile=profile,
                name=name,
                provider=provider,
                model=model,
                params={},
                is_default=(profile == "fast"),
                enabled=True,
            )
        )
    db.flush()
