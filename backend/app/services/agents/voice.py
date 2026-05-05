"""配音智能体：基于角色 voice_profile 调用 TTS 生成对白/旁白音频。"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from ... import models
from ...config import settings
from ..tts import get_tts


def get_or_create_profile(db: Session, *, video_id: str, character: str) -> models.VoiceProfile:
    profile = (
        db.query(models.VoiceProfile)
        .filter(
            models.VoiceProfile.video_id == video_id,
            models.VoiceProfile.character == character,
        )
        .first()
    )
    if profile:
        return profile
    profile = models.VoiceProfile(
        id=str(uuid.uuid4()),
        video_id=video_id,
        character=character,
        language="zh-CN",
        gender="male" if character in ("男主",) else "female" if character in ("女主",) else "neutral",
        age_band="adult",
        style="natural",
        voice_id=f"mock-{abs(hash(character)) % 5}",
        allow_clone=False,
    )
    db.add(profile)
    db.flush()
    return profile


def synthesize_lines(
    db: Session,
    *,
    job_id: str,
    video_id: str,
    lines: List[Dict],
    profile: str = "fast",
) -> List[Dict]:
    """对每条台词调用 TTS，返回 [{text, character, file_path, voice_id}]。"""

    out: List[Dict] = []
    if not lines:
        return out
    tts = get_tts(db, profile)
    audio_root = settings.audio_dir / job_id
    audio_root.mkdir(parents=True, exist_ok=True)

    for i, line in enumerate(lines):
        text = (line.get("line") or line.get("text") or "").strip()
        character = line.get("character") or "旁白"
        if not text:
            continue
        profile = get_or_create_profile(db, video_id=video_id, character=character)
        dst = audio_root / f"{i:03d}_{character}.m4a"
        tts.synthesize(text, voice_id=profile.voice_id, dst=str(dst))
        out.append(
            {
                "character": character,
                "text": text,
                "file_path": str(dst),
                "voice_id": profile.voice_id,
            }
        )
    return out
