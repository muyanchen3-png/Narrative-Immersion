"""从镜头分析结果汇总每成片一条角色库（与镜头表 characters 字段对齐）。

规则补充：
- 画面内**同时出现两人及以上**（以 visible_person_count 与 characters 条数综合判断）的镜头
  不参与角色汇总，避免对话/双人对切污染单人库。
- 汇总后用 LLM 按描述相似度（约定≥50%）合并重复条目；参照帧选取优先正脸（character_facings）。
若历史数据缺少 visible_person_count / character_facings，需对镜头「重新分析」后再「从镜头汇总角色」。
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any, DefaultDict, Dict, List, Optional, Set

from sqlalchemy.orm import Session

from .. import models
from ..config import settings
from .character_enrichment import ensure_character_preview_reference
from .llm import get_llm
from .llm_context import bind_model_kind, unbind_llm
from .shot_character_utils import (
    effective_visible_person_count,
    label_from_item,
    summary_hint_for_dedup,
)

logger = logging.getLogger(__name__)


def _norm_key(label: str) -> str:
    s = " ".join(str(label).split()).strip()
    s = s.lower() if re.match(r"^[a-zA-Z0-9\s\-\.]+$", s) else s
    return s[:256] if len(s) > 256 else s


def _combine_agg_cells(canonical_display_hint: str, cells: List[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "display": None,
        "aliases": set(),
        "count": 0,
        "first_s": None,
        "last_s": None,
        "shot_ids": set(),
        "evidence": [],
    }
    seen_ev: Set[str] = set()
    for c in cells:
        out["count"] += int(c.get("count", 0))
        sid = c.get("shot_ids")
        if isinstance(sid, set):
            out["shot_ids"] |= sid
        elif sid:
            out["shot_ids"].update(sid)
        al = c.get("aliases")
        if isinstance(al, set):
            out["aliases"] |= al
        elif isinstance(al, (list, tuple)):
            out["aliases"].update(al)
        d = c.get("display")
        if d and (out["display"] is None or len(str(d)) > len(str(out["display"] or ""))):
            out["display"] = d
        fs, ls = c.get("first_s"), c.get("last_s")
        if fs is not None:
            out["first_s"] = fs if out["first_s"] is None else min(out["first_s"], fs)
        if ls is not None:
            out["last_s"] = ls if out["last_s"] is None else max(out["last_s"], ls)
        for e in c.get("evidence") or []:
            if e and e not in seen_ev and len(out["evidence"]) < 12:
                seen_ev.add(e)
                out["evidence"].append(e)
    for c in cells:
        d = c.get("display")
        if d and d != out["display"]:
            out["aliases"].add(str(d)[:256])
    if not out["display"]:
        out["display"] = canonical_display_hint[:256]
    return out


def _merge_similar_characters_llm(
    db: Session,
    *,
    video_title: str,
    agg: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    keys = list(agg.keys())
    if len(keys) <= 1:
        return agg

    max_items = 48
    sorted_keys = sorted(keys, key=lambda k: -int(agg[k].get("count", 0)))
    key_subset = sorted_keys[:max_items]
    if len(keys) > max_items:
        logger.warning(
            "角色库汇总条目过多(%s)，AI 去重仅处理提及次数最多的前 %s 条 name_key",
            len(keys),
            max_items,
        )

    items: List[Dict[str, Any]] = []
    for k in key_subset:
        cell = agg[k]
        ev_list = cell.get("evidence") or []
        blob = " || ".join(ev_list[:6])[:4500]
        items.append(
            {
                "name_key": k,
                "display_name": cell.get("display") or k,
                "evidence": blob,
                "mention_count": int(cell.get("count", 0)),
            }
        )

    user_blob = json.dumps(
        {"video_title": video_title[:200], "candidates": items},
        ensure_ascii=False,
    )

    bind_model_kind(db, settings.default_profile or "fast", "llm")
    try:
        payload = get_llm().chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "<task>character_dedup</task>"
                        "你是影视角色归档编辑。下列条目来自同一视频的镜头汇总，每条含 name_key、展示名与证据摘要。"
                        "若两条描述明显是同一人（外貌衣着场景相似度≥50%，或同一角色的不同称呼），必须并入同一簇。"
                        "不确定则不要合并。输出 JSON：clusters 为数组的数组，每个内层数组列出应合并的 name_key（字符串），"
                        "簇与簇之间互不重叠；singleton 写成单元素数组。"
                        "你必须列出本次提供的全部 name_key，且每个恰好出现一次。"
                    ),
                },
                {"role": "user", "content": user_blob},
            ],
            temperature=0.15,
            max_tokens=4096,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("角色 AI 去重调用失败，跳过合并：%s", exc)
        return agg
    finally:
        unbind_llm()

    clusters = payload.get("clusters")
    if not isinstance(clusters, list) or not clusters:
        return agg

    merged: Dict[str, Dict[str, Any]] = {}
    assigned: Set[str] = set()

    for cl in clusters:
        if not isinstance(cl, list):
            continue
        ck = [str(x) for x in cl if x in agg and x not in assigned]
        if not ck:
            continue
        if len(ck) == 1:
            k0 = ck[0]
            merged[k0] = agg[k0]
            assigned.add(k0)
            continue
        canon = max(ck, key=lambda k: int(agg[k].get("count", 0)))
        merged[canon] = _combine_agg_cells(
            str(agg[canon].get("display") or canon),
            [agg[x] for x in ck],
        )
        assigned.update(ck)

    for k in keys:
        if k not in assigned:
            merged[k] = agg[k]

    return merged


def rebuild_video_characters(
    db: Session,
    video_id: str,
    *,
    granularity: Optional[str] = None,
) -> int:
    """删除该视频旧角色行，按镜头 characters 重新聚合写入；返回角色条数。"""

    video = db.get(models.VideoAsset, video_id)
    if not video:
        raise ValueError("视频不存在")

    shots: List[models.ShotSegment] = []
    if granularity:
        shots = (
            db.query(models.ShotSegment)
            .filter(
                models.ShotSegment.video_id == video_id,
                models.ShotSegment.granularity == granularity,
            )
            .order_by(models.ShotSegment.start_time)
            .all()
        )
    else:
        for pref in ("scene", "story", "10s", "5s", "1s"):
            cand = (
                db.query(models.ShotSegment)
                .filter(
                    models.ShotSegment.video_id == video_id,
                    models.ShotSegment.granularity == pref,
                )
                .order_by(models.ShotSegment.start_time)
                .all()
            )
            if cand:
                shots = cand
                break
        if not shots:
            shots = (
                db.query(models.ShotSegment)
                .filter(models.ShotSegment.video_id == video_id)
                .order_by(models.ShotSegment.start_time)
                .all()
            )

    agg: DefaultDict[str, Dict[str, Any]] = defaultdict(
        lambda: {
            "display": None,
            "aliases": set(),
            "count": 0,
            "first_s": None,
            "last_s": None,
            "shot_ids": set(),
            "evidence": [],
        }
    )

    skipped_multi = 0
    for shot in shots:
        if effective_visible_person_count(shot) >= 2:
            skipped_multi += 1
            continue
        raw = shot.characters or []
        if not isinstance(raw, list):
            raw = [raw]
        st = float(shot.start_time)
        et = float(shot.end_time)
        hint = summary_hint_for_dedup(shot)
        for item in raw:
            lab = label_from_item(item)
            if not lab:
                continue
            key = _norm_key(lab)
            if not key:
                continue
            cell = agg[key]
            cell["count"] += 1
            if cell["display"] is None:
                cell["display"] = lab[:256]
            elif lab != cell["display"] and lab not in cell["aliases"]:
                cell["aliases"].add(lab[:256])
            if cell["first_s"] is None or st < cell["first_s"]:
                cell["first_s"] = st
            if cell["last_s"] is None or et > cell["last_s"]:
                cell["last_s"] = et
            cell["shot_ids"].add(shot.id)
            if hint:
                ev = cell["evidence"]
                if hint not in ev and len(ev) < 10:
                    ev.append(hint)

    if skipped_multi:
        logger.info(
            "角色汇总跳过同框≥2人镜头 %s 条 video=%s",
            skipped_multi,
            video_id[:12],
        )

    merged_agg = _merge_similar_characters_llm(
        db,
        video_title=video.title or "",
        agg=dict(agg),
    )

    prev_rows = (
        db.query(models.VideoCharacter)
        .filter(models.VideoCharacter.video_id == video_id)
        .all()
    )
    prev_by_key: Dict[str, models.VideoCharacter] = {p.name_key: p for p in prev_rows}

    db.query(models.VideoCharacter).filter(models.VideoCharacter.video_id == video_id).delete(
        synchronize_session=False
    )

    now = datetime.utcnow()
    n = 0
    for key, cell in sorted(
        merged_agg.items(),
        key=lambda x: (-x[1]["count"], x[1]["display"] or ""),
    ):
        display = (cell["display"] or key)[:256]
        aliases = sorted(cell["aliases"])[:64]
        shot_ids = sorted(cell["shot_ids"])[:500]
        first_s = float(cell["first_s"] if cell["first_s"] is not None else 0.0)
        last_s = float(cell["last_s"] if cell["last_s"] is not None else first_s)
        prev = prev_by_key.get(key)
        shot_set = set(shot_ids)
        overlap = (set(prev.source_shot_ids or []) & shot_set) if prev else set()
        ap: dict = {}
        tv: dict = {}
        ref_sid: Optional[str] = None
        ref_img: Optional[str] = None
        ref_vid: Optional[str] = None
        enr_st = "pending"
        row_id: str
        if prev and overlap:
            row_id = prev.id
            ap = dict(prev.agent_profile or {})
            tv = dict(prev.three_views or {})
            enr_st = (prev.enrichment_status or "pending") or "pending"
            if prev.reference_shot_id in shot_ids:
                ref_sid = prev.reference_shot_id
                ref_img = prev.reference_image_path
                ref_vid = prev.reference_video_path
            else:
                if enr_st == "visual_ready":
                    enr_st = "partial"
        else:
            row_id = str(uuid.uuid4())
        vc = models.VideoCharacter(
            id=row_id,
            video_id=video_id,
            name_key=key[:256],
            display_name=display,
            aliases=aliases,
            mention_count=int(cell["count"]),
            first_seen_s=first_s,
            last_seen_s=last_s,
            source_shot_ids=shot_ids,
            description=None,
            agent_profile=ap,
            three_views=tv,
            reference_shot_id=ref_sid,
            reference_image_path=ref_img,
            reference_video_path=ref_vid,
            enrichment_status=enr_st,
            created_at=now,
            updated_at=now,
        )
        db.add(vc)
        n += 1

    db.flush()
    for vc in (
        db.query(models.VideoCharacter)
        .filter(models.VideoCharacter.video_id == video_id)
        .all()
    ):
        ensure_character_preview_reference(db, vc)
    db.flush()
    logger.info("角色库已重建 video=%s rows=%s granularity=%s", video_id[:12], n, granularity)
    return n
