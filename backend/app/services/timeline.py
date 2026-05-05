"""时间线版本与补丁操作。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from .. import models


@dataclass
class SegmentSpec:
    file_path: str
    duration: float
    source: str = "origin"
    shot_id: Optional[str] = None
    audio_path: Optional[str] = None
    caption: Optional[str] = None
    note: Optional[str] = None


def create_main_timeline(
    db: Session,
    *,
    video: models.VideoAsset,
    shots: List[models.ShotSegment],
) -> models.Timeline:
    timeline = models.Timeline(
        id=str(uuid.uuid4()),
        video_id=video.id,
        parent_id=None,
        label="主线",
        status="ready",
        created_by="system",
    )
    db.add(timeline)
    db.flush()

    cursor = 0.0
    for i, shot in enumerate(shots):
        seg = models.TimelineSegment(
            id=str(uuid.uuid4()),
            timeline_id=timeline.id,
            index=i,
            start_time=cursor,
            end_time=cursor + shot.duration,
            duration=shot.duration,
            shot_id=shot.id,
            source="origin",
            file_path=shot.file_path,
            audio_path=shot.audio_path,
            caption=shot.summary,
            note="原片镜头",
        )
        cursor += shot.duration
        db.add(seg)
    db.flush()
    return timeline


def get_segments(db: Session, timeline_id: str) -> List[models.TimelineSegment]:
    return (
        db.query(models.TimelineSegment)
        .filter(models.TimelineSegment.timeline_id == timeline_id)
        .order_by(models.TimelineSegment.index)
        .all()
    )


def get_timeline(db: Session, timeline_id: str) -> Optional[models.Timeline]:
    return db.get(models.Timeline, timeline_id)


def total_duration(db: Session, timeline_id: str) -> float:
    segs = get_segments(db, timeline_id)
    return sum(s.duration for s in segs)


def reorder_timeline_segments(
    db: Session,
    *,
    timeline_id: str,
    segment_ids_in_order: List[str],
) -> models.Timeline:
    """仅重排分支时间线上的片段顺序，保持各段 duration 与媒体路径不变，重写 index 与连续时间轴。"""

    tl = db.get(models.Timeline, timeline_id)
    if tl is None:
        raise LookupError("时间线不存在")
    if not tl.parent_id:
        raise ValueError("仅分支时间线可重排片段顺序")
    segs = get_segments(db, timeline_id)
    id_set = {s.id for s in segs}
    ordered = list(segment_ids_in_order)
    if set(ordered) != id_set or len(ordered) != len(id_set):
        raise ValueError("segment_ids 须包含当前时间线下的全部片段 id，且不得多余或缺失")
    id_to_seg = {s.id: s for s in segs}
    cursor = 0.0
    for i, sid in enumerate(ordered):
        s = id_to_seg[sid]
        s.index = i
        s.start_time = cursor
        s.end_time = cursor + s.duration
        cursor = s.end_time
    db.flush()
    out = db.get(models.Timeline, timeline_id)
    return out if out is not None else tl


def _repack_segment_times(db: Session, timeline_id: str) -> None:
    segs = get_segments(db, timeline_id)
    cursor = 0.0
    for i, s in enumerate(segs):
        s.index = i
        s.start_time = cursor
        s.end_time = cursor + s.duration
        cursor += s.duration
    db.flush()


def _mid_block_duration(segs: List[models.TimelineSegment]) -> float:
    return sum(s.duration for s in segs if s.source in ("generated", "fallback", "reused"))


def _same_media_path(a: str, b: str) -> bool:
    a = (a or "").replace("\\", "/")
    b = (b or "").replace("\\", "/")
    if not a or not b:
        return False
    if a == b:
        return True
    return Path(a).name == Path(b).name


def delete_branch_timeline_segment(
    db: Session,
    *,
    timeline_id: str,
    segment_id: str,
) -> models.Timeline:
    """从分支时间线移除一个「插入块」片段（生成/复用/兜底），重排时间码并同步 Job 与 Patch。

    不允许删除来自主线剪入的 origin 段；重装切入请用 branch-apply-time。
    """

    tl = db.get(models.Timeline, timeline_id)
    if tl is None:
        raise LookupError("时间线不存在")
    if not tl.parent_id:
        raise ValueError("仅分支时间线可删除片段")
    seg = db.get(models.TimelineSegment, segment_id)
    if seg is None or seg.timeline_id != timeline_id:
        raise LookupError("片段不存在")
    if seg.source not in ("generated", "fallback", "reused"):
        raise ValueError("只能删除生成、复用或兜底片段；主线剪入段请调整切入时刻")

    fp = str(seg.file_path or "")
    db.delete(seg)
    db.flush()
    _repack_segment_times(db, timeline_id)

    job = (
        db.query(models.GenerationJob)
        .filter(models.GenerationJob.new_timeline_id == timeline_id)
        .order_by(models.GenerationJob.created_at.desc())
        .first()
    )
    if job:
        gs = list(job.generated_segments or [])
        rs = list(job.reuse_segments or [])
        gs2 = [g for g in gs if not _same_media_path(fp, str(g.get("file_path") or ""))]
        rs2 = [r for r in rs if not _same_media_path(fp, str(r.get("file_path") or ""))]
        if len(gs2) != len(gs):
            job.generated_segments = gs2
        if len(rs2) != len(rs):
            job.reuse_segments = rs2

    tl_ref = db.get(models.Timeline, timeline_id)
    at = float(tl_ref.apply_time or 0.0) if tl_ref else 0.0
    segs_after = get_segments(db, timeline_id)
    mid_dur = _mid_block_duration(segs_after)
    patch = (
        db.query(models.TimelinePatch)
        .filter(models.TimelinePatch.to_timeline_id == timeline_id)
        .order_by(models.TimelinePatch.id)
        .first()
    )
    if patch is not None:
        patch.replace_end_time = at + mid_dur

    db.flush()
    out = db.get(models.Timeline, timeline_id)
    return out if out is not None else tl


def _populate_fork_segments(
    db: Session,
    *,
    timeline_id: str,
    base_timeline_id: str,
    apply_time: float,
    new_segments: Iterable[SegmentSpec],
) -> None:
    """将主线 ``base_timeline_id`` 在 ``apply_time`` 处剪接为：前缀 + ``new_segments`` + 主线尾段，写入 ``timeline_id``。"""

    base_segs = get_segments(db, base_timeline_id)
    cursor = 0.0
    next_index = 0
    n = len(base_segs)
    i = 0

    def _append_from_base(seg: models.TimelineSegment) -> None:
        nonlocal cursor, next_index
        new_seg = models.TimelineSegment(
            id=str(uuid.uuid4()),
            timeline_id=timeline_id,
            index=next_index,
            start_time=cursor,
            end_time=cursor + seg.duration,
            duration=seg.duration,
            shot_id=seg.shot_id,
            source=seg.source,
            file_path=seg.file_path,
            audio_path=seg.audio_path,
            caption=seg.caption,
            note=seg.note,
        )
        cursor += seg.duration
        next_index += 1
        db.add(new_seg)

    # 1) 原片时间轴上「整段结束时间不晚于分叉点」的片段
    while i < n and base_segs[i].end_time <= apply_time:
        _append_from_base(base_segs[i])
        i += 1

    # 2) 分叉点落在一镜内部：Demo 下仍整段引用该镜文件（未做画面内 trim，与历史行为一致）
    if i < n:
        s0 = base_segs[i]
        if s0.start_time < apply_time < s0.end_time:
            _append_from_base(s0)
            i += 1

    # 3) 生成分支（复用 + API 出片等）
    for spec in new_segments:
        seg = models.TimelineSegment(
            id=str(uuid.uuid4()),
            timeline_id=timeline_id,
            index=next_index,
            start_time=cursor,
            end_time=cursor + spec.duration,
            duration=spec.duration,
            shot_id=spec.shot_id,
            source=spec.source,
            file_path=spec.file_path,
            audio_path=spec.audio_path,
            caption=spec.caption,
            note=spec.note,
        )
        cursor += spec.duration
        next_index += 1
        db.add(seg)

    # 4) 接回主线尾段：apply_time 之后尚未编入的片段（旧逻辑在此处丢失）
    while i < n:
        _append_from_base(base_segs[i])
        i += 1


def rebuild_branch_fork(
    db: Session,
    *,
    branch_timeline_id: str,
    apply_time: float,
    new_segments: Iterable[SegmentSpec],
) -> models.Timeline:
    """对已有分支时间线按新的 ``apply_time`` 与中间插入段重新编排（删除旧 segment 后重装）。"""

    tl = db.get(models.Timeline, branch_timeline_id)
    if tl is None:
        raise LookupError("时间线不存在")
    if not tl.parent_id:
        raise ValueError("仅可对分支时间线重新编排")

    tl.apply_time = apply_time
    db.query(models.TimelineSegment).filter(models.TimelineSegment.timeline_id == branch_timeline_id).delete(
        synchronize_session=False
    )
    _populate_fork_segments(
        db,
        timeline_id=branch_timeline_id,
        base_timeline_id=tl.parent_id,
        apply_time=apply_time,
        new_segments=new_segments,
    )
    db.flush()
    return tl


def _inject_specs_from_branch_segments(
    specs: List[SegmentSpec],
    branch_segs: List[models.TimelineSegment],
) -> List[SegmentSpec]:
    """用当前分支上已落库的片段路径/时长覆盖 specs（保留二次合成路径等）。"""
    inject = [s for s in branch_segs if s.source in ("generated", "fallback", "reused")]
    if len(inject) != len(specs):
        inj2 = [s for s in branch_segs if "shot_outline" in (s.file_path or "")]
        if len(inj2) == len(specs):
            inject = inj2
    if len(inject) != len(specs):
        return specs
    out: List[SegmentSpec] = []
    for spec, seg in zip(specs, inject):
        out.append(
            replace(
                spec,
                file_path=seg.file_path,
                duration=seg.duration,
                audio_path=seg.audio_path or spec.audio_path,
            )
        )
    return out


def rebuild_branch_at_apply_time(
    db: Session,
    *,
    branch_timeline_id: str,
    apply_time: float,
) -> models.Timeline:
    """根据存储的生成任务重装分支切入时刻（用于手动编排）。需存在 ``GenerationJob.new_timeline_id`` 指向该分支。"""

    from .agents import editor

    tl = db.get(models.Timeline, branch_timeline_id)
    if tl is None:
        raise LookupError("时间线不存在")
    if not tl.parent_id:
        raise ValueError("仅分支时间线可调整切入时刻")

    job = (
        db.query(models.GenerationJob)
        .filter(models.GenerationJob.new_timeline_id == branch_timeline_id)
        .order_by(models.GenerationJob.created_at.desc())
        .first()
    )
    if job is None:
        raise LookupError("未找到 fork 该分支的生成任务，无法重装片段结构")

    video = db.get(models.VideoAsset, tl.video_id)
    dur = float(video.duration or 0.0) if video else 0.0
    at = float(apply_time)
    if dur > 0:
        at = max(0.0, min(at, dur - 0.5))

    branch_segs_before = get_segments(db, branch_timeline_id)
    specs = editor.assemble_specs(
        generated_shots=list(job.generated_segments or []),
        reused_shots=list(job.reuse_segments or []),
    )
    specs = _inject_specs_from_branch_segments(specs, branch_segs_before)

    rebuild_branch_fork(db, branch_timeline_id=branch_timeline_id, apply_time=at, new_segments=specs)

    replace_dur = sum(s.duration for s in specs)
    inv = db.get(models.Intervention, job.intervention_id)
    if inv is not None:
        inv.apply_time = at

    patch = (
        db.query(models.TimelinePatch)
        .filter(models.TimelinePatch.to_timeline_id == branch_timeline_id)
        .order_by(models.TimelinePatch.id)
        .first()
    )
    if patch is not None:
        patch.replace_start_time = at
        patch.replace_end_time = at + replace_dur

    db.flush()
    return db.get(models.Timeline, branch_timeline_id) or tl


def fork_timeline(
    db: Session,
    *,
    base: models.Timeline,
    label: str,
    branch_reason: str,
    apply_time: float,
    new_segments: Iterable[SegmentSpec],
) -> models.Timeline:
    """在 ``apply_time`` 处 fork 新时间线：先播原片到分叉点，再播生成分支，**再接回**主线上尚未播完的原片尾段。

    旧实现遇「整段在分叉点之后」的片段时 ``break``，会丢掉原片结局，导致分支只含「前缀+生成」、叙事断裂。
    """

    new_timeline = models.Timeline(
        id=str(uuid.uuid4()),
        video_id=base.video_id,
        parent_id=base.id,
        label=label,
        status="ready",
        branch_reason=branch_reason,
        apply_time=apply_time,
        created_by="user",
    )
    db.add(new_timeline)
    db.flush()

    _populate_fork_segments(
        db,
        timeline_id=new_timeline.id,
        base_timeline_id=base.id,
        apply_time=apply_time,
        new_segments=new_segments,
    )

    db.flush()
    return new_timeline


def append_patch(
    db: Session,
    *,
    intervention_id: str,
    from_timeline_id: str,
    to_timeline_id: str,
    replace_start: float,
    replace_end: float,
    transition_note: str,
    continuity_score: float,
    safety_score: float,
    quality_score: float,
) -> models.TimelinePatch:
    patch = models.TimelinePatch(
        id=str(uuid.uuid4()),
        intervention_id=intervention_id,
        from_timeline_id=from_timeline_id,
        to_timeline_id=to_timeline_id,
        replace_start_time=replace_start,
        replace_end_time=replace_end,
        transition_note=transition_note,
        continuity_score=continuity_score,
        safety_score=safety_score,
        quality_score=quality_score,
    )
    db.add(patch)
    db.flush()
    return patch


def delete_branch_timeline(db: Session, *, timeline_id: str) -> dict:
    """删除一条**分支**时间线（须有 ``parent_id``）；不得删除主线。

    会清理：关联的生成任务与产物、对话、剧情状态、干预与补丁等；``timeline_segments`` 随时间线级联删除。
    """
    tl = db.get(models.Timeline, timeline_id)
    if not tl:
        raise LookupError("时间线不存在")
    if tl.parent_id is None:
        raise ValueError("不能删除主线时间线")
    n_children = (
        db.query(models.Timeline)
        .filter(models.Timeline.parent_id == timeline_id)
        .count()
    )
    if n_children > 0:
        raise ValueError("该分支下还有子分支，请先删除子分支")

    video_id = tl.video_id
    deleted_id = tl.id

    iv_ids = [
        r[0]
        for r in db.query(models.Intervention.id)
        .filter(models.Intervention.timeline_id == timeline_id)
        .all()
    ]

    job_parts = [
        models.GenerationJob.timeline_id == timeline_id,
        models.GenerationJob.new_timeline_id == timeline_id,
    ]
    if iv_ids:
        job_parts.append(models.GenerationJob.intervention_id.in_(iv_ids))
    job_filter = or_(*job_parts)

    job_ids = [r[0] for r in db.query(models.GenerationJob.id).filter(job_filter).all()]
    if job_ids:
        db.query(models.GeneratedAsset).filter(models.GeneratedAsset.job_id.in_(job_ids)).delete(
            synchronize_session=False
        )
        db.query(models.GenerationJob).filter(models.GenerationJob.id.in_(job_ids)).delete(
            synchronize_session=False
        )

    if iv_ids:
        db.query(models.SafetyLog).filter(models.SafetyLog.intervention_id.in_(iv_ids)).delete(
            synchronize_session=False
        )

    patch_parts = [
        models.TimelinePatch.from_timeline_id == timeline_id,
        models.TimelinePatch.to_timeline_id == timeline_id,
    ]
    if iv_ids:
        patch_parts.append(models.TimelinePatch.intervention_id.in_(iv_ids))
    db.query(models.TimelinePatch).filter(or_(*patch_parts)).delete(synchronize_session=False)

    db.query(models.ChatMessage).filter(models.ChatMessage.timeline_id == timeline_id).delete(
        synchronize_session=False
    )
    db.query(models.StoryState).filter(models.StoryState.timeline_id == timeline_id).delete(
        synchronize_session=False
    )
    if iv_ids:
        db.query(models.Intervention).filter(models.Intervention.id.in_(iv_ids)).delete(
            synchronize_session=False
        )

    db.delete(tl)
    db.flush()
    return {"deleted_id": deleted_id, "video_id": video_id}
