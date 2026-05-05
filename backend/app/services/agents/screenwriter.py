"""编剧智能体：基于剧情状态和用户干预，生成新分支大纲、对白和因果。"""

from __future__ import annotations

from typing import Dict

from ..llm import get_llm


def write_branch(
    *,
    story_summary: str,
    intent: str,
    feasibility_level: str,
    shot_analyses: str = "",
    character_catalog: str = "",
    apply_context: str = "",
) -> Dict:
    ctx = (story_summary or "")[:4000]
    shots_blob = (shot_analyses or "").strip()[:8000]
    cat = (character_catalog or "").strip()[:8000]
    user_blob = intent
    if shots_blob:
        user_blob = (
            "【成片镜头分析摘录（按时间或与播放点相关排序），分支须与此一致、勿凭空编造人物场景】\n"
            f"{shots_blob}\n\n---\n【用户干预】\n{intent}"
        )
    if cat:
        user_blob = f"{cat}\n\n---\n{user_blob}"
    ac = (apply_context or "").strip()
    ac_block = f"\n<continuity>{ac[:1500]}</continuity>" if ac else ""
    return get_llm().chat_json(
        [
            {
                "role": "system",
                "content": (
                    "<task>screenwriter</task>"
                    f"<intent>{intent}</intent>"
                    f"<feasibility>{feasibility_level}</feasibility>"
                    "你是一名互动叙事编剧。结合剧情状态、成片镜头分析、成片角色库与用户干预，输出后续分支剧情；"
                    "主线人物称呼与外观须与角色库一致，不得与镜头分析矛盾。"
                    "角色库条目中的「用户备注」常含创作者设定的关键走向（如某件神兵、能力觉醒、命运转折）；"
                    "须自然融入 outline/dialogues/summary，视为既定世界观事实，勿在 JSON 或台词里写「根据用户备注」等元话语。"
                    "新分支必须承接干预时刻前后已发生的情节与情绪（像同一段连续故事顺接下去），"
                    "勿突兀跳切到无关时空、勿推翻上文已成立的事实；outline 每条应是可在叙事上接榫的镜头。"
                    "outline 至少 3 条、每条为可拍成单镜头的动作或场景描述。"
                    "输出 JSON: summary, outline[3-5], dialogues[{character,line}], constraints_kept[]。"
                    f"<story_state>{ctx}</story_state>"
                    f"{ac_block}"
                ),
            },
            {"role": "user", "content": user_blob},
        ]
    )
