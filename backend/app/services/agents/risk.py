"""风险智能体：评估生成耗时、失败概率、成本和替换点。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from ..cost import CostPlan, estimate_for_plan


@dataclass
class RiskAssessment:
    estimated_seconds: float
    estimated_cost: float
    suggested_apply_offset: float
    suggested_buffer: float
    risk_notes: List[str]
    cost_plan: CostPlan


def assess(*, shots: list, profile: str = "fast") -> RiskAssessment:
    num_shots = len(shots)
    total_seconds = sum(s.get("duration", 5.0) for s in shots)
    num_dialogues = sum(len(s.get("dialogue") or []) + (1 if s.get("voice_over") else 0) for s in shots)

    plan = estimate_for_plan(
        num_shots=num_shots,
        total_seconds=total_seconds,
        num_dialogues=num_dialogues,
        profile=profile,
    )

    notes: List[str] = []
    if num_shots == 0:
        notes.append("分镜为空，建议改为轻量影响而非局部分支")
    if total_seconds > 60:
        notes.append("分镜总时长 > 60s，建议缩短或拆分为多次干预")
    if profile == "fallback":
        notes.append("处于兜底模式，将使用关键帧/旁白/字幕代替部分视频生成")

    # 替换点偏移：至少留 8s 缓冲，其余按预估生成耗时 + buffer（不再对 fast 档做 20s 上限压缩）。
    suggested_buffer = 5.0
    suggested_apply_offset = max(8.0, plan.time + suggested_buffer)

    return RiskAssessment(
        estimated_seconds=plan.time,
        estimated_cost=plan.cost,
        suggested_apply_offset=suggested_apply_offset,
        suggested_buffer=suggested_buffer,
        risk_notes=notes,
        cost_plan=plan,
    )
