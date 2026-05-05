"""将叙事干预生成的视频登记为 ``ShotSegment``，供媒资库「镜头」列表与按来源筛选。"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from .. import models
from .media import thumbnail as ffmpeg_thumbnail

logger = logging.getLogger(__name__)


def build_generated_clip_intro(
    shot: Dict[str, Any],
    *,
    prompts: Optional[Dict[str, Any]] = None,
    voice_text: str = "",
) -> str:
    """拼一条可读「信息简介」：分镜摘要、主体、对白/旁白、生成提示摘录。"""
    parts: List[str] = []
    su = (shot.get("summary") or "").strip()
    if su:
        parts.append(f"【分镜】{su[:520]}")
    subj = (shot.get("subject") or shot.get("action") or "").strip()
    if subj and subj not in su:
        parts.append(f"主体/动作：{subj[:220]}")
    loc = (shot.get("location") or "").strip()
    if loc:
        parts.append(f"场景：{loc[:120]}")
    vo = (shot.get("voice_over") or "").strip()
    if vo:
        parts.append(f"旁白：{vo[:320]}")
    dlg = shot.get("dialogue") or []
    if isinstance(dlg, list) and dlg:
        lines: List[str] = []
        for d in dlg[:5]:
            if not isinstance(d, dict):
                continue
            c = (d.get("character") or "").strip()
            ln = (d.get("line") or d.get("text") or "").strip()
            if ln:
                lines.append(f"{c}：{ln}" if c else ln)
        if lines:
            parts.append("对白：" + "；".join(lines)[:450])
    vp = (prompts or {}).get("video_prompt") or ""
    if vp:
        parts.append(f"生成提示摘录：{str(vp).strip()[:400]}")
    elif voice_text.strip():
        parts.append(f"配音：{voice_text.strip()[:400]}")
    out = "\n".join(parts) if parts else "叙事干预生成片段"
    return out[:8000]


def normalize_shot_plan_dict(
    sp: Dict[str, Any],
    *,
    default_location: str = "",
) -> Dict[str, Any]:
    """补全分镜/shot_plan 字段：兼容模型别名，并从摘要推断主体，避免媒资库仅显示摘要。

    典型问题：分镜 LLM 只写 summary（如「年轻男子」），subject/action/location/characters 为空。
    """
    out = dict(sp)

    alias_pairs = [
        ("subject", ("role", "main_character", "figure", "protagonist", "who")),
        ("action", ("beat", "motion", "activity", "description", "movement")),
        ("location", ("setting", "place", "scene", "where", "locale", "environment")),
        ("summary", ("caption", "narrative", "beat_summary", "overview")),
        ("voice_over", ("narration", "narrator_line", "vo")),
    ]
    for canon, alts in alias_pairs:
        cur = str(out.get(canon) or "").strip()
        if cur:
            continue
        for a in alts:
            v = out.get(a)
            if isinstance(v, str) and v.strip():
                out[canon] = v.strip()
                break

    summ = str(out.get("summary") or "").strip()
    subj = str(out.get("subject") or "").strip()
    act = str(out.get("action") or "").strip()

    # 短单行摘要常当作人物/画面标签
    if not subj and summ and len(summ) <= 64 and "\n" not in summ:
        out["subject"] = summ
        subj = summ
    if not act and summ:
        out["action"] = summ
        act = summ

    loc = str(out.get("location") or "").strip()
    if not loc and default_location.strip():
        out["location"] = default_location.strip()
        loc = default_location.strip()

    dlg_raw = out.get("dialogue")
    if isinstance(dlg_raw, str) and dlg_raw.strip():
        out["dialogue"] = [
            {"character": subj or "", "line": dlg_raw.strip()},
        ]

    chars: List[str] = []
    raw_ch = out.get("characters")
    if isinstance(raw_ch, list):
        chars = [str(x).strip() for x in raw_ch if str(x).strip()]
    dlg_list = out.get("dialogue")
    if isinstance(dlg_list, list):
        for d in dlg_list:
            if isinstance(d, dict) and d.get("character"):
                c = str(d.get("character")).strip()
                if c and c not in chars:
                    chars.append(c)
    if not chars and subj:
        chars = [subj]
    elif not chars and summ and len(summ) <= 64 and "\n" not in summ:
        chars = [summ]
    if chars:
        out["characters"] = chars

    return out


def enrich_storyboard_shot(s: Dict[str, Any], *, default_location: str = "") -> None:
    """就地补全 `shots` 数组中的单条分镜（与 `shot_plan` 键一致）。"""
    if not isinstance(s, dict):
        return
    merged = normalize_shot_plan_dict(dict(s), default_location=default_location)
    s.clear()
    s.update(merged)


def _thumbnail_for_generated_clip(video_path: str, duration: float) -> Optional[str]:
    """从生成片段 mp4 抽一帧作为封面（与 ingest 切片逻辑相近）。"""
    p = Path(video_path)
    if not p.is_file():
        return None
    dst = p.with_name(p.stem + "_thumb.jpg")
    dur = max(float(duration or 0.0), 0.1)
    tseek = max(0.05, min(dur * 0.25, dur - 0.05))
    try:
        ffmpeg_thumbnail(str(p), tseek, str(dst))
    except Exception as exc:  # noqa: BLE001
        logger.warning("生成镜头缩略图失败 path=%s: %s", video_path, exc)
        return None
    return str(dst) if dst.exists() else None


def _structured_fields_from_item(g: Dict[str, Any]) -> Dict[str, Any]:
    """从生成日志项与分镜快照填充 ShotSegment 结构化字段。"""
    voice_text = (g.get("voice_text") or "").strip()
    raw_sp = g.get("shot_plan") if isinstance(g.get("shot_plan"), dict) else {}
    sp = normalize_shot_plan_dict(dict(raw_sp), default_location="")

    summary_one = (sp.get("summary") or "").strip()
    subject = (sp.get("subject") or "").strip()
    action = (sp.get("action") or "").strip()
    location = (sp.get("location") or "").strip() or None
    voice_over = (sp.get("voice_over") or "").strip()

    chars: List[str] = []
    raw_ch = sp.get("characters")
    if isinstance(raw_ch, list):
        chars = [str(x).strip() for x in raw_ch if str(x).strip()]
    dlg_raw = sp.get("dialogue")
    if isinstance(dlg_raw, list):
        for d in dlg_raw:
            if isinstance(d, dict) and d.get("character"):
                c = str(d.get("character")).strip()
                if c and c not in chars:
                    chars.append(c)

    dialogue_text: Optional[str] = None
    if voice_text:
        dialogue_text = voice_text[:8000]
    elif isinstance(dlg_raw, list):
        lines: List[str] = []
        for d in dlg_raw[:12]:
            if not isinstance(d, dict):
                continue
            c = (d.get("character") or "").strip()
            ln = (d.get("line") or d.get("text") or "").strip()
            if ln:
                lines.append(f"{c}：{ln}" if c else ln)
        if lines:
            dialogue_text = "\n".join(lines)[:8000]
    if voice_over and not dialogue_text:
        dialogue_text = voice_over[:8000]

    actions: List[str] = []
    if subject:
        actions.append(subject)
    if action and action != subject:
        actions.append(action)
    if not actions and summary_one:
        actions.append(summary_one[:500])

    caption = (g.get("brief") or g.get("caption") or "").strip() or "叙事干预生成片段"
    summary_out = summary_one or caption
    if len(summary_out) > 8000:
        summary_out = summary_out[:8000]

    return {
        "summary": summary_out,
        "characters": chars,
        "location": location,
        "actions": actions,
        "dialogue": dialogue_text,
        "emotion": None,
        "objects": [],
    }


def record_generated_clips_as_shots(
    db: Session,
    *,
    video_id: str,
    job_id: str,
    items: List[Dict[str, Any]],
) -> int:
    """每条生成结果写入一条镜头记录（若同路径已存在则尝试补缩略图与结构化字段）。"""
    n = 0
    for i, g in enumerate(items):
        fp = (g.get("file_path") or "").strip()
        if not fp:
            continue
        dur = float(g.get("duration") or 0.0)
        if dur <= 0:
            dur = 5.0
        fb = bool(g.get("fallback"))
        vm = (g.get("video_model") or "").strip()
        structured = _structured_fields_from_item(g)
        thumb_path = _thumbnail_for_generated_clip(fp, dur)

        dup = (
            db.query(models.ShotSegment)
            .filter(models.ShotSegment.file_path == fp)
            .first()
        )
        if dup is not None:
            updated = False
            if not dup.thumbnail_path and thumb_path:
                dup.thumbnail_path = thumb_path
                updated = True
            # 旧数据补全结构化字段（仅空字段）
            if structured.get("summary") and (not (dup.summary or "").strip()):
                dup.summary = structured["summary"]
                updated = True
            if structured.get("characters") and not (dup.characters or []):
                dup.characters = structured["characters"]
                updated = True
            if structured.get("location") and not dup.location:
                dup.location = structured["location"]
                updated = True
            if structured.get("actions") and not (dup.actions or []):
                dup.actions = structured["actions"]
                updated = True
            if structured.get("dialogue") and not (dup.dialogue or "").strip():
                dup.dialogue = structured["dialogue"]
                updated = True
            if updated:
                n += 1
            continue

        seg = models.ShotSegment(
            id=str(uuid.uuid4()),
            video_id=video_id,
            granularity="generated",
            index=i,
            start_time=0.0,
            end_time=dur,
            duration=dur,
            file_path=fp,
            thumbnail_path=thumb_path,
            audio_path=g.get("audio_path") or None,
            summary=structured["summary"],
            characters=structured["characters"],
            location=structured["location"],
            actions=structured["actions"],
            dialogue=structured["dialogue"],
            emotion=structured["emotion"],
            objects=structured["objects"],
            visual_style={},
            continuity_anchors={"generation_job_id": job_id, "video_model": vm},
            safety_labels=[],
            tags=["intervention", "generated"],
            source="fallback" if fb else "generated",
            quality_score=0.75 if fb else 0.9,
        )
        db.add(seg)
        n += 1
    if n:
        db.flush()
    return n


def backfill_missing_thumbnails(
    db: Session,
    *,
    video_id: Optional[str] = None,
) -> int:
    """为已登记但无封面的「生成/干预」镜头补抽缩略图（需本机文件仍存在）。"""

    q = (
        db.query(models.ShotSegment)
        .filter(
            models.ShotSegment.granularity == "generated",
            models.ShotSegment.thumbnail_path.is_(None),
        )
    )
    if video_id:
        q = q.filter(models.ShotSegment.video_id == video_id)
    n = 0
    for seg in q.all():
        tp = _thumbnail_for_generated_clip(seg.file_path, float(seg.duration or 0.0))
        if tp:
            seg.thumbnail_path = tp
            n += 1
    if n:
        db.flush()
    return n


def backfill_structured_fields_from_jobs(
    db: Session,
    *,
    video_id: str,
) -> int:
    """按最新生成任务里的 ``generated_segments`` 回填镜头结构化字段（摘要/人物/对白等）。

    旧任务若尚无 ``shot_plan``，至少可写入 ``voice_text`` 对应的对白。
    """

    jobs = (
        db.query(models.GenerationJob)
        .join(models.Intervention, models.GenerationJob.intervention_id == models.Intervention.id)
        .join(models.Timeline, models.Intervention.timeline_id == models.Timeline.id)
        .filter(models.Timeline.video_id == video_id)
        .order_by(models.GenerationJob.created_at.desc())
        .all()
    )
    by_fp: Dict[str, Dict[str, Any]] = {}
    for job in jobs:
        for g in job.generated_segments or []:
            fp = (g.get("file_path") or "").strip()
            if fp and fp not in by_fp:
                by_fp[fp] = g if isinstance(g, dict) else {}

    n = 0
    for fp, g in by_fp.items():
        seg = (
            db.query(models.ShotSegment)
            .filter(
                models.ShotSegment.video_id == video_id,
                models.ShotSegment.file_path == fp,
            )
            .first()
        )
        if seg is None:
            continue
        structured = _structured_fields_from_item(g)
        updated = False
        if structured.get("summary") and not (seg.summary or "").strip():
            seg.summary = structured["summary"]
            updated = True
        if structured.get("characters") and not (seg.characters or []):
            seg.characters = structured["characters"]
            updated = True
        if structured.get("location") and not seg.location:
            seg.location = structured["location"]
            updated = True
        if structured.get("actions") and not (seg.actions or []):
            seg.actions = structured["actions"]
            updated = True
        if structured.get("dialogue") and not (seg.dialogue or "").strip():
            seg.dialogue = structured["dialogue"]
            updated = True
        if updated:
            n += 1
    if n:
        db.flush()
    return n
