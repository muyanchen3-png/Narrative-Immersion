"""MiniMax 音色快速复刻：上传复刻/示例音频、调用 voice_clone、可选写回 VoiceProfile。"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import settings
from ..database import get_db
from ..upload_stream import read_uploadfile_bytes
from ..services import media
from ..services.agents import voice as voice_agent
from ..services.minimax_voice import MAX_UPLOAD_BYTES, quick_voice_clone, upload_audio_for_purpose
from ..services.model_resolve import resolve_tts_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice/minimax", tags=["voice-minimax"])

ALLOWED_AUDIO_EXT = {".mp3", ".m4a", ".wav"}


def _tts_credentials(db: Session) -> tuple[str, str]:
    client = resolve_tts_client(db, settings.default_profile or "fast")
    key = (client.api_key or settings.tts_api_key or settings.llm_api_key or "").strip()
    base = (client.base_url or settings.tts_base_url or settings.llm_base_url or "").strip()
    if not base or "minimaxi.com" not in base.lower():
        base = "https://api.minimaxi.com"
    return key, base


@router.post("/upload", response_model=schemas.MinimaxVoiceUploadOut)
async def upload_minimax_voice_audio(
    purpose: str = Form(..., description="voice_clone 或 prompt_audio"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> schemas.MinimaxVoiceUploadOut:
    """
    上传复刻素材或示例短音频，返回 ``file_id``。

    - **voice_clone**：时长 10 秒～5 分钟，≤20MB，格式 mp3/m4a/wav
    - **prompt_audio**：时长 &lt; 8 秒，≤20MB，格式同上
    """

    if purpose not in ("voice_clone", "prompt_audio"):
        raise HTTPException(status_code=400, detail="purpose 须为 voice_clone 或 prompt_audio")

    suffix = Path(file.filename or "audio.bin").suffix.lower()
    if suffix not in ALLOWED_AUDIO_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"仅支持 {', '.join(sorted(ALLOWED_AUDIO_EXT))}",
        )

    raw = await read_uploadfile_bytes(
        file,
        max_bytes=MAX_UPLOAD_BYTES,
        detail_over="文件超过 20MB",
    )

    api_key, base_url = _tts_credentials(db)
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="未配置 MiniMax API Key（HERMES_TTS_API_KEY 或 HERMES_LLM_API_KEY）",
        )

    tmp_path = Path(tempfile.mkstemp(suffix=suffix)[1])
    try:
        tmp_path.write_bytes(raw)
        info = media.probe(str(tmp_path))
        dur = float(info.duration or 0.0)
        if purpose == "voice_clone":
            if dur < 10.0 - 0.05:
                raise HTTPException(status_code=400, detail="复刻音频时长须不少于约 10 秒")
            if dur > 300.0 + 1.0:
                raise HTTPException(status_code=400, detail="复刻音频时长须不超过 5 分钟")
        else:
            if dur >= 8.0 - 0.05:
                raise HTTPException(status_code=400, detail="示例音频须短于 8 秒")

        fid = upload_audio_for_purpose(
            api_key=api_key,
            base_url=base_url,
            file_path=tmp_path,
            purpose=purpose,
        )
        return schemas.MinimaxVoiceUploadOut(
            file_id=fid,
            purpose=purpose,
            duration_seconds=round(dur, 2),
        )
    finally:
        tmp_path.unlink(missing_ok=True)


@router.post("/clone", response_model=schemas.MinimaxVoiceCloneOut)
def minimax_voice_clone_execute(
    body: schemas.MinimaxVoiceCloneIn,
    db: Session = Depends(get_db),
) -> schemas.MinimaxVoiceCloneOut:
    """
    调用 MiniMax **快速复刻** ``POST /v1/voice_clone``。

    完成后若填写 ``video_id`` + ``character``，将把 ``voice_id`` 写入该角色的 ``VoiceProfile``，
    后续叙事生成里 TTS 将使用该音色（需 ``HERMES_TTS_PROVIDER=minimax`` 且兼容接口）。
    """

    api_key, base_url = _tts_credentials(db)
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="未配置 MiniMax API Key（HERMES_TTS_API_KEY 或 HERMES_LLM_API_KEY）",
        )

    model = (body.model or settings.minimax_voice_clone_model or "speech-2.8-hd").strip()

    try:
        resp = quick_voice_clone(
            api_key=api_key,
            base_url=base_url,
            file_id=body.file_id.strip(),
            voice_id=body.voice_id.strip(),
            text=body.text.strip(),
            model=model,
            prompt_file_id=(body.prompt_file_id or "").strip() or None,
            prompt_text=(body.prompt_text or "").strip() or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    updated = False
    if body.video_id and body.character:
        v = db.get(models.VideoAsset, body.video_id)
        if not v:
            raise HTTPException(status_code=404, detail="成片不存在")
        prof = voice_agent.get_or_create_profile(
            db, video_id=body.video_id, character=body.character.strip()
        )
        prof.voice_id = body.voice_id.strip()[:64]
        prof.allow_clone = True
        db.commit()
        updated = True

    return schemas.MinimaxVoiceCloneOut(
        voice_id=body.voice_id.strip()[:64],
        clone_raw=resp if isinstance(resp, dict) else {},
        voice_profile_updated=updated,
    )
