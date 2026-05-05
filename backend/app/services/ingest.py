"""上传后视频处理流水线：转码 -> 多粒度切分 -> 内容理解 -> 剧情状态 -> 主线时间线。"""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..config import settings
from .. import models
from . import analysis, asr, character_library, ingest_progress as ingest_progress_tracker, media, story_state, timeline
from .llm import get_llm
from .llm_context import bind_model_kind, unbind_llm

logger = logging.getLogger(__name__)


def register_video(
    db: Session,
    *,
    src_path: str,
    title: str,
    description: str = "",
    config: dict | None = None,
) -> models.VideoAsset:
    info = media.probe(src_path)
    video_id = str(uuid.uuid4())
    target = settings.uploads_dir / f"{video_id}.mp4"
    if Path(src_path).resolve() != target.resolve():
        shutil.copy2(src_path, target)
    poster = settings.uploads_dir / f"{video_id}.jpg"
    media.thumbnail(str(target), max(0.5, info.duration * 0.1), str(poster))

    video = models.VideoAsset(
        id=video_id,
        title=title,
        description=description,
        duration=info.duration,
        width=info.width,
        height=info.height,
        fps=info.fps,
        file_path=str(target),
        poster_path=str(poster) if poster.exists() else None,
        status="processing",
        config=config or {},
    )
    db.add(video)
    db.flush()
    return video


def slice_segments(
    db: Session,
    *,
    video: models.VideoAsset,
    granularity: str,
    boundaries: List[Tuple[float, float]],
    track_progress_video_id: Optional[str] = None,
) -> List[models.ShotSegment]:
    out_dir = settings.segments_dir / video.id / granularity
    out_dir.mkdir(parents=True, exist_ok=True)

    bind_model_kind(db, "fast", "vlm")
    segments: List[models.ShotSegment] = []
    try:
        for i, (s, e) in enumerate(boundaries):
            if e - s < 0.4:
                continue
            seg_path = out_dir / f"{i:04d}.mp4"
            media.cut(video.file_path, s, e, str(seg_path))
            thumb_path = out_dir / f"{i:04d}.jpg"
            media.thumbnail(str(seg_path), max(0.05, (e - s) * 0.3), str(thumb_path))

            analyzed = analysis.analyze_shot(
                video_title=video.title,
                index=i,
                start=s,
                end=e,
                hint=f"granularity={granularity}",
                segment_video_path=str(seg_path),
            )
            fields = analysis.normalize_analysis_for_shot(analyzed)
            if asr.is_asr_enabled(db):
                try:
                    asr.enrich_shot_fields_with_asr(Path(seg_path), fields, db=db)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("镜头 ASR 合并跳过 index=%s: %s", i, exc)

            # audio_path 由 ASR 写入 fields，勿在此重复传参（否则会与 **fields 冲突）
            seg = models.ShotSegment(
                id=str(uuid.uuid4()),
                video_id=video.id,
                granularity=granularity,
                index=i,
                start_time=s,
                end_time=e,
                duration=e - s,
                file_path=str(seg_path),
                thumbnail_path=str(thumb_path) if thumb_path.exists() else None,
                source="origin",
                quality_score=0.9,
                **fields,
            )
            db.add(seg)
            segments.append(seg)
            if track_progress_video_id:
                ingest_progress_tracker.tick(track_progress_video_id)
    finally:
        unbind_llm()
    db.flush()
    return segments


def _fixed_boundaries(duration: float, step: float) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    cursor = 0.0
    while cursor < duration:
        end = min(duration, cursor + step)
        out.append((cursor, end))
        cursor = end
    return out


def _scene_boundaries(duration: float, target_count: int = 8) -> List[Tuple[float, float]]:
    """简化的场景切分：均分为 target_count 段。

    真实场景可使用 PySceneDetect 等基于像素差异的切分。
    """

    if duration <= 0:
        return []
    target_count = max(2, min(target_count, int(duration // 5) or 2))
    step = duration / target_count
    return [(i * step, min(duration, (i + 1) * step)) for i in range(target_count)]


def count_analyzable_shots(duration: float, granularities: Iterable[str]) -> int:
    """与 slice_segments 一致：仅统计时长 ≥0.4s 的镜头（将被切片并 analyze）。"""

    n = 0
    for g in granularities:
        if g == "1s":
            boundaries = _fixed_boundaries(duration, 1.0)
        elif g == "5s":
            boundaries = _fixed_boundaries(duration, 5.0)
        elif g == "10s":
            boundaries = _fixed_boundaries(duration, 10.0)
        elif g == "scene":
            boundaries = _scene_boundaries(duration, target_count=max(4, int(duration // 8)))
        elif g == "story":
            boundaries = _scene_boundaries(duration, target_count=max(2, int(duration // 30)))
        else:
            continue
        for s, e in boundaries:
            if e - s >= 0.4:
                n += 1
    return n


def slice_multi_granularity(
    db: Session,
    *,
    video: models.VideoAsset,
    granularities: Iterable[str],
    track_progress_video_id: Optional[str] = None,
) -> List[models.ShotSegment]:
    duration = video.duration
    all_segments: List[models.ShotSegment] = []
    for g in granularities:
        if g == "1s":
            boundaries = _fixed_boundaries(duration, 1.0)
        elif g == "5s":
            boundaries = _fixed_boundaries(duration, 5.0)
        elif g == "10s":
            boundaries = _fixed_boundaries(duration, 10.0)
        elif g == "scene":
            boundaries = _scene_boundaries(duration, target_count=max(4, int(duration // 8)))
        elif g == "story":
            boundaries = _scene_boundaries(duration, target_count=max(2, int(duration // 30)))
        else:
            continue
        all_segments.extend(
            slice_segments(
                db,
                video=video,
                granularity=g,
                boundaries=boundaries,
                track_progress_video_id=track_progress_video_id,
            )
        )
    return all_segments


def _shot_lines_for_video_synopsis(shots: List[models.ShotSegment], *, max_shots: int = 60) -> str:
    """将主线镜头分析拼成给 LLM 的纯文本（按时间）。"""

    lines: List[str] = []
    for s in shots[:max_shots]:
        sm = (s.summary or "").strip() or "（该镜头暂无摘要）"
        loc = (s.location or "").strip()
        loc_s = f"；地点：{loc}" if loc else ""
        ch = s.characters or []
        ch_s = ""
        if isinstance(ch, list) and ch:
            ch_s = "；人物：" + "、".join(str(x) for x in ch[:8])
        dlg = (s.dialogue or "").strip()
        dlg_s = f"；对白摘录：{dlg[:220]}" if dlg else ""
        lines.append(
            f"[{s.start_time:.1f}s–{s.end_time:.1f}s] {sm}{loc_s}{ch_s}{dlg_s}"
        )
    return "\n".join(lines)


def _fallback_video_description(video: models.VideoAsset, shots: List[models.ShotSegment]) -> str:
    """模型失败时，用镜头摘要简单拼接，保证 video_description 非空。"""

    chunks: List[str] = []
    for s in shots:
        t = (s.summary or "").strip()
        if t:
            chunks.append(t)
    body = " ".join(chunks)
    if not body.strip():
        return f"{(video.title or '该片').strip() or '该片'}：镜头分析尚未产出有效摘要，暂无剧情梗概。"
    if len(body) > 2000:
        body = body[:2000] + "…"
    return body


def fill_video_description_from_shots(
    db: Session,
    *,
    video: models.VideoAsset,
    main_shots: List[models.ShotSegment],
    overwrite: bool = False,
) -> None:
    """根据主线镜头分析生成全片剧情梗概，写入 `VideoAsset.description`（解说里 `video_description` 来源）。"""

    if not main_shots:
        return
    if not overwrite and (video.description or "").strip():
        return

    user_blob = _shot_lines_for_video_synopsis(main_shots)
    title_esc = (video.title or "").replace("<", "‹")[:500]
    system_content = (
        "<task>video_description</task>"
        f"<title>{title_esc}</title>"
        "你是影视资料编辑。下面是同一部成片按时间顺序的镜头分析摘录（含摘要、地点、人物、对白片段）。"
        "请写一段 120～450 字的「全片剧情梗概」，用于后续互动放映解说：交代主要人物与冲突走向；"
        "不得编造摘录中不存在的情节；语气客观简洁；不要使用 Markdown 标题或列表符号，连续段落即可。"
    )

    bind_model_kind(db, settings.default_profile or "fast", "llm")
    out = ""
    try:
        text = get_llm().chat(
            [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_blob},
            ],
            json_mode=False,
            temperature=0.35,
            max_tokens=900,
        )
        out = (text or "").strip()
        if len(out) < 20:
            raise ValueError("synopsis too short")
    except Exception as exc:  # noqa: BLE001
        logger.warning("全片梗概生成失败，使用镜头摘要拼接兜底：%s", exc)
        out = _fallback_video_description(video, main_shots)
    finally:
        unbind_llm()

    if out:
        video.description = out[:16000]
        db.flush()


def finalize_video(
    db: Session,
    *,
    video: models.VideoAsset,
    main_shots: List[models.ShotSegment],
) -> models.Timeline:
    main_timeline = timeline.create_main_timeline(db, video=video, shots=main_shots)
    story_state.generate_initial_state(db, timeline=main_timeline, shots=main_shots)
    video.status = "ready"
    db.flush()
    return main_timeline


def run_slice_finalize_pipeline(
    db: Session,
    *,
    video: models.VideoAsset,
    track_progress_video_id: Optional[str] = None,
) -> Tuple[models.VideoAsset, models.Timeline]:
    """切片 + 镜头分析 + 主线时间线 + 梗概 + 角色库汇总。"""

    cfg = video.config or {}
    granularities = cfg.get("granularities") or ["1s", "5s", "10s", "scene", "story"]
    if track_progress_video_id:
        total = count_analyzable_shots(video.duration, granularities)
        ingest_progress_tracker.start(track_progress_video_id, total=total)

    all_shots = slice_multi_granularity(
        db,
        video=video,
        granularities=granularities,
        track_progress_video_id=track_progress_video_id,
    )
    if track_progress_video_id:
        ingest_progress_tracker.set_phase(track_progress_video_id, "finalizing")

    main_shots = [s for s in all_shots if s.granularity == "scene"]
    if not main_shots:
        main_shots = [s for s in all_shots if s.granularity == "5s"]
    if not main_shots:
        main_shots = all_shots
    main_shots.sort(key=lambda s: s.start_time)
    main_timeline = finalize_video(db, video=video, main_shots=main_shots)
    fill_video_description_from_shots(db, video=video, main_shots=main_shots, overwrite=False)
    try:
        character_library.rebuild_video_characters(db, video.id, granularity=None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("角色库汇总失败（可稍后在媒资库手动提取）：%s", exc)
    return video, main_timeline


def ingest_pipeline(
    db: Session,
    *,
    src_path: str,
    title: str,
    description: str = "",
    config: dict | None = None,
) -> Tuple[models.VideoAsset, models.Timeline]:
    cfg = config or {}
    video = register_video(db, src_path=src_path, title=title, description=description, config=cfg)
    return run_slice_finalize_pipeline(db, video=video, track_progress_video_id=None)


def complete_ingest_after_register(db: Session, *, video_id: str) -> Tuple[models.VideoAsset, models.Timeline]:
    """上传接口先 `register_video` 并提交后，由后台任务调用本函数完成切片与分析。"""

    video = db.get(models.VideoAsset, video_id)
    if not video:
        raise ValueError("视频不存在")
    return run_slice_finalize_pipeline(db, video=video, track_progress_video_id=video_id)


def run_ingest_background(video_id: str) -> None:
    """FastAPI BackgroundTasks：独立会话中跑完整入库流水线。"""

    from ..database import SessionLocal

    db = SessionLocal()
    try:
        complete_ingest_after_register(db, video_id=video_id)
        db.commit()
        ingest_progress_tracker.mark_done(video_id)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("后台成片处理失败 video=%s", video_id[:12])
        ingest_progress_tracker.fail(video_id, str(exc))
        db2 = SessionLocal()
        try:
            v = db2.get(models.VideoAsset, video_id)
            if v:
                v.status = "failed"
                db2.commit()
        except Exception:  # noqa: BLE001
            db2.rollback()
        finally:
            db2.close()
    finally:
        db.close()


def purge_timeline_dependencies_for_video(db: Session, *, video_id: str) -> None:
    """删除该成片下所有时间线及对话、剧情状态、干预、生成任务等依赖（不删 ShotSegment、不删 video_assets 行）。"""

    tls = db.query(models.Timeline).filter(models.Timeline.video_id == video_id).all()
    ids = {t.id for t in tls}
    if not ids:
        return

    intrv_rows = db.query(models.Intervention.id).filter(models.Intervention.timeline_id.in_(ids)).all()
    intrv_ids = [r[0] for r in intrv_rows]
    if intrv_ids:
        job_rows = db.query(models.GenerationJob.id).filter(models.GenerationJob.intervention_id.in_(intrv_ids)).all()
        job_ids = [r[0] for r in job_rows]
        if job_ids:
            db.query(models.GeneratedAsset).filter(models.GeneratedAsset.job_id.in_(job_ids)).delete(
                synchronize_session=False
            )
        db.query(models.GenerationJob).filter(models.GenerationJob.intervention_id.in_(intrv_ids)).delete(
            synchronize_session=False
        )
        db.query(models.SafetyLog).filter(models.SafetyLog.intervention_id.in_(intrv_ids)).delete(
            synchronize_session=False
        )

    db.query(models.Intervention).filter(models.Intervention.timeline_id.in_(ids)).delete(synchronize_session=False)
    db.query(models.ChatMessage).filter(models.ChatMessage.timeline_id.in_(ids)).delete(synchronize_session=False)
    db.query(models.StoryState).filter(models.StoryState.timeline_id.in_(ids)).delete(synchronize_session=False)
    db.query(models.TimelinePatch).filter(
        or_(models.TimelinePatch.from_timeline_id.in_(ids), models.TimelinePatch.to_timeline_id.in_(ids))
    ).delete(synchronize_session=False)

    # 仍挂在 timeline_id / new_timeline_id 上的任务（与 intervention 清理互补）
    touch_jobs = [
        r[0]
        for r in db.query(models.GenerationJob.id)
        .filter(
            or_(
                models.GenerationJob.timeline_id.in_(ids),
                models.GenerationJob.new_timeline_id.in_(ids),
            )
        )
        .all()
    ]
    if touch_jobs:
        db.query(models.GeneratedAsset).filter(models.GeneratedAsset.job_id.in_(touch_jobs)).delete(
            synchronize_session=False
        )
        db.query(models.GenerationJob).filter(models.GenerationJob.id.in_(touch_jobs)).delete(
            synchronize_session=False
        )

    _delete_timelines_leaf_order(db, video_id=video_id)


def _unlink_video_storage_files(
    *,
    video_id: str,
    main_file: str,
    poster_file: Optional[str],
    character_ids: List[str],
) -> None:
    """删除 uploads 成片/封面、segments 切片目录、generated/characters 下该成片角色产物。"""
    from pathlib import Path as _P

    for p in (main_file, poster_file or ""):
        if p and _P(p).is_file():
            try:
                _P(p).unlink()
            except OSError:
                pass
    seg_root = settings.segments_dir / video_id
    if seg_root.exists():
        shutil.rmtree(seg_root, ignore_errors=True)
    root_gen = settings.generated_dir / "characters"
    for cid in character_ids:
        d = root_gen / cid
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)


def delete_video_completely(db: Session, *, video_id: str) -> None:
    """删除数据库中的成片记录及镜头、角色、时间线等；并删除 uploads 成片文件、封面、切片目录、角色生成目录。"""
    video = db.get(models.VideoAsset, video_id)
    if not video:
        raise ValueError("视频不存在")

    char_ids = [r[0] for r in db.query(models.VideoCharacter.id).filter(models.VideoCharacter.video_id == video_id).all()]
    main_fp = video.file_path
    poster_fp = video.poster_path

    purge_timeline_dependencies_for_video(db, video_id=video_id)

    db.query(models.ShotSegment).filter(models.ShotSegment.video_id == video_id).delete(synchronize_session=False)
    db.query(models.VoiceProfile).filter(models.VoiceProfile.video_id == video_id).delete(synchronize_session=False)
    db.query(models.VideoCharacter).filter(models.VideoCharacter.video_id == video_id).delete(synchronize_session=False)

    db.delete(video)
    db.flush()

    _unlink_video_storage_files(
        video_id=video_id,
        main_file=main_fp,
        poster_file=poster_fp,
        character_ids=char_ids,
    )


def _delete_timelines_leaf_order(db: Session, *, video_id: str) -> None:
    """删除某视频下所有时间线版本（先删无子节点的 fork，避免 parent_id 外键阻塞）。"""

    while db.query(models.Timeline).filter(models.Timeline.video_id == video_id).count():
        all_tls = db.query(models.Timeline).filter(models.Timeline.video_id == video_id).all()
        id_set = {t.id for t in all_tls}
        # 出现在他人 parent_id 里的 id = 仍有子时间线指向自己，暂不能删
        ids_with_children = {t.parent_id for t in all_tls if t.parent_id is not None}
        leaves = [i for i in id_set if i not in ids_with_children]
        if not leaves:
            leaves = [all_tls[0].id]
        for lid in leaves:
            tl = db.get(models.Timeline, lid)
            if tl:
                db.delete(tl)
        db.flush()


def delete_shot_segment(db: Session, *, video_id: str, shot_id: str) -> None:
    """删除单条镜头记录及切片文件；解除时间线片段对该镜头的引用；修正角色库中的 source_shot_ids / 参照镜头。"""

    shot = db.get(models.ShotSegment, shot_id)
    if not shot or shot.video_id != video_id:
        raise ValueError("镜头不存在")

    db.query(models.TimelineSegment).filter(models.TimelineSegment.shot_id == shot_id).update(
        {models.TimelineSegment.shot_id: None},
        synchronize_session=False,
    )

    for vc in (
        db.query(models.VideoCharacter).filter(models.VideoCharacter.video_id == video_id).all()
    ):
        ids = list(vc.source_shot_ids or [])
        if shot_id in ids:
            vc.source_shot_ids = [x for x in ids if x != shot_id]
        if vc.reference_shot_id == shot_id:
            vc.reference_shot_id = None
            vc.reference_image_path = None
            vc.reference_video_path = None

    for p in (shot.file_path, shot.thumbnail_path or "", shot.audio_path or ""):
        if not p:
            continue
        fp = Path(p)
        if fp.is_file():
            try:
                fp.unlink()
            except OSError:
                pass

    db.delete(shot)
    db.flush()


def resplit_video(db: Session, *, video_id: str) -> Tuple[models.VideoAsset, models.Timeline]:
    """保留原始成片文件，清空镜头切分与时间线后重新跑切片与主线。"""

    video = db.get(models.VideoAsset, video_id)
    if not video:
        raise ValueError("视频不存在")

    purge_timeline_dependencies_for_video(db, video_id=video_id)

    db.query(models.ShotSegment).filter(models.ShotSegment.video_id == video_id).delete(synchronize_session=False)

    seg_root = settings.segments_dir / video_id
    if seg_root.exists():
        shutil.rmtree(seg_root, ignore_errors=True)

    info = media.probe(video.file_path)
    video.duration = info.duration
    video.width = info.width
    video.height = info.height
    video.fps = info.fps
    video.status = "processing"
    db.flush()

    cfg = video.config or {}
    granularities = cfg.get("granularities") or ["1s", "5s", "10s", "scene", "story"]
    all_shots = slice_multi_granularity(db, video=video, granularities=granularities)
    main_shots = [s for s in all_shots if s.granularity == "scene"]
    if not main_shots:
        main_shots = [s for s in all_shots if s.granularity == "5s"]
    if not main_shots:
        main_shots = all_shots
    main_shots.sort(key=lambda s: s.start_time)
    main_timeline = finalize_video(db, video=video, main_shots=main_shots)
    fill_video_description_from_shots(db, video=video, main_shots=main_shots, overwrite=True)
    try:
        character_library.rebuild_video_characters(db, video.id, granularity=None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("角色库汇总失败（可稍后在媒资库手动提取）：%s", exc)
    db.flush()
    return video, main_timeline
