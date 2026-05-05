"""成片角色富化：参照镜头抽帧、字幕证据驱动的人物身份智能体、生图三视图/转向图。"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

from sqlalchemy.orm import Session

from .. import models
from ..config import settings
from .character_identity_agent import analyze_character_from_evidence, compose_final_prompt
from .model_resolve import resolve_image_gen_client
from .shot_character_utils import facing_rank_for_character

logger = logging.getLogger(__name__)

# 用于从「混在同一角色名下的多条镜头」里剔除离群画面（如片头古风 vs 正片都市）
_RE_MODERN_URBAN = re.compile(
    r"街道|地铁|马路|西装|都市|城市|写字楼|面试|白领|现代|行人|车流|公文包|流浪|"
    r"小狗|救助|马路|行人道|写字楼|电梯|地铁口|站台|打车|西装革履"
)
_RE_FANTASY_COSTUME = re.compile(
    r"古装|汉服|仙侠|雪山|袍子|长袍|斗笠|幻想|异世界|宫殿|披风|发髻|高束发|"
    r"修炼|御剑|古风|武林|江湖|灵气|仙山|腰带绣|云纹|和服|长衫|布袍|绣|山巅|积雪"
)


def _axis_text(s: models.ShotSegment) -> str:
    parts = [s.summary or "", s.location or "", (s.dialogue or "")[:200]]
    vs = s.visual_style or {}
    if isinstance(vs, dict):
        parts.append(" ".join(str(v) for v in vs.values())[:300])
    objs = s.objects or []
    if objs:
        parts.append(" ".join(str(o) for o in objs[:12]))
    acts = s.actions or []
    if acts:
        parts.append(" ".join(str(a) for a in acts[:8]))
    return " ".join(parts)


def _modern_fantasy_hits(s: models.ShotSegment) -> Tuple[int, int]:
    t = _axis_text(s)
    return len(_RE_MODERN_URBAN.findall(t)), len(_RE_FANTASY_COSTUME.findall(t))


def align_evidence_shots_for_character(
    shots: List[models.ShotSegment],
    display_name: str,
    name_aliases: List[str],
) -> Tuple[List[models.ShotSegment], str]:
    """
    同一 display_name 可能在多条镜头里出现；若少数镜头是片头/闪回（画风与主线不一致），
    按摘要/地点里的关键词多数派，剔除明显离群的镜头，仅用于富化（不改角色库 source_shot_ids）。
    """
    ordered = sorted(shots, key=lambda s: float(s.start_time))
    named = [s for s in ordered if _shot_lists_character(s, display_name, name_aliases)]
    pool = named if named else ordered

    if len(pool) <= 2:
        return pool, ""

    m_total = 0
    f_total = 0
    per = [_modern_fantasy_hits(s) for s in pool]
    for m, f in per:
        m_total += m
        f_total += f

    if m_total == 0 and f_total == 0:
        return pool, ""

    kept: List[models.ShotSegment] = []
    note = ""

    # 主线明显偏现代都市：去掉奇幻古装占优的单镜
    if m_total >= max(f_total * 2, 3):
        for s, (m, f) in zip(pool, per):
            if f > m + 1:
                continue
            kept.append(s)
        if len(kept) >= max(2, len(pool) // 3):
            note = (
                f"富化证据已从 {len(pool)} 条镜头按「都市/现代」主线对齐为 {len(kept)} 条"
                f"（剔除与主线画风冲突的离群镜头）。角色库「来源镜头」仍为全部 {len(shots)} 条。"
            )
            logger.info(
                "证据对齐：%s → %s 条镜头（都市主线多数派）",
                len(pool),
                len(kept),
            )
            return sorted(kept, key=lambda s: float(s.start_time)), note

    # 主线明显偏奇幻：去掉现代占优的离群
    if f_total >= max(m_total * 2, 3):
        for s, (m, f) in zip(pool, per):
            if m > f + 1:
                continue
            kept.append(s)
        if len(kept) >= max(2, len(pool) // 3):
            note = (
                f"富化证据已从 {len(pool)} 条镜头按「古装/奇幻」主线对齐为 {len(kept)} 条"
                f"（剔除与主线画风冲突的离群镜头）。角色库来源仍为全部 {len(shots)} 条。"
            )
            logger.info(
                "证据对齐：%s → %s 条镜头（奇幻主线多数派）",
                len(pool),
                len(kept),
            )
            return sorted(kept, key=lambda s: float(s.start_time)), note

    return pool, ""


def _shot_lists_character(
    s: models.ShotSegment, display_name: str, name_aliases: List[str]
) -> bool:
    """人物是否在镜头的 characters 字段中（与成片角色名 / 别名对齐）。"""
    names = [display_name] + [a for a in name_aliases if a and a != display_name]
    chars = [str(x).strip() for x in (s.characters or []) if x]
    if not chars:
        return False
    for n in names:
        if not n:
            continue
        for c in chars:
            if n == c:
                return True
            if len(n) >= 2 and (n in c or c in n):
                return True
    return False


def _pick_reference_shot(
    shots: List[models.ShotSegment],
    display_name: str,
    name_aliases: List[str],
) -> models.ShotSegment:
    """
    优先从「characters 字段包含该角色」的镜头中选参照帧；同一朝向档位内优先有缩略图；
    朝向优先：正脸 > 侧面 > 未知 > 背面（依赖镜头分析 character_facings）。
    """
    ordered = sorted(shots, key=lambda s: float(s.start_time))
    named = [s for s in ordered if _shot_lists_character(s, display_name, name_aliases)]
    pool = named if named else ordered
    if not named:
        logger.warning(
            "参照帧选取：未找到 characters 含「%s」的镜头，使用全部 %d 条证据镜头",
            display_name[:24],
            len(ordered),
        )

    def sort_key(s: models.ShotSegment) -> tuple:
        fr = facing_rank_for_character(s, display_name, name_aliases)
        has_thumb = (
            0
            if (s.thumbnail_path and Path(str(s.thumbnail_path)).is_file())
            else 1
        )
        return (fr, has_thumb, float(s.start_time))

    pool_sorted = sorted(pool, key=sort_key)
    if not pool_sorted:
        return ordered[len(ordered) // 2]
    best_rank = sort_key(pool_sorted[0])[0]
    tier = [s for s in pool_sorted if sort_key(s)[0] == best_rank]
    use_pool = tier
    return use_pool[len(use_pool) // 2]


def ensure_reference_frame(shot: models.ShotSegment, dest: Path) -> Path:
    """优先使用镜头缩略图；否则对切片视频在中点时间抽一帧。"""
    if shot.thumbnail_path:
        tp = Path(str(shot.thumbnail_path))
        if tp.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(tp, dest)
            return dest
    vid = Path(str(shot.file_path))
    if not vid.is_file():
        raise FileNotFoundError(f"镜头视频不存在：{shot.file_path}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    mid = max(0.05, float(shot.duration) * 0.5)
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            str(mid),
            "-i",
            str(vid),
            "-vframes",
            "1",
            "-q:v",
            "2",
            str(dest),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not dest.is_file():
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"ffmpeg 抽帧失败：{err or proc.returncode}")
    return dest


def ensure_character_preview_reference(db: Session, vc: models.VideoCharacter) -> bool:
    """
    若尚无有效的参照图文件，从关联镜头生成 ``generated/characters/<id>/reference.jpg``
    （优先镜头缩略图，否则对切片中点抽帧）。不调用人物 LLM、不生三视图，**不改变** ``enrichment_status``；
    完整「富化」仍会覆盖同一路径下的文件。
    """

    out_dir = settings.generated_dir / "characters" / vc.id
    dest = out_dir / "reference.jpg"
    existing = (vc.reference_image_path or "").strip()
    if existing:
        try:
            if Path(existing).is_file():
                return False
        except OSError:
            pass

    ids = list(vc.source_shot_ids or [])
    if not ids:
        return False

    shots = (
        db.query(models.ShotSegment)
        .filter(models.ShotSegment.id.in_(ids))
        .order_by(models.ShotSegment.start_time)
        .all()
    )
    if not shots:
        return False

    ref_shot = _pick_reference_shot(shots, vc.display_name, list(vc.aliases or []))
    try:
        ensure_reference_frame(ref_shot, dest)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "自动预览参照帧失败 character=%s video=%s: %s",
            (vc.id or "")[:12],
            (vc.video_id or "")[:12],
            exc,
        )
        return False

    vc.reference_shot_id = ref_shot.id
    vc.reference_video_path = str(ref_shot.file_path)
    vc.reference_image_path = str(dest)
    vc.updated_at = datetime.utcnow()
    return True


def enrich_video_character(
    db: Session,
    *,
    video_id: str,
    character_id: str,
    profile: str = "fast",
) -> models.VideoCharacter:
    video = db.get(models.VideoAsset, video_id)
    vc = db.get(models.VideoCharacter, character_id)
    if not video or not vc or vc.video_id != video_id:
        raise ValueError("角色或成片不存在")
    ids = list(vc.source_shot_ids or [])
    if not ids:
        raise ValueError("该角色没有关联镜头，无法富化")
    shots = (
        db.query(models.ShotSegment)
        .filter(models.ShotSegment.id.in_(ids))
        .order_by(models.ShotSegment.start_time)
        .all()
    )
    if not shots:
        raise ValueError("关联镜头记录缺失")

    now = datetime.utcnow()
    vc.enrichment_status = "analyzing"
    vc.updated_at = now
    db.commit()

    aligned_shots, align_note = align_evidence_shots_for_character(
        shots, vc.display_name, list(vc.aliases or [])
    )
    ref_shot = _pick_reference_shot(aligned_shots, vc.display_name, list(vc.aliases or []))
    out_dir = settings.generated_dir / "characters" / vc.id
    ref_img_path = out_dir / "reference.jpg"
    sheet_path = out_dir / "turnaround.png"

    try:
        ensure_reference_frame(ref_shot, ref_img_path)
    except Exception as exc:  # noqa: BLE001
        vc.enrichment_status = "failed"
        vc.updated_at = datetime.utcnow()
        db.commit()
        logger.exception("参照帧失败 video=%s character=%s", video_id[:12], character_id[:12])
        raise RuntimeError(f"参照帧失败：{exc}") from exc

    agent = analyze_character_from_evidence(
        db,
        video=video,
        display_name=vc.display_name,
        name_aliases=[vc.display_name] + list(vc.aliases or []),
        evidence_shots=aligned_shots,
        profile=profile,
        reference_shot=ref_shot,
    )
    agent["_evidence_shots_total_in_library"] = len(shots)
    agent["_evidence_shots_used_for_enrichment"] = len(aligned_shots)
    if align_note:
        agent["_evidence_alignment_note"] = align_note

    # 从 agent 取出新结构：直接来自镜头的内容块
    shot_context = str(agent.get("_shot_context") or "").strip()
    appearance_keywords = str(agent.get("_appearance_keywords") or "").strip()
    style_block = str(agent.get("_style_block") or "").strip()
    emotion_block = str(agent.get("_emotion_block") or "").strip()
    scene_block = str(agent.get("_scene_block") or "").strip()
    label = str(agent.get("who_is") or vc.display_name).strip()

    # 直接用 compose_final_prompt 把镜头原始字段塞入生图 prompt
    gen_prompt = compose_final_prompt(
        shot_context=shot_context,
        appearance_keywords=appearance_keywords,
        style_block=style_block,
        scene_block=scene_block,
        emotion_block=emotion_block,
        character_label=label,
        reference_image_exists=ref_img_path.is_file(),
    )

    vc.agent_profile = agent
    vc.reference_shot_id = ref_shot.id
    vc.reference_image_path = str(ref_img_path)
    vc.reference_video_path = str(ref_shot.file_path)
    vc.enrichment_status = "partial"
    vc.updated_at = datetime.utcnow()
    db.commit()

    img = resolve_image_gen_client(db, profile)
    try:
        ok = img.generate_turnaround_sheet(
            character_label=label,
            appearance_prompt=gen_prompt,
            reference_image=ref_img_path,
            out_sheet=sheet_path,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("生图异常，将仅保留智能体与参照帧：%s", exc)
        ok = False

    vc.three_views = {
        "sheet": str(sheet_path),
        "reference": str(ref_img_path),
        "source": "api" if ok else "placeholder",
    }
    vc.enrichment_status = "visual_ready" if sheet_path.is_file() else "partial"
    vc.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(vc)
    return vc


def enrich_all_video_characters(
    db: Session,
    *,
    video_id: str,
    profile: str = "fast",
    only_pending: bool = True,
) -> List[dict]:
    q = db.query(models.VideoCharacter).filter(models.VideoCharacter.video_id == video_id)
    if only_pending:
        q = q.filter(
            models.VideoCharacter.enrichment_status.in_(
                ("pending", "failed", "partial", "analyzing")
            )
        )
    rows = q.order_by(models.VideoCharacter.mention_count.desc()).all()
    out: List[dict] = []
    for vc in rows:
        cid = vc.id
        try:
            updated = enrich_video_character(
                db, video_id=video_id, character_id=cid, profile=profile
            )
            out.append(
                {
                    "character_id": cid,
                    "ok": True,
                    "enrichment_status": updated.enrichment_status,
                    "detail": None,
                }
            )
        except Exception as exc:  # noqa: BLE001
            out.append(
                {
                    "character_id": cid,
                    "ok": False,
                    "enrichment_status": "failed",
                    "detail": str(exc)[:500],
                }
            )
    return out
