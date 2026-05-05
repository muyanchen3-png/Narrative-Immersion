"""叙事干预共享上下文：成片角色库摘要，与镜头分析一并写入各智能体，保证人物外观一致。"""

from __future__ import annotations

from typing import List

from sqlalchemy.orm import Session

from .. import models


def character_catalog_for_intervention(
    db: Session,
    video_id: str,
    *,
    limit: int = 28,
    max_chars: int = 12000,
) -> str:
    """
    汇总当前成片角色库（优先出镜次数多），供编剧/导演/分镜/视频 prompt、放映厅问答等引用。
    含展示名、描述、智能体摘要字段，以及创作者在角色卡填写的 **user_notes（用户备注）**。
    无角色库或为空串时不注入。
    """

    rows: List[models.VideoCharacter] = (
        db.query(models.VideoCharacter)
        .filter(models.VideoCharacter.video_id == video_id)
        .order_by(
            models.VideoCharacter.mention_count.desc(),
            models.VideoCharacter.display_name,
        )
        .limit(limit)
        .all()
    )
    if not rows:
        return ""

    lines: List[str] = []
    total = 0
    for r in rows:
        parts: List[str] = [r.display_name]
        if r.description and str(r.description).strip():
            parts.append(str(r.description).strip()[:520])
        ap = r.agent_profile or {}
        if isinstance(ap, dict):
            for k in ("identity_summary", "who_is", "appearance_for_art"):
                v = ap.get(k)
                if isinstance(v, str) and v.strip():
                    parts.append(f"{k}：{v.strip()[:420]}")
                    break
        un = (r.user_notes or "").strip()
        if un:
            # 备注常含长线剧情设定，略放宽以免「觉醒/伏笔」类句子被截断
            parts.append(f"用户备注：{un[:1200]}")
        line = " ".join(parts)
        if len(line) < 4:
            continue
        seg = line[:1600]
        if total + len(seg) + 3 > max_chars:
            break
        lines.append(seg)
        total += len(seg) + 3

    if not lines:
        return ""

    return (
        "【成片角色库 — 身份、外观与用户备注（分镜/生成/问答须对齐；勿凭空新增无关主角）】\n"
        + "\n".join(f"- {ln}" for ln in lines)
    )
