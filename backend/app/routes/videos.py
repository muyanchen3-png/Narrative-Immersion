from __future__ import annotations

import json
from datetime import datetime
import logging
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import settings
from ..database import get_db, run_with_sqlite_lock_retry
from ..upload_stream import write_uploadfile_to_path
from ..services import asr, character_enrichment, character_library, ingest, shot_reanalyze
from ..services.ingest_progress import snapshot as ingest_progress_snapshot

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/videos", tags=["videos"])


@router.get("", response_model=List[schemas.VideoOut])
def list_videos(db: Session = Depends(get_db)) -> List[models.VideoAsset]:
    return db.query(models.VideoAsset).order_by(models.VideoAsset.created_at.desc()).all()


@router.post("/upload", response_model=schemas.VideoOut)
async def upload_video(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
    title: str = Form("未命名视频"),
    description: str = Form(""),
    config: str = Form("{}"),
) -> models.VideoAsset:
    try:
        cfg = json.loads(config) if config else {}
    except Exception:
        cfg = {}

    upload_id = str(uuid.uuid4())
    suffix = Path(file.filename or "video.mp4").suffix or ".mp4"
    src_path = settings.uploads_dir / f"raw_{upload_id}{suffix}"
    await write_uploadfile_to_path(file, src_path)

    try:
        video = ingest.register_video(
            db,
            src_path=str(src_path),
            title=title,
            description=description,
            config=cfg,
        )
        db.commit()
        background_tasks.add_task(ingest.run_ingest_background, video.id)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("注册成片失败")
        try:
            src_path.unlink()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"上传处理失败：{exc}") from exc
    finally:
        try:
            src_path.unlink()
        except Exception:
            pass
    db.refresh(video)
    return video


@router.get("/{video_id}/ingest-progress", response_model=schemas.IngestProgressOut)
def get_ingest_progress_route(video_id: str, db: Session = Depends(get_db)) -> schemas.IngestProgressOut:
    """切片与逐镜头分析进度（轮询）；完成后 phase=done，失败见 error 且成片 status=failed。"""

    v = db.get(models.VideoAsset, video_id)
    if not v:
        raise HTTPException(status_code=404, detail="成片不存在")

    snap = ingest_progress_snapshot(video_id)
    if snap:
        return schemas.IngestProgressOut(
            video_id=video_id,
            total=int(snap.get("total") or 0),
            current=int(snap.get("current") or 0),
            phase=str(snap.get("phase") or "unknown"),
            error=snap.get("error"),
        )
    if v.status == "ready":
        return schemas.IngestProgressOut(video_id=video_id, total=1, current=1, phase="done", error=None)
    if v.status == "failed":
        return schemas.IngestProgressOut(
            video_id=video_id, total=0, current=0, phase="error", error="成片处理失败，请重试上传或查看日志"
        )
    return schemas.IngestProgressOut(video_id=video_id, total=0, current=0, phase="pending", error=None)


# 必须先于 GET /{video_id} 注册，避免部分环境下子路径与动态段匹配歧义导致 POST 落到仅允许 GET 的路由上（405）。
@router.post("/{video_id}/resplit", response_model=schemas.VideoOut)
def resplit_video(video_id: str, db: Session = Depends(get_db)) -> models.VideoAsset:
    """保留成片文件，清空镜头与时间线后重新切分（用于修正拆解或更新剧情状态）。"""
    try:
        video, _tl = ingest.resplit_video(db, video_id=video_id)
        db.commit()
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("重新拆解失败")
        raise HTTPException(status_code=500, detail=f"重新拆解失败：{exc}") from exc
    db.refresh(video)
    return video


@router.delete("/{video_id}", response_model=schemas.VideoDeletedOut)
def delete_video_entirely(video_id: str, db: Session = Depends(get_db)) -> schemas.VideoDeletedOut:
    """删除成片记录及镜头、时间线、对话、角色等全部关联数据；删除 uploads 成片/封面、切片目录与角色生成产物。"""
    try:
        ingest.delete_video_completely(db, video_id=video_id)
        db.commit()
    except ValueError as err:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(err)) from err
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("删除成片失败")
        raise HTTPException(status_code=500, detail=f"删除成片失败：{exc}") from exc
    return schemas.VideoDeletedOut(video_id=video_id)


# 路径必须以静态段开头，避免 POST /api/videos/reanalyze-shots（缺少 id 时）被当成 GET /{video_id} 命中而导致 405。
@router.post("/reanalyze-all-shots/{video_id}", response_model=schemas.ReanalyzeShotsOut)
def reanalyze_all_shots(
    video_id: str,
    granularity: Optional[str] = Query(
        None,
        description="仅重新生成该切分粒度下的镜头，例如 10s、scene；不传则处理全部粒度。",
    ),
    db: Session = Depends(get_db),
) -> schemas.ReanalyzeShotsOut:
    """不切分视频，对该成片已有镜头重新生成摘要等（可选只处理某一 granularity）。"""
    try:
        n = shot_reanalyze.reanalyze_all_shots_for_video(db, video_id, granularity=granularity)
        ch = character_library.rebuild_video_characters(db, video_id, granularity=None)
        db.commit()
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("批量重新生成镜头信息失败")
        raise HTTPException(status_code=500, detail=f"批量重新生成失败：{exc}") from exc
    logger.info(
        "批量重新生成镜头完成 video=%s granularity=%s shots_updated=%s characters=%s",
        video_id[:12],
        granularity or "(全部)",
        n,
        ch,
    )
    return schemas.ReanalyzeShotsOut(video_id=video_id, shots_updated=n, characters_count=ch)


@router.get("/{video_id}/shots", response_model=List[schemas.ShotOut])
def list_shots(
    video_id: str,
    granularity: Optional[str] = None,
    db: Session = Depends(get_db),
) -> List[models.ShotSegment]:
    q = db.query(models.ShotSegment).filter(models.ShotSegment.video_id == video_id)
    if granularity:
        q = q.filter(models.ShotSegment.granularity == granularity)
    return q.order_by(models.ShotSegment.granularity, models.ShotSegment.index).all()


@router.delete("/{video_id}/shots/{shot_id}", response_model=schemas.ShotDeletedOut)
def delete_shot(video_id: str, shot_id: str, db: Session = Depends(get_db)) -> schemas.ShotDeletedOut:
    """删除该成片下一条镜头（切片 mp4、缩略图、抽音频等）；时间线片段上对该镜头的引用会被清空。"""
    if not db.get(models.VideoAsset, video_id):
        raise HTTPException(status_code=404, detail="成片不存在")
    try:
        ingest.delete_shot_segment(db, video_id=video_id, shot_id=shot_id)
        db.commit()
    except ValueError as err:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(err)) from err
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("删除镜头失败")
        raise HTTPException(status_code=500, detail=f"删除镜头失败：{exc}") from exc
    return schemas.ShotDeletedOut(video_id=video_id, shot_id=shot_id)


@router.post(
    "/{video_id}/shots/{shot_id}/transcribe-asr",
    response_model=schemas.ShotOut,
)
def transcribe_shot_asr(
    video_id: str,
    shot_id: str,
    db: Session = Depends(get_db),
) -> models.ShotSegment:
    """仅对单条镜头做音轨 ASR，合并到 dialogue，并保存抽出的 wav 路径到 audio_path。"""
    if not db.get(models.VideoAsset, video_id):
        raise HTTPException(status_code=404, detail="视频不存在")
    shot = db.get(models.ShotSegment, shot_id)
    if not shot or shot.video_id != video_id:
        raise HTTPException(status_code=404, detail="镜头不存在")
    try:
        upd, _ = asr.apply_asr_to_shot_row(shot, db)
        shot.dialogue = upd.get("dialogue")
        if upd.get("audio_path"):
            shot.audio_path = upd["audio_path"]
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("单镜 ASR 失败")
        raise HTTPException(status_code=500, detail=f"ASR 失败：{exc}") from exc
    db.refresh(shot)
    return shot


@router.post(
    "/{video_id}/transcribe-asr-bulk",
    response_model=schemas.TranscribeAsrBulkOut,
)
def transcribe_shots_asr_bulk(
    video_id: str,
    granularity: Optional[str] = Query(
        None,
        description="只处理该粒度；不传则处理本视频下全部镜头。",
    ),
    db: Session = Depends(get_db),
) -> schemas.TranscribeAsrBulkOut:
    """对多条镜头依次做音轨 ASR（不重新跑 VLM）。"""
    if not db.get(models.VideoAsset, video_id):
        raise HTTPException(status_code=404, detail="视频不存在")
    q = db.query(models.ShotSegment).filter(models.ShotSegment.video_id == video_id)
    if granularity:
        q = q.filter(models.ShotSegment.granularity == granularity)
    shots = q.order_by(models.ShotSegment.granularity, models.ShotSegment.index).all()
    n_text = 0
    for shot in shots:
        try:
            upd, had_asr = asr.apply_asr_to_shot_row(shot, db)
            shot.dialogue = upd.get("dialogue")
            if upd.get("audio_path"):
                shot.audio_path = upd["audio_path"]
            if had_asr:
                n_text += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("批量 ASR 单条失败 shot=%s: %s", shot.id[:8], exc)
    db.commit()
    return schemas.TranscribeAsrBulkOut(
        video_id=video_id,
        shots_processed=len(shots),
        shots_with_transcript=n_text,
    )


@router.get("/{video_id}/timelines", response_model=List[schemas.TimelineOut])
def list_timelines(video_id: str, db: Session = Depends(get_db)) -> List[models.Timeline]:
    return (
        db.query(models.Timeline)
        .filter(models.Timeline.video_id == video_id)
        .order_by(models.Timeline.created_at)
        .all()
    )


@router.get("/{video_id}/characters", response_model=List[schemas.VideoCharacterOut])
def list_video_characters(video_id: str, db: Session = Depends(get_db)) -> List[models.VideoCharacter]:
    if not db.get(models.VideoAsset, video_id):
        raise HTTPException(status_code=404, detail="视频不存在")
    rows = (
        db.query(models.VideoCharacter)
        .filter(models.VideoCharacter.video_id == video_id)
        .order_by(models.VideoCharacter.mention_count.desc(), models.VideoCharacter.display_name)
        .all()
    )
    changed = False
    for vc in rows:
        if character_enrichment.ensure_character_preview_reference(db, vc):
            changed = True
    if changed:
        db.commit()
    return rows


@router.post("/{video_id}/characters/extract", response_model=schemas.ExtractCharactersOut)
def extract_video_characters(
    video_id: str,
    granularity: Optional[str] = Query(
        None,
        description="仅用该粒度镜头汇总角色（如 scene）；不传则自动选用可用镜头集。",
    ),
    db: Session = Depends(get_db),
) -> schemas.ExtractCharactersOut:
    """从已有镜头的 characters 字段聚合写入本视频的成片角色库。"""
    try:
        if settings.db_url.startswith("sqlite"):

            def _extract_commit() -> int:
                n0 = character_library.rebuild_video_characters(
                    db, video_id, granularity=granularity
                )
                db.commit()
                return n0

            n = run_with_sqlite_lock_retry(_extract_commit, db=db)
        else:
            n = character_library.rebuild_video_characters(
                db, video_id, granularity=granularity
            )
            db.commit()
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("提取角色库失败")
        raise HTTPException(status_code=500, detail=f"提取角色失败：{exc}") from exc
    return schemas.ExtractCharactersOut(video_id=video_id, count=n)


@router.post(
    "/{video_id}/characters/enrich-all",
    response_model=schemas.CharacterEnrichBatchOut,
)
def enrich_all_characters(
    video_id: str,
    profile: str = Query("fast"),
    only_pending: bool = Query(
        True,
        description="为 true 时仅处理 pending / failed / partial / analyzing，跳过已 visual_ready。",
    ),
    db: Session = Depends(get_db),
) -> schemas.CharacterEnrichBatchOut:
    """批量富化该成片下角色（按需跳过已完成）。"""
    if not db.get(models.VideoAsset, video_id):
        raise HTTPException(status_code=404, detail="视频不存在")
    try:
        items = character_enrichment.enrich_all_video_characters(
            db,
            video_id=video_id,
            profile=profile.strip() or "fast",
            only_pending=only_pending,
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("批量角色富化失败")
        raise HTTPException(status_code=500, detail=f"批量富化失败：{exc}") from exc
    return schemas.CharacterEnrichBatchOut(
        video_id=video_id,
        items=[
            schemas.CharacterEnrichItemOut(
                character_id=i["character_id"],
                ok=i["ok"],
                enrichment_status=i["enrichment_status"],
                detail=i.get("detail"),
            )
            for i in items
        ],
    )


@router.post(
    "/{video_id}/characters/{character_id}/enrich",
    response_model=schemas.VideoCharacterOut,
)
def enrich_one_character(
    video_id: str,
    character_id: str,
    profile: str = Query("fast", description="模型 profile（对应 ModelConfig）"),
    db: Session = Depends(get_db),
) -> models.VideoCharacter:
    """从关联镜头的字幕/摘要调用人物身份智能体，抽取参照帧并生成三视图设定图。"""
    if not db.get(models.VideoAsset, video_id):
        raise HTTPException(status_code=404, detail="视频不存在")
    try:
        vc = character_enrichment.enrich_video_character(
            db,
            video_id=video_id,
            character_id=character_id,
            profile=profile.strip() or "fast",
        )
        db.commit()
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except RuntimeError as err:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(err)) from err
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("角色富化失败")
        raise HTTPException(status_code=500, detail=f"角色富化失败：{exc}") from exc
    db.refresh(vc)
    return vc


@router.post(
    "/{video_id}/characters/{character_id}/upload-ref",
    response_model=schemas.VideoCharacterOut,
)
async def upload_character_ref_image(
    video_id: str,
    character_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> models.VideoCharacter:
    """
    上传/覆盖角色的自定义参照图。
    文件保存到 generated/characters/{character_id}/reference.jpg。
    同时将 enrichment_status 重置为 partial（下次可重新触发富化）。
    """
    vc = db.get(models.VideoCharacter, character_id)
    if not vc or vc.video_id != video_id:
        raise HTTPException(status_code=404, detail="角色不存在")
    suffix = Path(file.filename or "reference.jpg").suffix.lower()
    if suffix not in (".jpg", ".jpeg", ".png", ".webp"):
        raise HTTPException(status_code=400, detail="仅支持 jpg / png / webp")
    out_dir = settings.generated_dir / "characters" / character_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"reference{suffix}"
    await write_uploadfile_to_path(
        file,
        out_path,
        chunk_size=10 * 1024 * 1024,
        max_bytes=10 * 1024 * 1024,
        detail_over="文件不超过 10MB",
    )
    vc.reference_image_path = str(out_path)
    vc.enrichment_status = "partial"
    vc.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(vc)
    return vc


@router.post(
    "/{video_id}/characters/{character_id}/upload-sheet",
    response_model=schemas.VideoCharacterOut,
)
async def upload_character_turnaround_sheet(
    video_id: str,
    character_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> models.VideoCharacter:
    """
    上传/覆盖三视图设定图（turnaround.png），不修改镜头抽出的参照帧 reference_image_path。
    """
    vc = db.get(models.VideoCharacter, character_id)
    if not vc or vc.video_id != video_id:
        raise HTTPException(status_code=404, detail="角色不存在")
    suffix = Path(file.filename or "turnaround.png").suffix.lower()
    if suffix not in (".jpg", ".jpeg", ".png", ".webp"):
        raise HTTPException(status_code=400, detail="仅支持 jpg / png / webp")
    out_dir = settings.generated_dir / "characters" / character_id
    out_dir.mkdir(parents=True, exist_ok=True)
    # 与富化流水统一：主文件名为 turnaround，扩展名随上传
    out_path = out_dir / f"turnaround{suffix if suffix else '.png'}"
    await write_uploadfile_to_path(
        file,
        out_path,
        chunk_size=1024 * 1024,
        max_bytes=10 * 1024 * 1024,
        detail_over="文件不超过 10MB",
    )
    prev = dict(vc.three_views) if isinstance(vc.three_views, dict) else {}
    ref_in_views = prev.get("reference")
    if not ref_in_views and vc.reference_image_path:
        ref_in_views = str(vc.reference_image_path)
    vc.three_views = {
        **prev,
        "sheet": str(out_path),
        "reference": ref_in_views,
        "source": "user",
    }
    vc.enrichment_status = "visual_ready"
    vc.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(vc)
    return vc


@router.patch(
    "/{video_id}/characters/{character_id}",
    response_model=schemas.VideoCharacterOut,
)
def patch_video_character(
    video_id: str,
    character_id: str,
    body: schemas.VideoCharacterPatch,
    db: Session = Depends(get_db),
) -> models.VideoCharacter:
    """更新成片角色库（当前支持用户备注）。"""
    vc = db.get(models.VideoCharacter, character_id)
    if not vc or vc.video_id != video_id:
        raise HTTPException(status_code=404, detail="角色不存在")
    if body.user_notes is not None:
        vc.user_notes = body.user_notes
    vc.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(vc)
    return vc


@router.delete(
    "/{video_id}/characters/{character_id}",
)
def delete_character(
    video_id: str,
    character_id: str,
    db: Session = Depends(get_db),
) -> dict:
    """删除指定角色（及其 generated 目录）。"""
    vc = db.get(models.VideoCharacter, character_id)
    if not vc or vc.video_id != video_id:
        raise HTTPException(status_code=404, detail="角色不存在")
    # 清理生成文件
    char_gen_dir = settings.generated_dir / "characters" / character_id
    if char_gen_dir.exists():
        import shutil
        shutil.rmtree(char_gen_dir)
    db.delete(vc)
    db.commit()
    return {"ok": True, "character_id": character_id}


@router.delete(
    "/{video_id}/characters/{character_id}/ref-image",
)
def delete_character_ref_image(
    video_id: str,
    character_id: str,
    db: Session = Depends(get_db),
) -> dict:
    """清除角色的参照图路径（不删文件，供重新上传）。"""
    vc = db.get(models.VideoCharacter, character_id)
    if not vc or vc.video_id != video_id:
        raise HTTPException(status_code=404, detail="角色不存在")
    vc.reference_image_path = None
    vc.enrichment_status = "partial"
    vc.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "character_id": character_id}


@router.get("/{video_id}", response_model=schemas.VideoOut)
def get_video(video_id: str, db: Session = Depends(get_db)) -> models.VideoAsset:
    video = db.get(models.VideoAsset, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")
    return video
