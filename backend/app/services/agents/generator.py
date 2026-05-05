"""生成智能体：调用图像/视频/音频模型，生成单个镜头片段。"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import models
from ...config import settings
from .. import media
from ..tts import get_tts
from ..video_gen import get_video_gen
from . import voice as voice_agent

logger = logging.getLogger(__name__)


def _tts_character_for_shot(shot: dict) -> str:
    dlg = shot.get("dialogue") or []
    if isinstance(dlg, list) and dlg:
        row = dlg[0]
        if isinstance(row, dict):
            ch = (row.get("character") or "").strip()
            if ch:
                return ch
    subj = (shot.get("subject") or "").strip()
    return subj if subj else "旁白"


def _video_character_for_label(
    db: Session,
    *,
    video_id: str,
    label: str,
) -> Optional[models.VideoCharacter]:
    """按展示名、别名、模糊包含匹配成片角色库中的一条。"""
    subj = (label or "").strip()
    if not subj:
        return None
    rows = (
        db.query(models.VideoCharacter)
        .filter(models.VideoCharacter.video_id == video_id)
        .order_by(models.VideoCharacter.mention_count.desc())
        .all()
    )
    for vc in rows:
        if subj == vc.display_name:
            return vc
    for vc in rows:
        aliases = list(vc.aliases or [])
        if subj in aliases:
            return vc
    for vc in rows:
        dn = vc.display_name or ""
        if len(subj) >= 2 and (subj in dn or dn in subj):
            return vc
    return None


def _effective_reference_image_path(vc: models.VideoCharacter) -> Optional[str]:
    """优先镜头抽帧参照图，其次三视图 sheet/front 等（与角色库页上传一致）。"""
    p = vc.reference_image_path
    if p and Path(str(p)).is_file():
        return str(p)
    tv = vc.three_views or {}
    if isinstance(tv, dict):
        for k in ("sheet", "front", "turnaround", "side", "back"):
            cand = tv.get(k)
            if cand and Path(str(cand)).is_file():
                return str(cand)
        for _k, cand in tv.items():
            if isinstance(cand, str) and cand and Path(cand).is_file():
                return cand
    return None


def resolve_shot_character_and_reference(
    db: Session,
    *,
    video_id: str,
    shot: dict,
) -> tuple[Optional[models.VideoCharacter], Optional[str]]:
    """按对白角色 → 分镜 characters → subject 顺序，解析第一个在角色库中有可用参照图的角色。

    用于：MiniMax 首帧/主体参考（一致性）与 VoiceProfile 绑定（配音一致性）。
    """
    labels: List[str] = []
    seen: set[str] = set()
    for d in shot.get("dialogue") or []:
        if isinstance(d, dict):
            ch = (d.get("character") or "").strip()
            if ch and ch not in seen:
                seen.add(ch)
                labels.append(ch)
    for c in shot.get("characters") or []:
        if isinstance(c, str):
            t = c.strip()
            if t and t not in seen:
                seen.add(t)
                labels.append(t)
    subj = (shot.get("subject") or "").strip()
    if subj and subj not in seen:
        labels.append(subj)

    for label in labels:
        vc = _video_character_for_label(db, video_id=video_id, label=label)
        if vc is None:
            continue
        ref = _effective_reference_image_path(vc)
        if ref:
            return vc, ref

    return None, None


@dataclass
class GeneratedShot:
    shot_id: str
    file_path: str
    audio_path: Optional[str]
    duration: float
    cost: float
    prompt: str
    fallback: bool
    caption: str
    #: 实际使用的视频模型标识（mock- 前缀表示占位片段）
    video_model: str = ""


def generate_shot(
    db: Session,
    *,
    job_id: str,
    video_id: str,
    shot: dict,
    prompts: dict,
    voice_audio: Optional[str] = None,
    voice_text: str = "",
    profile: str = "fast",
    forbid_placeholder: Optional[bool] = None,
    clip_brief: Optional[str] = None,
) -> GeneratedShot:
    duration = float(shot.get("duration", 5.0))
    title = shot.get("subject") or shot.get("action") or "新镜头"
    subtitle = shot.get("voice_over") or (
        shot.get("dialogue", [{}])[0].get("line") if shot.get("dialogue") else ""
    ) or ""

    out_dir = settings.generated_dir / job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    shot_uid = shot.get("id") or str(uuid.uuid4())[:8]
    dst = out_dir / f"shot_{shot_uid}.mp4"

    client = get_video_gen(db, profile)
    resolved_vc, ref_path = resolve_shot_character_and_reference(
        db, video_id=video_id, shot=shot
    )
    subj_refs = [ref_path] if ref_path else None
    if resolved_vc:
        logger.info(
            "镜头绑定角色库：%s，参照图 %s",
            resolved_vc.display_name,
            ref_path,
        )
    elif settings.video_require_character_reference and client.provider == "minimax":
        logger.warning(
            "未解析到带参照图的角色（请确认对白/主体与角色库展示名一致，且已上传参照图或三视图），将不调用 MiniMax 文生",
        )

    # 叙事干预须真实出片：默认禁止占位视频；调用方可显式传入 forbid_placeholder=False（不建议）
    use_no_placeholder = True if forbid_placeholder is None else bool(forbid_placeholder)
    clip = client.generate(
        prompt=prompts.get("video_prompt") or shot.get("summary", ""),
        duration=duration,
        title=title,
        subtitle=subtitle,
        voice_text=voice_text or subtitle,
        dst=str(dst),
        first_frame_image_path=ref_path,
        subject_reference_paths=subj_refs,
        forbid_placeholder=use_no_placeholder,
    )

    speech = (voice_text or subtitle).strip()
    character_key = (
        (resolved_vc.display_name.strip() if resolved_vc else "")
        or _tts_character_for_shot(shot)
    )
    final_video_path = clip.file_path
    voice_sidecar = clip.audio_path
    tts_extra_cost = 0.0
    voice_muxed = False

    def _optional_bgm_file() -> Optional[Path]:
        raw = (settings.generation_bgm_path or "").strip()
        if not raw:
            return None
        p = Path(raw)
        if not p.is_absolute():
            p = settings.storage_path / raw
        return p if p.is_file() else None

    used_bgm: Optional[str] = None
    try:
        vi = media.probe(clip.file_path)
        has_speech = bool(speech.strip())
        # MiniMax 常返回带音轨的 mp4；若有台词仍须 TTS 覆盖/混音，否则会出现「有画面无对白配音」
        if has_speech:
            needs_voice_mux = True
        else:
            # 无台词：仅当无音轨或占位片时补轨
            needs_voice_mux = (not clip.fallback) or not vi.has_audio
        if needs_voice_mux:
            prof = voice_agent.get_or_create_profile(
                db,
                video_id=video_id,
                character=character_key,
            )
            tts = get_tts(db, profile)
            voice_mp = str(out_dir / f"shot_{shot_uid}_voice.m4a")
            if speech:
                tts.synthesize(speech, voice_id=prof.voice_id, dst=voice_mp)
                tts_extra_cost = max(0.02, 0.002 * len(speech))
            else:
                media.make_silent_audio(max(0.5, float(clip.duration)), voice_mp)
            audio_for_mux = voice_mp
            bgm_p = _optional_bgm_file()
            if has_speech and bgm_p is not None:
                mixed = str(out_dir / f"shot_{shot_uid}_voice_bgm.m4a")
                dur_v = max(0.2, float(vi.duration or clip.duration))
                media.mix_voice_with_bgm_track(
                    voice_mp,
                    str(bgm_p),
                    dur_v,
                    mixed,
                    voice_gain=settings.generation_bgm_voice_gain,
                    bgm_gain=settings.generation_bgm_track_gain,
                )
                audio_for_mux = mixed
                used_bgm = str(bgm_p)
            mux_tmp = str(out_dir / f"shot_{shot_uid}_muxed.mp4")
            media.mux_video_with_tts_audio(clip.file_path, audio_for_mux, mux_tmp)
            Path(mux_tmp).replace(Path(clip.file_path))
            voice_sidecar = audio_for_mux
            voice_muxed = True
    except Exception as exc:  # noqa: BLE001
        if settings.intervention_no_fallback:
            raise
        logger.warning("镜头配音或音视频合成失败，保留原始画面：%s", exc)

    total_cost = float(clip.cost) + tts_extra_cost
    try:
        dur_final = media.probe(final_video_path).duration
    except Exception:
        dur_final = float(clip.duration)

    meta: Dict[str, object] = {
        "shot_id": shot_uid,
        "subtitle": subtitle,
        "voice_text": voice_text,
        "speech_text": speech,
        "fallback": clip.fallback,
        "voice_muxed": voice_muxed,
        "tts_sidecar": voice_sidecar,
        "character_display_name": resolved_vc.display_name if resolved_vc else None,
        "character_reference_image": ref_path,
        "voice_character_key": character_key,
        "bgm_path": used_bgm,
    }
    if (clip_brief or "").strip():
        meta["brief"] = (clip_brief or "").strip()[:8000]

    gid = (job_id or "").strip()
    if not gid:
        raise RuntimeError("缺少 job_id，无法登记生成产物 GeneratedAsset")

    db.flush()
    job_row = db.scalar(select(models.GenerationJob.id).where(models.GenerationJob.id == gid))
    if job_row is None:
        raise RuntimeError(
            f"数据库中不存在 GenerationJob id={gid[:12]}…，无法写入生成产物。"
            "若为叙事干预，请确认干预会话未被并发删除；否则为重试后端。"
        )

    asset = models.GeneratedAsset(
        id=str(uuid.uuid4()),
        job_id=gid,
        kind="video",
        file_path=final_video_path,
        duration=dur_final,
        prompt=clip.prompt,
        seed=None,
        model=clip.model,
        cost=total_cost,
        quality_score=0.88 if voice_muxed and not clip.fallback else (0.85 if not clip.fallback else 0.7),
        metadata_json=meta,
    )
    db.add(asset)
    db.flush()

    cap_out = ((clip_brief or "").strip() or subtitle or title).strip()
    return GeneratedShot(
        shot_id=shot_uid,
        file_path=final_video_path,
        audio_path=voice_sidecar,
        duration=float(dur_final),
        cost=total_cost,
        prompt=clip.prompt,
        fallback=clip.fallback,
        caption=cap_out,
        video_model=clip.model,
    )
