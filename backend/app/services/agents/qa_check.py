"""质检智能体：基于连续性服务出最终是否上线决策。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from ... import models
from ..continuity import ContinuityResult, check_segments


@dataclass
class QACheckResult:
    passed: bool
    continuity: ContinuityResult
    notes: List[str]


def review(
    *,
    prev_segment: Optional[models.TimelineSegment],
    next_segment: Optional[models.TimelineSegment],
    shots: List[dict],
    story_summary: str,
    threshold: float = 0.6,
) -> QACheckResult:
    cont = check_segments(prev=prev_segment, new_shots=shots, next_=next_segment, story_summary=story_summary)
    notes: List[str] = []
    if cont.score < threshold:
        notes.append(f"连续性得分 {cont.score:.2f} 低于阈值 {threshold}，建议改用复用片段或回退")
    if not shots:
        notes.append("分镜列表为空，质检不通过")
    return QACheckResult(passed=cont.score >= threshold and bool(shots), continuity=cont, notes=notes)
