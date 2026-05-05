"""提示词智能体：把分镜转成生图/生视频/配音可用的 prompt。"""

from __future__ import annotations

from typing import Dict

from ..llm import get_llm


def shot_to_prompts(
    *,
    shot: dict,
    character_catalog: str = "",
    scene_context: str = "",
    video_title: str = "",
) -> Dict:
    cc = (character_catalog or "").strip()
    cc_block = f"<character_library>{cc[:3800]}</character_library>" if cc else ""
    sc = (scene_context or "").strip()
    sc_block = f"<scene_context>{sc[:4500]}</scene_context>" if sc else ""
    vt = (video_title or "").strip()[:300]
    vt_block = f"<piece_title>{vt}</piece_title>" if vt else ""

    user_parts = [
        shot.get("summary") or shot.get("action") or "镜头",
        "",
        "【须遵守的场景约束】",
        sc[:3500] if sc else "（无额外场景块；仅从分镜字段推断。）",
    ]
    user_blob = "\n".join(user_parts).strip()[:6000]

    return get_llm().chat_json(
        [
            {
                "role": "system",
                "content": (
                    "<task>prompt</task>"
                    + vt_block
                    + f"<shot>{str(shot)[:1100]}</shot>"
                    + sc_block
                    + cc_block
                    + "你是影视提示词工程师。必须在 video_prompt / image_prompt 中写清："
                    "场景环境（室内/室外、具体空间）、光线与色调、时代氛围；"
                    "角色动作与镜头运动；并与 scene_context、角色库中的地点/外观一致，禁止冲突。"
                    "成片生成会以角色库参照图为锚点：对白/主体须指名角色库中的角色名，外观勿与参照矛盾。"
                    "negative_prompt 列出会破坏画风一致性的元素。"
                    "返回 JSON：image_prompt, video_prompt, negative_prompt, style_tokens。"
                ),
            },
            {"role": "user", "content": user_blob},
        ]
    )
