"""镜头内容理解：基于 LLM/VLM 的字段抽取（Mock 模式下使用规则）。

若提供 segment_video_path 且已配置真实 API，会从切片 MP4 抽取中间帧；
MiniMax（base_url 含 minimax）走 /v1/coding_plan/vlm，其余走 OpenAI 兼容多模态 chat。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from .llm import (
    _safe_parse_json,
    get_llm,
    image_file_to_data_uri,
    is_minimax_endpoint,
    minimax_vlm_describe_frame,
)
from . import media

logger = logging.getLogger(__name__)


def _safe_json_list(value: Any, *, label: str = "") -> list:
    """保证写入 SQLAlchemy JSON / SQLite 的是「仅含 JSON 原生类型」的 list，避免 numpy 标量等导致 binding 失败。"""

    if value is None:
        return []
    if isinstance(value, str):
        s = value.strip()
        return [s] if s else []
    if isinstance(value, dict):
        try:
            return [json.loads(json.dumps(value, ensure_ascii=False, default=str))]
        except (TypeError, ValueError):
            return [str(value)]
    if not isinstance(value, (list, tuple)):
        try:
            return json.loads(json.dumps([value], ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            return [str(value)]

    try:
        out = json.loads(json.dumps(list(value), ensure_ascii=False, default=str))
        if isinstance(out, list):
            return out
    except (TypeError, ValueError) as exc:
        logger.warning("镜头字段 %s list 规范化失败，退回字符串列表：%s", label or "?", exc)
    if isinstance(value, (list, tuple)):
        return [str(x) for x in value if x is not None]
    return [str(value)]


def _safe_json_dict(value: Any, *, label: str = "") -> dict:
    if value is None or not isinstance(value, dict):
        return {}
    try:
        out = json.loads(json.dumps(value, ensure_ascii=False, default=str))
        return out if isinstance(out, dict) else {}
    except (TypeError, ValueError) as exc:
        logger.warning("镜头字段 %s dict 规范化失败：%s", label or "?", exc)
        return {}


def normalize_analysis_for_shot(analyzed: Dict[str, Any]) -> Dict[str, Any]:
    """将 analyze_shot 返回的 JSON 规范为可写入 ShotSegment 的字段（ingest 与单镜重新生成共用）。"""

    em = analyzed.get("emotion")
    if isinstance(em, list):
        em = " / ".join(str(x) for x in em) if em else None
    elif em is not None:
        em = str(em)
    if isinstance(em, str) and len(em) > 64:
        em = em[:64]

    loc = analyzed.get("location")
    if loc is not None and not isinstance(loc, str):
        loc = str(loc)

    dialogue = analyzed.get("dialogue")
    if dialogue is not None and not isinstance(dialogue, str):
        dialogue = str(dialogue)

    summary = analyzed.get("summary")
    if summary is not None and not isinstance(summary, str):
        summary = str(summary)

    ca = _safe_json_dict(analyzed.get("continuity_anchors"), label="continuity_anchors")
    vpc = analyzed.get("visible_person_count")
    if isinstance(vpc, (int, float)):
        ca["visible_person_count"] = int(max(0, round(vpc)))
    facings = analyzed.get("character_facings")
    if isinstance(facings, list):
        ca["character_facings"] = [
            str(x).strip().lower() if x is not None else "unknown" for x in facings[:64]
        ]

    return {
        "summary": summary,
        "characters": _safe_json_list(analyzed.get("characters"), label="characters"),
        "location": loc,
        "actions": _safe_json_list(analyzed.get("actions"), label="actions"),
        "dialogue": dialogue,
        "emotion": em,
        "objects": _safe_json_list(analyzed.get("objects"), label="objects"),
        "visual_style": _safe_json_dict(analyzed.get("visual_style"), label="visual_style"),
        "continuity_anchors": ca,
        "tags": _safe_json_list(analyzed.get("tags"), label="tags"),
    }


def analyze_shot(
    *,
    video_title: str,
    index: int,
    start: float,
    end: float,
    hint: str = "",
    segment_video_path: Optional[str] = None,
) -> Dict:
    characters_scope = (
        "字段「characters」：仅列出本镜头画面内**可见、同框出镜**的人物或主体（如宠物、拟人形象）。"
        "写法要求：用「名字 + 外观特征」（外观特征至少包含：发型发色、衣服主色、标志性配饰）分隔用顿号。禁止只写纯名字，若纯名字是唯一信息则写「名字（外观不详）」。"
        "禁止列入仅在台词/旁白/字幕或剧情推断中出现、但本帧画面中**并未出现**的角色。"
    )
    system_content = (
        "<task>shot_analysis</task>"
        f"<index>{index}</index>"
        "你是视频镜头分析师。结合配图（若有）与元数据输出该镜头的结构化字段："
        "summary, characters, location, actions, dialogue, emotion, objects, "
        "visual_style, continuity_anchors, tags；并额外输出："
        "visible_person_count（整数，本画面内清晰可辨的**人物/主体**人数，不得小于 characters 非空条数）"
        "与 character_facings（字符串数组，与 characters 严格同序，取值 front|side|back|unknown，"
        "表示各主体相对镜头的面部朝向，front=正脸/面向镜头）。返回 JSON。"
        + characters_scope
    )
    user_meta = json.dumps(
        {
            "video_title": video_title,
            "index": index,
            "start": start,
            "end": end,
            "hint": hint,
        },
        ensure_ascii=False,
    )

    client = get_llm()
    vp = segment_video_path
    use_visual = bool(vp and Path(vp).is_file() and client.api_key and client.provider != "mock")

    if use_visual:
        try:
            info = media.probe(vp)  # type: ignore[arg-type]
            mid = max(0.05, min(info.duration * 0.45, max(0.1, info.duration - 0.05)))
            fd, jpg = tempfile.mkstemp(suffix=".jpg")
            os.close(fd)
            try:
                media.thumbnail(vp, mid, jpg)
                if not Path(jpg).is_file() or Path(jpg).stat().st_size < 80:
                    raise RuntimeError("抽帧失败或文件过小")

                if is_minimax_endpoint(client.base_url or ""):
                    logger.info(
                        "镜头分析 MiniMax VLM video=%s index=%s file=%s",
                        video_title[:40],
                        index,
                        Path(vp).name,
                    )
                    prompt = (
                        f"{system_content}\n\n镜头元数据：{user_meta}\n\n"
                        "请结合配图（该镜头切片中间帧）理解画面，只输出一个 JSON 对象，字段："
                        "summary, characters, location, actions, dialogue, emotion, objects, visual_style, continuity_anchors, tags, "
                        "visible_person_count, character_facings。"
                        "visible_person_count 为整数；character_facings 与 characters 同序、取 front|side|back|unknown。"
                        "characters 仅含画面内可见人物/主体，勿填对白里提到但画面未出现者。"
                        "characters/actions/objects/tags/character_facings 为数组；visual_style、continuity_anchors 为对象；不要 markdown 代码块。"
                    )
                    text = minimax_vlm_describe_frame(
                        base_url=client.base_url or "",
                        api_key=client.api_key or "",
                        prompt=prompt,
                        jpeg_path=jpg,
                    )
                    parsed = _safe_parse_json(text)
                    if parsed:
                        return parsed
                    logger.warning("MiniMax VLM 返回未能解析为 JSON，尝试从正文截取")
                    return _safe_parse_json(text) or {}

                uri = image_file_to_data_uri(jpg)
                logger.info(
                    "镜头分析 OpenAI 兼容多模态 video=%s index=%s file=%s",
                    video_title[:40],
                    index,
                    Path(vp).name,
                )
                return client.chat_json_multimodal(
                    [
                        {"role": "system", "content": system_content},
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": user_meta
                                    + "\n请结合配图（中间帧）填写 JSON，含 visible_person_count 与 character_facings（同序）。"
                                    + "\ncharacters 仅列本帧画面中可见、同框的角色；勿根据对白补充画面外人物。",
                                },
                                {"type": "image_url", "image_url": {"url": uri}},
                            ],
                        },
                    ],
                    temperature=0.35,
                    max_tokens=2048,
                )
            finally:
                try:
                    Path(jpg).unlink()
                except OSError:
                    pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("镜头配图分析失败，回退纯文本：%s", exc)

    return get_llm().chat_json(
        [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_meta},
        ]
    )
