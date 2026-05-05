"""媒资库素材复用检索（向量 + 关键词的混合方案）。

Demo 阶段使用简化的关键词重叠 + 角色/地点匹配评分，避免依赖外部向量库。
真实部署时可替换为 Faiss / pgvector / Milvus 等向量库。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from sqlalchemy.orm import Session

from .. import models


@dataclass
class ReuseCandidate:
    shot: models.ShotSegment
    score: float
    reasons: List[str]


def _tokens(text: str) -> List[str]:
    if not text:
        return []
    return [t.strip() for t in text.replace("，", ",").replace("。", ",").split(",") if t.strip()]


def _score_shot(
    shot: models.ShotSegment,
    *,
    characters: List[str],
    location: Optional[str],
    actions: List[str],
    keywords: List[str],
) -> ReuseCandidate:
    reasons: List[str] = []
    score = 0.0

    if characters and shot.characters:
        overlap = len(set(characters) & set(shot.characters))
        if overlap:
            score += overlap * 1.5
            reasons.append(f"人物匹配 {overlap}")

    if location and shot.location and location in shot.location:
        score += 1.5
        reasons.append("地点匹配")

    if actions and shot.actions:
        overlap = len(set(actions) & set(shot.actions))
        if overlap:
            score += overlap
            reasons.append(f"动作匹配 {overlap}")

    if keywords:
        text = " ".join(
            [
                shot.summary or "",
                " ".join(shot.tags or []),
                shot.dialogue or "",
                shot.location or "",
                " ".join(shot.actions or []),
            ]
        )
        overlap = sum(1 for k in keywords if k and k in text)
        if overlap:
            score += overlap * 0.5
            reasons.append(f"关键词重叠 {overlap}")

    if shot.granularity in ("scene", "5s"):
        score += 0.3

    return ReuseCandidate(shot=shot, score=round(score, 3), reasons=reasons)


def find_reusable_shots(
    db: Session,
    *,
    video_id: str,
    characters: Optional[List[str]] = None,
    location: Optional[str] = None,
    actions: Optional[List[str]] = None,
    keywords: Optional[List[str]] = None,
    limit: int = 5,
    min_score: float = 0.5,
) -> List[ReuseCandidate]:
    characters = characters or []
    actions = actions or []
    keywords = keywords or []

    shots = (
        db.query(models.ShotSegment)
        .filter(models.ShotSegment.video_id == video_id)
        .all()
    )
    candidates = [
        _score_shot(s, characters=characters, location=location, actions=actions, keywords=keywords)
        for s in shots
    ]
    candidates = [c for c in candidates if c.score >= min_score]
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:limit]
