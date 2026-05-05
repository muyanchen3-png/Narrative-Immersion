"""成本估算与预算控制。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

# 单位成本（人民币元）。Mock 模式下统一按 0 计费，但保留可配置结构。
UNIT_COST: Dict[str, float] = {
    "llm_token_1k": 0.02,
    "vlm_image": 0.05,
    "image_gen": 0.4,
    "video_gen_per_sec": 1.2,
    "tts_1k_chars": 0.15,
}

# 单位时延估算（秒）
UNIT_TIME: Dict[str, float] = {
    "llm_call": 1.5,
    "image_gen": 3.0,
    "video_gen_per_sec": 4.5,
    "tts_per_line": 1.0,
    "edit_concat_per_sec": 0.05,
}


@dataclass
class CostPlan:
    items: List[Dict[str, float]] = field(default_factory=list)

    def add(self, label: str, count: float, unit_cost: float, unit_time: float) -> None:
        self.items.append(
            {
                "label": label,
                "count": count,
                "cost": count * unit_cost,
                "time": count * unit_time,
            }
        )

    @property
    def cost(self) -> float:
        return round(sum(i["cost"] for i in self.items), 4)

    @property
    def time(self) -> float:
        return round(sum(i["time"] for i in self.items), 2)


def estimate_for_plan(*, num_shots: int, total_seconds: float, num_dialogues: int, profile: str = "fast") -> CostPlan:
    plan = CostPlan()
    profile_factor = {"fast": 0.6, "quality": 1.4, "fallback": 0.2}.get(profile, 1.0)

    plan.add("LLM 编排+导演+编剧", count=4, unit_cost=UNIT_COST["llm_token_1k"], unit_time=UNIT_TIME["llm_call"])
    plan.add("分镜+提示词", count=max(1, num_shots), unit_cost=UNIT_COST["llm_token_1k"], unit_time=UNIT_TIME["llm_call"])
    plan.add(
        "关键帧生图",
        count=num_shots,
        unit_cost=UNIT_COST["image_gen"] * profile_factor,
        unit_time=UNIT_TIME["image_gen"] * profile_factor,
    )
    plan.add(
        "镜头视频生成",
        count=total_seconds,
        unit_cost=UNIT_COST["video_gen_per_sec"] * profile_factor,
        unit_time=UNIT_TIME["video_gen_per_sec"] * profile_factor,
    )
    plan.add("配音 TTS", count=max(1, num_dialogues), unit_cost=UNIT_COST["tts_1k_chars"], unit_time=UNIT_TIME["tts_per_line"])
    plan.add("剪辑拼接", count=total_seconds, unit_cost=0.0, unit_time=UNIT_TIME["edit_concat_per_sec"])
    return plan
