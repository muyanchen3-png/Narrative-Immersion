"""安全策略与可执行性分级。"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import List, Optional

from sqlalchemy.orm import Session

from .. import models
from .llm import get_llm


DEFAULT_POLICIES: List[dict] = [
    {
        "label": "色情",
        "category": "forbid",
        "keywords": ["色情", "黄色", "脱衣", "裸露", "性行为"],
        "description": "禁止生成色情内容。",
    },
    {
        "label": "赌博",
        "category": "forbid",
        "keywords": ["赌博", "押注", "下注"],
        "description": "禁止生成赌博相关内容。",
    },
    {
        "label": "毒品",
        "category": "forbid",
        "keywords": ["吸毒", "毒品", "贩毒"],
        "description": "禁止生成毒品相关内容。",
    },
    {
        "label": "极端暴力",
        "category": "forbid",
        "keywords": ["杀人", "屠杀", "炸弹", "恐怖袭击"],
        "description": "禁止生成极端暴力或违法犯罪教学。",
    },
    {
        "label": "自残",
        "category": "forbid",
        "keywords": ["自杀", "自残", "割腕"],
        "description": "禁止生成自残相关内容。",
    },
    {
        "label": "轻微冲突",
        "category": "restrict",
        "keywords": ["争吵", "推搡", "冲突"],
        "description": "允许出现，但需要剧情合理化处理。",
        "rewrite_template": "把冲突改写为非暴力的对峙或言辞交锋。",
    },
    {
        "label": "犯罪改写",
        "category": "rewrite",
        "keywords": ["抢劫", "偷窃", "诈骗"],
        "description": "尝试改写为合法剧情。",
        "rewrite_template": "把犯罪改写为阻止犯罪、调查或合法解决",
    },
]


@dataclass
class SafetyDecision:
    decision: str  # allow / rewrite / reject
    matched_policy: Optional[str]
    rewrite_text: Optional[str]
    reason: Optional[str]


def ensure_default_policies(db: Session) -> None:
    existing = {p.label for p in db.query(models.SafetyPolicy).all()}
    for item in DEFAULT_POLICIES:
        if item["label"] in existing:
            continue
        db.add(
            models.SafetyPolicy(
                id=str(uuid.uuid4()),
                label=item["label"],
                category=item["category"],
                keywords=item["keywords"],
                description=item.get("description", ""),
                rewrite_template=item.get("rewrite_template"),
                enabled=True,
            )
        )
    db.flush()


def evaluate(db: Session, text: str) -> SafetyDecision:
    text = text or ""
    policies = db.query(models.SafetyPolicy).filter(models.SafetyPolicy.enabled.is_(True)).all()
    text_lower = text.lower()
    for p in policies:
        for kw in p.keywords or []:
            if kw and kw.lower() in text_lower:
                if p.category == "forbid":
                    return SafetyDecision("reject", p.label, None, f"命中策略：{p.label}")
                if p.category == "rewrite":
                    rewrite = _try_rewrite(text, p)
                    return SafetyDecision("rewrite", p.label, rewrite, f"命中策略：{p.label}")
                if p.category == "restrict":
                    rewrite = _try_rewrite(text, p)
                    return SafetyDecision("rewrite", p.label, rewrite, f"受限策略：{p.label}")
    return SafetyDecision("allow", None, None, None)


def _try_rewrite(text: str, policy: models.SafetyPolicy) -> str:
    template = policy.rewrite_template or "改写为不违反内容规则的安全版本"
    llm = get_llm()
    out = llm.chat(
        [
            {
                "role": "system",
                "content": (
                    "<task>safety_rewrite</task>"
                    f"<policy>{policy.label}</policy>"
                    f"<template>{template}</template>"
                    "你是一个内容安全编辑，请改写用户的剧情干预指令，使其符合策略要求。"
                    "保持原始用户意图的核心，但去除违法或不当元素。"
                    "只返回改写后的纯文本指令。"
                ),
            },
            {"role": "user", "content": text},
        ]
    )
    if not out or out.startswith("（mock）"):
        out = re.sub(r"|".join(re.escape(k) for k in policy.keywords or [".."]), "（已改写）", text)
    return out


FEASIBILITY_LEVELS = {
    "L0": "拒绝执行",
    "L1": "仅问答不影响时间线",
    "L2": "轻量影响（情绪/风格/旁白）",
    "L3": "局部分支（10-30 秒替换）",
    "L4": "长线分支（新建时间线）",
    "L5": "需要预生成或暂不可执行",
}


@dataclass
class FeasibilityDecision:
    level: str
    rationale: str


def classify_feasibility(intent_text: str, story_summary: str) -> FeasibilityDecision:
    llm = get_llm()
    out = llm.chat_json(
        [
            {
                "role": "system",
                "content": (
                    "<task>feasibility</task>"
                    "你是一名互动叙事系统的可执行性评审员。给定用户干预意图和当前剧情摘要，"
                    "判断该干预属于以下哪一级：L0 拒绝、L1 仅问答、L2 轻量影响、L3 局部分支、"
                    "L4 长线分支、L5 不可即时执行。返回 JSON：{level, rationale}。"
                    f"<context>{story_summary[:200]}</context>"
                ),
            },
            {"role": "user", "content": intent_text},
        ]
    )
    level = out.get("level") or "L3"
    if level not in FEASIBILITY_LEVELS:
        level = "L3"
    return FeasibilityDecision(level=level, rationale=out.get("rationale", ""))
