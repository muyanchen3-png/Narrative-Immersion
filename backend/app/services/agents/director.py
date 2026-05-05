"""导演智能体：决定哪些原片保留、哪些片段替换、哪里需要桥接。"""

from __future__ import annotations

from typing import Dict

from ..llm import get_llm


def plan_shots(
    *,
    story_summary: str,
    branch_outline: list,
    intent: str,
    shot_analyses: str = "",
    character_catalog: str = "",
) -> Dict:
    payload = {
        "intent": intent,
        "story_summary": (story_summary or "")[:4000],
        "branch_outline": branch_outline,
    }
    sa = (shot_analyses or "").strip()
    if sa:
        payload["shot_analyses_excerpt"] = sa[:6000]
    cc = (character_catalog or "").strip()
    if cc:
        payload["character_library"] = cc[:8000]
    return get_llm().chat_json(
        [
            {
                "role": "system",
                "content": (
                    "<task>director</task>"
                    "你是一名导演。结合剧情状态、成片镜头分析、成片角色库与编剧分支大纲，决定替换策略。"
                    "角色库中的用户备注含创作者设定的情节走向，规划镜头时须与之兼容，勿在输出中写「根据备注」类元话语。"
                    "shots_plan 必须为非空数组：每项含 role（与角色库称呼一致）, duration（秒）, summary（镜头叙事）, location（可选）。"
                    "镜头条数须与 branch_outline 规模匹配（通常 3～6 条），不得返回空数组。"
                    "输出 JSON：decision, reuse_strategy, bridge_strategy, shots_plan。"
                ),
            },
            {"role": "user", "content": str(payload)},
        ]
    )
