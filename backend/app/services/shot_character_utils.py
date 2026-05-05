"""镜头与角色条目的公共解析：人数判断、正脸排序，供角色库与富化抽帧共用。"""

from __future__ import annotations

import json
from typing import Any, List, Optional

from .. import models


def label_from_item(item: Any) -> Optional[str]:
    if item is None:
        return None
    if isinstance(item, str):
        t = item.strip()
        return t or None
    if isinstance(item, dict):
        for k in ("name", "label", "character", "role", "title"):
            v = item.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()[:256]
        try:
            return json.dumps(item, ensure_ascii=False)[:256]
        except (TypeError, ValueError):
            return str(item)[:256]
    return str(item).strip()[:256] or None


def effective_visible_person_count(shot: models.ShotSegment) -> int:
    """
    画面内可辨人物/主体数量：优先「分析模型给出的 visible_person_count」与
    characters 非空条数取较大值，避免漏计同框未写全的情况。
    """
    ca = shot.continuity_anchors or {}
    if not isinstance(ca, dict):
        ca = {}
    vpc = ca.get("visible_person_count")
    raw = shot.characters or []
    if not isinstance(raw, list):
        raw = [raw]
    n_labels = sum(1 for x in raw if label_from_item(x))
    if isinstance(vpc, (int, float)) and int(vpc) >= 0:
        return max(int(vpc), n_labels)
    return n_labels


def _match_character_index(
    characters: list, display_name: str, name_aliases: List[str]
) -> Optional[int]:
    """返回与 display/aliases 最匹配 characters 列表下标，用于对齐 character_facings。"""
    names = [display_name] + [a for a in name_aliases if a and a != display_name]
    raw = characters or []
    if not isinstance(raw, list):
        raw = [raw]
    for i, item in enumerate(raw):
        lab = label_from_item(item)
        if not lab:
            continue
        for n in names:
            if not n:
                continue
            if n == lab:
                return i
            if len(n) >= 2 and (n in lab or lab in n):
                return i
    return None


def facing_rank_for_character(
    shot: models.ShotSegment, display_name: str, name_aliases: List[str]
) -> int:
    """
    数值越小越优先作为参照帧：0=正脸/slightly front，1=侧面，2=未知，3=背面。
    """
    ca = shot.continuity_anchors or {}
    if not isinstance(ca, dict):
        ca = {}
    facings = ca.get("character_facings") or []
    raw = shot.characters or []
    if not isinstance(raw, list):
        raw = [raw]
    if not isinstance(facings, list):
        facings = []
    idx = _match_character_index(raw, display_name, name_aliases)
    if idx is None or idx >= len(facings):
        return 2
    fv = str(facings[idx]).lower().strip()
    if fv in ("front", "正脸", "正面", "fwd"):
        return 0
    if fv in ("side", "侧脸", "侧面", "profile"):
        return 1
    if fv in ("back", "背", "背面", "rear"):
        return 3
    if fv in ("three_quarter", "3/4", "四分之三"):
        return 1
    return 2


def summary_hint_for_dedup(shot: models.ShotSegment) -> str:
    """用于角色去重判定的短描述（不依赖重新分析）。"""
    parts: List[str] = []
    if shot.summary:
        parts.append(str(shot.summary)[:400])
    if shot.location:
        parts.append(f"地点:{shot.location[:80]}")
    if shot.dialogue:
        parts.append(f"对白:{str(shot.dialogue)[:120]}")
    if shot.visual_style and isinstance(shot.visual_style, dict):
        parts.append(str(shot.visual_style)[:200])
    return " ".join(parts).strip()[:800]
