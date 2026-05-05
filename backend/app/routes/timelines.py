from __future__ import annotations

from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import settings
from ..database import get_db
from ..services import story_state, timeline as timeline_svc

router = APIRouter(prefix="/api/timelines", tags=["timelines"])


@router.delete("/{timeline_id}")
def delete_timeline(timeline_id: str, db: Session = Depends(get_db)) -> dict:
    """删除一条分支时间线（由干预 fork 出的版本）。不得删除主线；若存在子分支须先删子分支。"""
    try:
        out = timeline_svc.delete_branch_timeline(db, timeline_id=timeline_id)
        db.commit()
        return {"ok": True, **out}
    except LookupError:
        raise HTTPException(status_code=404, detail="时间线不存在") from None
    except ValueError as e:
        msg = str(e)
        if "主线" in msg:
            raise HTTPException(status_code=400, detail=msg) from None
        if "子分支" in msg:
            raise HTTPException(status_code=409, detail=msg) from None
        raise HTTPException(status_code=400, detail=msg) from None


def _to_segment_out(seg: models.TimelineSegment) -> schemas.TimelineSegmentOut:
    return schemas.TimelineSegmentOut(
        id=seg.id,
        timeline_id=seg.timeline_id,
        index=seg.index,
        start_time=seg.start_time,
        end_time=seg.end_time,
        duration=seg.duration,
        shot_id=seg.shot_id,
        source=seg.source,
        file_path=_relativize(seg.file_path),
        audio_path=_relativize(seg.audio_path) if seg.audio_path else None,
        caption=seg.caption,
        note=seg.note,
    )


def _relativize(p: str) -> str:
    return str(p)


@router.put(
    "/{timeline_id}/branch-apply-time",
    response_model=schemas.TimelineOut,
)
def set_branch_apply_time(
    timeline_id: str,
    body: schemas.BranchApplyTimeIn,
    db: Session = Depends(get_db),
) -> models.Timeline:
    """手动调整分支叙事切入时刻并重装该分支下的片段序列（需存在对应生成任务）。"""
    try:
        tl = timeline_svc.rebuild_branch_at_apply_time(
            db,
            branch_timeline_id=timeline_id,
            apply_time=body.apply_time,
        )
        db.commit()
        db.refresh(tl)
        return tl
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@router.put(
    "/{timeline_id}/segment-order",
    response_model=schemas.TimelineOut,
)
def reorder_timeline_segment_order(
    timeline_id: str,
    body: schemas.TimelineSegmentReorderIn,
    db: Session = Depends(get_db),
) -> models.Timeline:
    """手动调整分支时间线上片段的播放顺序（拖拽排序）。"""
    try:
        tl = timeline_svc.reorder_timeline_segments(
            db,
            timeline_id=timeline_id,
            segment_ids_in_order=list(body.segment_ids),
        )
        db.commit()
        db.refresh(tl)
        return tl
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@router.delete("/{timeline_id}/segments/{segment_id}", response_model=schemas.TimelineOut)
def delete_branch_segment(
    timeline_id: str,
    segment_id: str,
    db: Session = Depends(get_db),
) -> models.Timeline:
    """从分支时间线删除一个生成/复用/兜底片段（不可删主线剪入段）。"""
    try:
        tl = timeline_svc.delete_branch_timeline_segment(
            db,
            timeline_id=timeline_id,
            segment_id=segment_id,
        )
        db.commit()
        db.refresh(tl)
        return tl
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@router.get("/{timeline_id}/manifest", response_model=schemas.TimelineManifest)
def get_manifest(timeline_id: str, db: Session = Depends(get_db)) -> schemas.TimelineManifest:
    tl = db.get(models.Timeline, timeline_id)
    if not tl:
        raise HTTPException(status_code=404, detail="时间线不存在")
    segs = timeline_svc.get_segments(db, timeline_id)
    duration = sum(s.duration for s in segs)
    return schemas.TimelineManifest(
        timeline_id=tl.id,
        version=len(segs),
        label=tl.label,
        status=tl.status,
        apply_time=tl.apply_time,
        duration=duration,
        segments=[_to_segment_out(s) for s in segs],
    )


@router.get("/{timeline_id}/segments", response_model=List[schemas.TimelineSegmentOut])
def list_segments(timeline_id: str, db: Session = Depends(get_db)) -> List[schemas.TimelineSegmentOut]:
    return [_to_segment_out(s) for s in timeline_svc.get_segments(db, timeline_id)]


@router.get("/{timeline_id}/story-state", response_model=schemas.StoryStateOut)
def get_story_state(timeline_id: str, db: Session = Depends(get_db)) -> models.StoryState:
    state = story_state.latest_state(db, timeline_id)
    if not state:
        raise HTTPException(status_code=404, detail="尚未生成剧情状态")
    return state


@router.get("/{timeline_id}", response_model=schemas.TimelineOut)
def get_timeline(timeline_id: str, db: Session = Depends(get_db)) -> models.Timeline:
    tl = db.get(models.Timeline, timeline_id)
    if not tl:
        raise HTTPException(status_code=404, detail="时间线不存在")
    return tl


@router.get("/{timeline_id}/segment/{segment_id}/file")
def stream_segment_file(timeline_id: str, segment_id: str, db: Session = Depends(get_db)):
    seg = db.get(models.TimelineSegment, segment_id)
    if not seg or seg.timeline_id != timeline_id:
        raise HTTPException(status_code=404, detail="片段不存在")
    path = Path(seg.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="片段文件不存在")
    return FileResponse(str(path), media_type="video/mp4")
