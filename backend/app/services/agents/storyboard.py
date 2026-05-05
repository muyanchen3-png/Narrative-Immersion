"""分镜智能体：把剧情拆成可生成的 3-6 秒镜头序列。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from ..llm import get_llm

logger = logging.getLogger(__name__)


def _normalize_shots(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """兼容模型把数组放在嵌套字段或非标准键名的情况。"""

    raw = data.get("shots")
    if raw is None and isinstance(data.get("data"), dict):
        raw = data["data"].get("shots")
    if raw is None and isinstance(data.get("result"), dict):
        raw = data["result"].get("shots")
    if raw is None:
        raw = data.get("shot_list") or data.get("segments")
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(item)
    return out


def make_storyboard(
    *,
    plan: dict,
    dialogues: list,
    shot_analyses: str = "",
    character_catalog: str = "",
) -> Dict:
    plan_s = str(plan)[:3500]
    user_parts: Dict = {"dialogues": dialogues}
    sa = (shot_analyses or "").strip()
    if sa:
        user_parts["shot_analyses_excerpt"] = sa[:6000]
    cc = (character_catalog or "").strip()
    if cc:
        user_parts["character_library"] = cc[:8000]
    data = get_llm().chat_json(
        [
            {
                "role": "system",
                "content": (
                    "<task>storyboard</task>"
                    f"<director_plan>{plan_s}</director_plan>"
                    "你是一名分镜师。将导演计划细化为可生成、可检索复用的镜头序列；须与成片镜头分析与角色库外观一致。"
                    "必须返回非空 JSON：顶层键 **shots** 为非空数组；subject 与 dialogue.character 须使用角色库中的称呼。"
                    "每个镜头包含：id, duration, shot_type, camera, subject, action, location, "
                    "lighting, mood, voice_over, dialogue[{character,line}], summary。"
                    "只输出 JSON 对象，勿输出思考过程标签。"
                    "返回 JSON: {shots:[...]}。"
                ),
            },
            {"role": "user", "content": str(user_parts)},
        ],
        max_tokens=4096,
        temperature=0.35,
    )
    shots = _normalize_shots(data)
    if not shots:
        logger.warning(
            "分镜 LLM 未得到可用 shots：payload_keys=%s",
            list(data.keys()) if isinstance(data, dict) else type(data),
        )
    return {**data, "shots": shots}
