"""剧情状态生成与更新。"""

from __future__ import annotations

import uuid
from typing import List, Optional

from sqlalchemy.orm import Session

from .. import models
from . import timeline as timeline_svc
from .intervention_context import character_catalog_for_intervention
from .llm import get_llm


def _timeline_segment_at_virtual_time(
    segs: List[models.TimelineSegment], play_time: float
) -> Optional[models.TimelineSegment]:
    """与前端 VideoTimelinePlayer.findSegmentIndex 一致：按虚拟时间轴命中片段。"""
    if not segs:
        return None
    t = play_time
    if t <= 0:
        return segs[0]
    last = segs[-1]
    if t >= last.end_time:
        return last
    for s in segs:
        if s.start_time <= t < s.end_time:
            return s
    return segs[-1]


def shot_blurb_for_timeline_at_play_time(db: Session, *, timeline_id: str, play_time: float) -> str:
    """
    当前播放进度对应的解说上下文：先命中本条时间线的 TimelineSegment，
    再通过 shot_id 取 ShotSegment 的摘要/对白；无 shot 时用片段 caption。
    这样与屏上字幕、播放文件同源，避免仅用「原片时间」查 ShotSegment 时在分支/替换片段下串戏。
    """
    segs = timeline_svc.get_segments(db, timeline_id)
    seg = _timeline_segment_at_virtual_time(segs, play_time)
    if seg is None:
        return ""
    if seg.shot_id:
        shot = db.get(models.ShotSegment, seg.shot_id)
        if shot:
            bits = [x for x in [shot.summary, shot.dialogue, shot.location] if x]
            return " ".join(bits).strip()[:800]
    return (seg.caption or "").strip()[:800]


def generate_initial_state(db: Session, *, timeline: models.Timeline, shots: List[models.ShotSegment]) -> models.StoryState:
    summary_lines = []
    for s in shots[: min(20, len(shots))]:
        summary_lines.append(
            f"[{s.start_time:.1f}-{s.end_time:.1f}s] {s.summary or s.dialogue or '镜头片段'}"
        )

    char_cat = (character_catalog_for_intervention(db, timeline.video_id) or "").strip()
    shots_blob = "\n".join(summary_lines)
    user_blob = (
        f"【成片角色库 — 含用户备注】\n{char_cat[:6000]}\n\n---\n【镜头摘要】\n{shots_blob}"
        if char_cat
        else shots_blob
    )

    payload = get_llm().chat_json(
        [
            {
                "role": "system",
                "content": (
                    "<task>story_state</task>"
                    "你是剧情建模师。若输入含「成片角色库」，其中用户备注为创作者设定，建模人物关系与世界观时应采信；"
                    "备注中的后续伏笔（如某力量觉醒、神兵认主）应写入 open_threads、characters_state 或 causal_links 等字段，用剧情语言表述。"
                    "不要在 summary 等输出里出现「根据用户备注」类措辞。"
                    "在此基础上结合镜头摘要列表，输出当前视频整体剧情的初始状态。"
                    "返回 JSON：current_event, previous_events, causal_links, characters_state, "
                    "world_rules, location_time, open_threads, constraints, summary。"
                ),
            },
            {"role": "user", "content": user_blob[:14000]},
        ]
    )

    state = models.StoryState(
        id=str(uuid.uuid4()),
        timeline_id=timeline.id,
        time_point=0.0,
        current_event=payload.get("current_event"),
        previous_events=payload.get("previous_events") or [],
        causal_links=payload.get("causal_links") or [],
        characters_state=payload.get("characters_state") or {},
        world_rules=payload.get("world_rules") or {},
        location_time=payload.get("location_time") or {},
        open_threads=payload.get("open_threads") or [],
        constraints=payload.get("constraints") or [],
        summary=payload.get("summary"),
    )
    db.add(state)
    db.flush()
    return state


def latest_state(db: Session, timeline_id: str) -> Optional[models.StoryState]:
    return (
        db.query(models.StoryState)
        .filter(models.StoryState.timeline_id == timeline_id)
        .order_by(models.StoryState.time_point.desc(), models.StoryState.created_at.desc())
        .first()
    )


def append_state(
    db: Session,
    *,
    timeline_id: str,
    time_point: float,
    update: dict,
) -> models.StoryState:
    base = latest_state(db, timeline_id)
    state = models.StoryState(
        id=str(uuid.uuid4()),
        timeline_id=timeline_id,
        time_point=time_point,
        current_event=update.get("current_event") or (base.current_event if base else None),
        previous_events=update.get("previous_events") or (base.previous_events if base else []),
        causal_links=update.get("causal_links") or (base.causal_links if base else []),
        characters_state=update.get("characters_state") or (base.characters_state if base else {}),
        world_rules=update.get("world_rules") or (base.world_rules if base else {}),
        location_time=update.get("location_time") or (base.location_time if base else {}),
        open_threads=update.get("open_threads") or (base.open_threads if base else []),
        constraints=update.get("constraints") or (base.constraints if base else []),
        summary=update.get("summary") or (base.summary if base else None),
    )
    db.add(state)
    db.flush()
    return state


def shot_blurb_at_play_time(db: Session, *, video_id: str, play_time: float) -> str:
    """按【原片时间轴】在 ShotSegment 里查找（多粒度时优先 scene）。不适用于已与原片时间不对齐的时间线；解说请用 shot_blurb_for_timeline_at_play_time。"""

    rows = (
        db.query(models.ShotSegment)
        .filter(
            models.ShotSegment.video_id == video_id,
            models.ShotSegment.start_time <= play_time,
            models.ShotSegment.end_time > play_time,
        )
        .all()
    )
    if not rows:
        return ""
    priority = {"scene": 0, "story": 1, "10s": 2, "5s": 3, "1s": 4}
    rows.sort(key=lambda r: (priority.get(r.granularity, 99), r.index))
    r = rows[0]
    bits = [x for x in [r.summary, r.dialogue, r.location] if x]
    return " ".join(bits).strip()[:800]


def shot_analyses_text_for_intervention(
    db: Session,
    *,
    video_id: str,
    play_time: Optional[float],
    max_chars: int = 12000,
    max_lines: int = 80,
) -> str:
    """
    成片镜头分析摘录（时间顺序或围绕干预播放点），供编剧/导演/分镜与媒资检索对齐原片内容。
    无镜头记录时返回空串。
    """

    rows = (
        db.query(models.ShotSegment)
        .filter(models.ShotSegment.video_id == video_id)
        .order_by(models.ShotSegment.start_time.asc(), models.ShotSegment.index.asc())
        .all()
    )
    if not rows:
        return ""

    def one_line(r: models.ShotSegment) -> str:
        t0, t1 = r.start_time, r.end_time
        bits = [f"[{t0:.1f}-{t1:.1f}s|{r.granularity}]"]
        if r.summary:
            bits.append(r.summary)
        if r.dialogue:
            bits.append(f"对白:{str(r.dialogue)[:220]}")
        if r.location:
            bits.append(f"地点:{r.location}")
        if r.characters:
            bits.append(f"人物:{str(r.characters)[:200]}")
        return " ".join(bits)

    ordered = list(rows)
    if play_time is not None and play_time >= 0:

        def dist(r: models.ShotSegment) -> float:
            if r.start_time <= play_time < r.end_time:
                return 0.0
            if play_time < r.start_time:
                return r.start_time - play_time
            return play_time - r.end_time

        ordered = sorted(rows, key=dist)

    lines: List[str] = []
    total = 0
    for r in ordered[:max_lines]:
        ln = one_line(r)
        if total + len(ln) + 1 > max_chars:
            break
        lines.append(ln)
        total += len(ln) + 1
    return "\n".join(lines)


def state_summary_text(state: Optional[models.StoryState]) -> str:
    if state is None:
        return ""
    parts: List[str] = []
    if state.summary:
        parts.append(state.summary)
    if state.current_event:
        parts.append(f"当前事件：{state.current_event}")
    if state.location_time:
        parts.append(
            f"地点：{state.location_time.get('location')}，时间：{state.location_time.get('time')}"
        )
    if state.characters_state:
        names = ", ".join(state.characters_state.keys())
        parts.append(f"出场人物：{names}")
    return "\n".join(parts)
