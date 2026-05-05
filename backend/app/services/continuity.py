"""连续性检查：人物、场景、道具、剧情、声音、剪辑六维度评分。

Demo 阶段以基于 LLM 的启发式评分为主，并结合简单的元数据对比。
真实部署时可加入人脸 embedding / 首尾帧相似度 / ASR 文本对齐等。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .. import models
from .llm import get_llm


@dataclass
class ContinuityResult:
    score: float
    issues: List[str]
    suggestions: List[str]
    breakdown: Dict[str, float]


def check_segments(
    *,
    prev: Optional[models.TimelineSegment],
    new_shots: List[dict],
    next_: Optional[models.TimelineSegment],
    story_summary: str,
) -> ContinuityResult:
    breakdown = {
        "character": 0.9,
        "scene": 0.85,
        "prop": 0.9,
        "story": 0.88,
        "audio": 0.85,
        "editing": 0.9,
    }

    issues: List[str] = []
    suggestions: List[str] = []

    # 元数据维度的初步审计
    if prev is not None and new_shots:
        first = new_shots[0]
        if prev.caption and first.get("location") and prev.caption.find(first["location"]) == -1:
            breakdown["scene"] -= 0.05
            suggestions.append("新片段地点与前一片段不同，建议增加 1 个过渡镜头说明位置变化")

    if next_ is not None and new_shots:
        last = new_shots[-1]
        if next_.caption and last.get("mood") and last["mood"] not in (next_.caption or ""):
            suggestions.append("新片段结束情绪与原片下一段差异较大，建议在合并点淡出 0.3-0.5 秒")

    # LLM 维度的再审视
    llm_result = get_llm().chat_json(
        [
            {
                "role": "system",
                "content": (
                    "<task>continuity_check</task>"
                    "你是连续性审计员。基于剧情摘要和新生成镜头的描述，评估画面、剧情、声音"
                    "在与前后片段拼接时的潜在问题。返回 JSON：{score, issues:[], suggestions:[]}。"
                ),
            },
            {
                "role": "user",
                "content": str({"story": story_summary, "shots": new_shots}),
            },
        ]
    )
    llm_score = float(llm_result.get("score", 0.86))
    issues.extend(llm_result.get("issues") or [])
    suggestions.extend(llm_result.get("suggestions") or [])

    overall = round((sum(breakdown.values()) / len(breakdown)) * 0.6 + llm_score * 0.4, 3)
    return ContinuityResult(score=overall, issues=issues, suggestions=suggestions, breakdown=breakdown)
