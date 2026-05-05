"""单个镜头重新跑内容理解并写回 ShotSegment，并同步关联时间线片段的 caption。"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from .. import models
from pathlib import Path

from . import analysis, asr
from .llm_context import bind_model_kind, unbind_llm

logger = logging.getLogger(__name__)


def reanalyze_shot(db: Session, shot_id: str) -> models.ShotSegment:
    shot = db.get(models.ShotSegment, shot_id)
    if not shot:
        raise ValueError("镜头不存在")
    video = db.get(models.VideoAsset, shot.video_id)
    if not video:
        raise ValueError("视频不存在")

    bind_model_kind(db, "fast", "vlm")
    try:
        analyzed = analysis.analyze_shot(
            video_title=video.title,
            index=shot.index,
            start=shot.start_time,
            end=shot.end_time,
            hint=f"granularity={shot.granularity}",
            segment_video_path=shot.file_path,
        )
        fields = analysis.normalize_analysis_for_shot(analyzed)
        try:
            asr.enrich_shot_fields_with_asr(Path(shot.file_path), fields, db=db)
        except Exception as exc:  # noqa: BLE001
            logger.warning("单镜 ASR 合并跳过 shot=%s: %s", shot_id[:8], exc)
        for key, value in fields.items():
            setattr(shot, key, value)

        for tl_seg in db.query(models.TimelineSegment).filter(models.TimelineSegment.shot_id == shot_id).all():
            tl_seg.caption = shot.summary
        db.flush()
    finally:
        unbind_llm()

    return shot


def reanalyze_all_shots_for_video(db: Session, video_id: str, *, granularity: Optional[str] = None) -> int:
    """对该成片下 ShotSegment 重新跑内容理解并写回；可选只处理某一 granularity（如 10s）。不切分视频。"""

    video = db.get(models.VideoAsset, video_id)
    if not video:
        raise ValueError("视频不存在")

    q = db.query(models.ShotSegment).filter(models.ShotSegment.video_id == video_id)
    if granularity:
        q = q.filter(models.ShotSegment.granularity == granularity)
    shots = q.order_by(models.ShotSegment.granularity, models.ShotSegment.index).all()
    if not shots:
        return 0

    bind_model_kind(db, "fast", "vlm")
    try:
        for shot in shots:
            analyzed = analysis.analyze_shot(
                video_title=video.title,
                index=shot.index,
                start=shot.start_time,
                end=shot.end_time,
                hint=f"granularity={shot.granularity}",
                segment_video_path=shot.file_path,
            )
            fields = analysis.normalize_analysis_for_shot(analyzed)
            try:
                asr.enrich_shot_fields_with_asr(Path(shot.file_path), fields, db=db)
            except Exception as exc:  # noqa: BLE001
                logger.warning("批量 ASR 合并跳过 shot=%s: %s", shot.id[:8], exc)
            for key, value in fields.items():
                setattr(shot, key, value)

            for tl_seg in db.query(models.TimelineSegment).filter(models.TimelineSegment.shot_id == shot.id).all():
                tl_seg.caption = shot.summary
            db.flush()
    finally:
        unbind_llm()

    return len(shots)
