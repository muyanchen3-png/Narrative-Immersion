"""从镜头字段直接构建角色外观信息，禁止模型编造。

流程：
  1. 把所有镜头的 summary/dialogue/characters/location/actions/emotion/objects/visual_style/tags
     字段全部聚合，按镜头发分组。
  2. 严格从镜头原文提炼外观关键词（只能从原文提取，禁止自由发挥）。
  3. 直接取 visual_style / emotion / objects / location 字段填入生图 prompt，不经抽象层。
  4. 智能体输出 identity_summary / who_is / role_in_story（供前端展示），
     生图 prompt 则直接引用镜头原始字段。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from .. import models
from .llm import get_llm
from .llm_context import bind_llm, unbind_llm

logger = logging.getLogger(__name__)


# ─── 工具函数 ────────────────────────────────────────────────────────


def _flatten(d: Any) -> str:
    """把字典摊平为 k:v; k:v 形式的短字符串。"""
    if not isinstance(d, dict):
        return str(d) if d else ""
    parts: List[str] = []
    for k, v in d.items():
        if isinstance(v, dict):
            parts.append(_flatten(v))
        elif isinstance(v, (list, tuple)):
            parts.append(k + ": " + ", ".join(str(x) for x in v if x))
        elif v:
            parts.append(k + ": " + str(v))
    return "; ".join(parts)


# ─── 镜头上下文聚合 ────────────────────────────────────────────────


def _build_shot_context(
    shots: List[models.ShotSegment],
    display_name: str,
    name_aliases: List[str],
) -> str:
    """
    把每个镜头的全部视觉相关字段文本化，供生图 prompt 直接引用。
    每个镜头块包含：摘要 / 对白 / 人物字段 / 地点 / 动作 / 道具 / 情绪 / 视觉风格。
    """
    lines: List[str] = []
    all_names = [display_name] + [a for a in name_aliases if a != display_name]

    for idx, s in enumerate(shots):
        parts: List[str] = []
        parts.append("=== 镜头 {} [{:.1f}s–{:.1f}s] ===".format(idx + 1, s.start_time, s.end_time))

        # 该角色是否在 characters 字段里
        shot_chars = [str(c) for c in (s.characters or [])]
        matched = [c for c in shot_chars if c in all_names]
        parts.append("[角色出现情况] " + (", ".join(matched) if matched else "（未在 characters 字段出现）"))

        summary = (s.summary or "").strip()
        if summary:
            parts.append("[摘要] " + summary)

        dialogue = (s.dialogue or "").strip()
        if dialogue:
            parts.append("[对白] " + dialogue[:200])

        location = (s.location or "").strip()
        if location:
            parts.append("[地点] " + location)

        actions = s.actions or []
        if actions:
            parts.append("[动作] " + ", ".join(str(a) for a in actions[:6]))

        objects = s.objects or []
        if objects:
            parts.append("[道具/物体] " + ", ".join(str(o) for o in objects[:8]))

        emotion = (s.emotion or "").strip()
        if emotion:
            parts.append("[情绪/氛围] " + emotion)

        vs = s.visual_style
        if vs and isinstance(vs, dict) and vs:
            parts.append("[视觉风格] " + _flatten(vs))

        tags = s.tags or []
        if tags:
            parts.append("[标签] " + ", ".join(str(t) for t in tags[:10]))

        lines.append("\n".join(parts))

    return "\n\n".join(lines)


# ─── 从镜头原文严格提取外观关键词 ──────────────────────────────────


def _extract_appearance_from_shots(
    shots: List[models.ShotSegment],
    display_name: str,
    db: Session,
    profile: str,
) -> str:
    """
    严格从镜头原文提炼外观关键词：禁止编造，只能引用原文已有词汇。
    输出每行格式：项 原文描述 [来源：镜头N]
    """
    char_blocks: List[str] = []
    for idx, s in enumerate(shots):
        block_parts: List[str] = []
        summary = (s.summary or "").strip()
        if summary:
            block_parts.append(summary)
        dialogue = (s.dialogue or "").strip()
        if dialogue:
            block_parts.append("对白：" + dialogue[:150])
        objects = s.objects or []
        if objects:
            block_parts.append("道具：" + ", ".join(str(o) for o in objects[:5]))
        actions = s.actions or []
        if actions:
            block_parts.append("动作：" + ", ".join(str(a) for a in actions[:4]))
        if block_parts:
            char_blocks.append(
                "--- 镜头 {} 中 {} 的画面信息 ---\n".format(idx + 1, display_name)
                + "\n".join(block_parts)
            )

    context_text = "\n\n".join(char_blocks)
    if not context_text.strip():
        return ""

    system = (
        "<task>character_appearance_extraction</task>\n"
        "你是一个严格的视觉描述提取器。你的任务是从下面的「镜头原文」中提取该角色的外观关键词。\n"
        "规则：\n"
        "1. 只能从原文提取，不要添加任何原文里没有的描述。\n"
        "2. 原文没有提到外观项时，直接跳过，不要编造。\n"
        "3. 原文提到的视觉细节（颜色/发型/服装/配饰）必须全部保留。\n"
        "4. 输出格式：每行一项：`项 原文描述 [来源：镜头N]`。\n"
        "5. 只输出客观描述，不要主观解读。\n"
    )

    bind_llm(db, profile)
    try:
        text = get_llm().chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps({"角色名称": display_name, "镜头原文": context_text}, ensure_ascii=False)},
            ],
            temperature=0.2,
            max_tokens=800,
        )
    finally:
        unbind_llm()

    return text if isinstance(text, str) else ""


# ─── 聚合视觉风格字段 ──────────────────────────────────────────────


def _aggregate_visual_style(shots: List[models.ShotSegment]) -> Dict[str, str]:
    """
    直接从每个镜头的 visual_style / emotion / objects / location 字段聚合，
    返回 style_block / emotion_block / scene_block。
    """
    style_keywords: List[str] = []
    emotion_keywords: List[str] = []
    scene_keywords: List[str] = []

    for s in shots:
        vs = s.visual_style
        if vs and isinstance(vs, dict) and vs:
            flat = _flatten(vs)
            if flat:
                style_keywords.append(flat)
        em = (s.emotion or "").strip()
        if em:
            emotion_keywords.append(em)
        loc = (s.location or "").strip()
        if loc:
            scene_keywords.append("地点: " + loc)
        objs = s.objects or []
        if objs:
            scene_keywords.append("道具: " + ", ".join(str(o) for o in objs[:6]))

    style_block = " | ".join("visual style: " + s for s in style_keywords) if style_keywords else "anime illustration, high detail"
    emotion_block = ", ".join(emotion_keywords[:4]) if emotion_keywords else ""
    scene_block = " ; ".join(scene_keywords[:8]) if scene_keywords else ""

    return {
        "style_block": style_block,
        "emotion_block": emotion_block,
        "scene_block": scene_block,
    }


# ─── 组装最终生图 prompt ───────────────────────────────────────────


def compose_final_prompt(
    shot_context: str,
    appearance_keywords: str,
    style_block: str,
    scene_block: str,
    emotion_block: str,
    character_label: str,
    reference_image_exists: bool,
) -> str:
    """
    把镜头原始字段 + 严格提取的外观关键词直接组成生图 prompt，
    不经过任何抽象层，让模型直接参照镜头内容生成三视图。
    """
    sections: List[str] = []

    sections.append(
        "【三视图角色设定图生成指令】\n"
        "请根据以下真实镜头内容生成角色三视图（正面 / 侧面 / 背面）。\n"
        "严格遵守「约束」部分的所有要求。"
    )

    sections.append("【角色身份】 " + character_label)

    sections.append(
        "【角色外观关键词（严格从镜头原文提取，禁止添加原文没有的描述）】\n"
        + (appearance_keywords if appearance_keywords.strip()
           else "（外观信息未在镜头中确认，按通用角色描述生成）")
    )

    if shot_context.strip():
        sections.append(
            "【全部相关镜头原始描述（供你理解角色的真实画面）】\n"
            + shot_context[:2000]
        )

    sections.append("【画面风格关键词（直接摘自镜头 visual_style 字段）】\n" + style_block)

    if emotion_block:
        sections.append("【情绪/氛围关键词（直接摘自镜头 emotion 字段）】 " + emotion_block)

    if scene_block:
        sections.append("【场景与道具（直接摘自镜头 location/objects 字段）】 " + scene_block)

    sections.append(
        "【人数与主体 - 必须严格遵守】\n"
        "整张横向设定图只允许一名主人公（即本卡片目标角色）以全身形式呈现；禁止第二名人物入画，"
        "禁止双人/多人同框，禁止人群背景中以多人为画面主体；禁止在同一格里画两个或以上完整人物。"
        "三个视图必须是同一人的正面、侧面、背面 turnaround，不得出现「一格一人」式的多名角色。"
        "English: exactly one full-body protagonist; no second person; no crowd as subject; solo character sheet."
    )

    sections.append(
        "【三视图生成约束 - 必须严格遵守】\n"
        "neutral standing pose, arms relaxed at sides, feet shoulder-width apart, "
        "three-quarter turn for side view, full front view, full back view, "
        "consistent proportions across all three views, no text, no watermark, high detail"
    )

    if reference_image_exists:
        sections.append(
            "【参照图】 本次请求附带了一张参照图（subject_reference），"
            "角色面部特征、发型、服装颜色与款式必须与参照图完全一致，"
            "仅改变视角（正面 / 侧面 / 背面），不要改变角色外貌。"
        )

    return "\n\n".join(sections)


# ─── 身份智能体（输出结构化 JSON，供前端展示）──────────────────────


def _generate_identity_json(
    shots: List[models.ShotSegment],
    video: models.VideoAsset,
    display_name: str,
    name_aliases: List[str],
    db: Session,
    profile: str,
    reference_shot: Optional[models.ShotSegment] = None,
) -> Dict[str, Any]:
    """从镜头原文输出结构化身份 JSON（仅用于前端展示，不直接用于生图 prompt）。

    若提供 reference_shot，则身份摘要必须与该镜头画面（地点/服装/氛围）一致，
    避免「全文叙事」与「卡片参照图」来自不同镜头时产生矛盾。
    """

    shot_lines: List[str] = []
    for idx, s in enumerate(shots[:20]):
        parts: List[str] = []
        parts.append("=== 镜头 {} [{:.1f}s--{:.1f}s] ===".format(idx + 1, s.start_time, s.end_time))
        summary = (s.summary or "").strip()
        if summary:
            parts.append("[摘要] " + summary)
        dialogue = (s.dialogue or "").strip()
        if dialogue:
            parts.append("[对白] " + dialogue[:200])
        characters = s.characters or []
        if characters:
            parts.append("[人物字段] " + ", ".join(str(c) for c in characters[:8]))
        location = (s.location or "").strip()
        if location:
            parts.append("[地点] " + location)
        visual_style = s.visual_style or {}
        if visual_style and isinstance(visual_style, dict):
            parts.append("[视觉风格] " + json.dumps(visual_style, ensure_ascii=False))
        emotion = (s.emotion or "").strip()
        if emotion:
            parts.append("[情绪/氛围] " + emotion)
        shot_lines.append("\n".join(parts))

    ref_block: Optional[str] = None
    if reference_shot is not None:
        rs = reference_shot
        rp: List[str] = []
        rp.append(
            "时间 {:.1f}s — {:.1f}s，镜头 id 前缀 {}".format(
                float(rs.start_time), float(rs.end_time), str(rs.id)[:8]
            )
        )
        if (rs.summary or "").strip():
            rp.append("[摘要] " + (rs.summary or "").strip()[:600])
        if (rs.location or "").strip():
            rp.append("[地点] " + (rs.location or "").strip())
        ch = rs.characters or []
        if ch:
            rp.append("[人物字段] " + ", ".join(str(c) for c in ch[:8]))
        vs = rs.visual_style or {}
        if vs and isinstance(vs, dict) and vs:
            rp.append("[视觉风格] " + json.dumps(vs, ensure_ascii=False)[:400])
        ref_block = "\n".join(rp)

    bundle = {
        "成片标题": video.title,
        "成片简介（限前400字）": (video.description or "")[:400],
        "目标角色当前称呼": display_name,
        "别名/其它写法": name_aliases[:16],
        "镜头证据（共{}条）".format(len(shot_lines)): shot_lines,
    }
    if ref_block:
        bundle["【重要】本卡片参照图来自以下单条镜头，身份描述须与此画面一致"] = ref_block

    system = (
        "<task>character_identity</task>\n"
        "你是影视角色统筹。你只能从「镜头证据」中提取角色信息，禁止编造镜头中没有的外观细节。\n"
        "\n"
        "若用户消息里包含「【重要】本卡片参照图来自以下单条镜头」：\n"
        "  • identity_summary、who_is、appearance_for_art、visual_style_for_gen 必须以该条镜头的"
        "摘要、地点、视觉风格为准；描述的是「画面中这个人」在此镜头里的样子。\n"
        "  • 其它镜头仅用于补充剧情行为；若其它镜头写的是现代都市而参照镜头明显是古装/异世界等，"
        "不得以其它镜头覆盖参照镜头的画面设定；可在 evidence_summary 中写："
        "「剧情跨越多场景，本卡片配图取自 X–Y 秒镜头」。\n"
        "  • 禁止在 identity_summary 中出现参照镜头画面中显然没有的服饰或场景关键词"
        "（除非 evidence_summary 明确说明叙事与配图分离）。\n"
        "\n"
        "输出 JSON（全部字段必须有值）：\n"
        "  identity_summary   -- 一句话定位（谁+立场+在故事中的位置），须与参照镜头画面一致\n"
        "  who_is           -- 面向观众的回答（30字以内）\n"
        "  role_in_story    -- 叙事功能（主角/配角/反派等）\n"
        "  appearance_for_art -- 给生图模型的英文外观描述，全部来自镜头证据，"
        "包含 hair color and style、eye color、clothing colors and patterns、"
        "accessories、build/skin tone、iconic features；"
        "用英文短语逗号分隔，不要句子；某特征在镜头中无证据时写 not confirmed in footage\n"
        "  visual_style_for_gen -- 画面风格关键词（来自 visual_style 字段），英文短语逗号分隔\n"
        "  evidence_summary -- 一句话说明哪些镜头支撑了你的推断（若配图镜头与叙事侧重不同须点名）\n"
        "不要 markdown 代码块，直接输出 JSON 对象。"
    )

    bind_llm(db, profile)
    try:
        raw = get_llm().chat_json(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(bundle, ensure_ascii=False)},
            ],
            temperature=0.3,
            max_tokens=1500,
        )
    finally:
        unbind_llm()

    if not isinstance(raw, dict):
        fallback: Dict[str, Any] = {
            "identity_summary": "镜头信息不足，无法生成完整人物身份。",
            "who_is": display_name,
            "role_in_story": "",
            "appearance_for_art": "human character, full body, neutral pose",
            "visual_style_for_gen": "anime illustration",
            "evidence_summary": "无有效证据。",
        }
        if reference_shot is not None and ref_block:
            fallback["reference_frame_alignment"] = (
                "身份摘要与左侧参照图均以此镜头为准：\n" + ref_block
            )[:1200]
        return fallback

    out_json = {
        "identity_summary": str(raw.get("identity_summary") or "")[:800],
        "who_is": str(raw.get("who_is") or display_name)[:400],
        "role_in_story": str(raw.get("role_in_story") or "")[:400],
        "appearance_for_art": str(raw.get("appearance_for_art") or "")[:1200],
        "visual_style_for_gen": str(raw.get("visual_style_for_gen") or "")[:600],
        "evidence_summary": str(raw.get("evidence_summary") or "")[:800],
    }
    if reference_shot is not None and ref_block:
        out_json["reference_frame_alignment"] = (
            "身份摘要与左侧参照图均以此镜头为准：\n" + ref_block
        )[:1200]
    return out_json


# ─── 公开入口 ─────────────────────────────────────────────────────


def analyze_character_from_evidence(
    db: Session,
    *,
    video: models.VideoAsset,
    display_name: str,
    name_aliases: List[str],
    evidence_shots: List[models.ShotSegment],
    profile: str = "fast",
    reference_shot: Optional[models.ShotSegment] = None,
) -> Dict[str, Any]:
    """
    两路并行：
      A. 身份 JSON（写入 agent_profile 供前端展示）
      B. 直接从镜头字段构建生图 prompt parts（由 enrich_video_character 拼入 generate_turnaround_sheet）

    返回的结构同时包含两路内容。
    """

    # ── A. 身份 JSON（前端展示）────────────────────────────
    agent_out = _generate_identity_json(
        shots=evidence_shots,
        video=video,
        display_name=display_name,
        name_aliases=name_aliases,
        db=db,
        profile=profile,
        reference_shot=reference_shot,
    )

    # ── B1. 镜头上下文（全部原始字段）────────────────────
    all_names = [display_name] + [a for a in name_aliases if a != display_name]
    shot_context = _build_shot_context(evidence_shots, display_name, all_names)

    # ── B2. 严格提取外观关键词 ───────────────────────────
    appearance_keywords = _extract_appearance_from_shots(
        shots=evidence_shots,
        display_name=display_name,
        db=db,
        profile=profile,
    )

    # ── B3. 聚合视觉风格字段 ──────────────────────────────
    style_agg = _aggregate_visual_style(evidence_shots)

    # ── 合并进 agent_out ────────────────────────────────
    out: Dict[str, Any] = {
        **agent_out,
        # 新增：直接塞给生图 pipeline 的原始内容块
        "_shot_context": shot_context[:4000],
        "_appearance_keywords": appearance_keywords[:2000],
        "_style_block": style_agg["style_block"],
        "_emotion_block": style_agg["emotion_block"],
        "_scene_block": style_agg["scene_block"],
        "_target_characters": all_names,
    }

    logger.info(
        "角色身份完成 video=%s name=%s appearance=%s",
        video.id[:12],
        display_name[:40],
        (agent_out.get("appearance_for_art") or "")[:80],
    )
    return out
