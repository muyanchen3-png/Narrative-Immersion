"""编排智能体：接收用户干预 -> 安全 -> 可执行性 -> 风险 -> 复用 -> 生成 -> 质检 -> 时间线补丁。

Demo 阶段同步执行整套流程并把每个步骤写入 generation_jobs.timeline_log，前端可据此渲染进度条。
真实部署应改为后台任务（Celery/Temporal），并通过 SSE/WebSocket 推送进度。
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.exc import PendingRollbackError
from sqlalchemy.orm import Session

from ... import models
from ...config import settings
from .. import cache, generated_shot_catalog, story_state, timeline
from ..intervention_context import character_catalog_for_intervention
from ..llm import get_llm
from ..video_gen import get_video_gen
from ..safety import (
    FEASIBILITY_LEVELS,
    FeasibilityDecision,
    SafetyDecision,
    classify_feasibility,
    evaluate as safety_evaluate,
)
from . import apply_schedule, director, editor, generator, prompt, qa_check, risk, screenwriter, storyboard, voice

logger = logging.getLogger(__name__)

# 用户可见的失败文案（助手回复与 API error 字段）
INTERVENTION_MODEL_FAILURE_MESSAGE = (
    "干预失败：模型不可用或配置有误，请在 Hermes 设置或环境变量中检查 LLM、视频等模型与密钥。"
)


class InterventionResult(dict):
    pass


def _fallback_shots_from_director_plan(plan: dict) -> List[Dict]:
    """分镜 LLM 若未返回 shots，用导演的 shots_plan 生成最小可用镜头结构，避免复用/生成环节全部跳过。"""
    sp = plan.get("shots_plan") or []
    out: List[Dict] = []
    if not isinstance(sp, list):
        return out
    for i, item in enumerate(sp):
        if not isinstance(item, dict):
            continue
        summary = (item.get("summary") or item.get("description") or "").strip()
        role = (item.get("role") or item.get("subject") or "人物").strip()
        try:
            duration = float(item.get("duration", 5.0) or 5.0)
        except (TypeError, ValueError):
            duration = 5.0
        duration = max(1.0, min(duration, 120.0))
        loc = (item.get("location") or "").strip()
        out.append(
            {
                "id": f"plan_{i}",
                "duration": duration,
                "shot_type": "medium",
                "camera": "static",
                "subject": role,
                "action": summary or role,
                "location": loc,
                "summary": summary or f"{role} 镜头",
                "dialogue": [],
                "voice_over": "",
            }
        )
    return out


def _outline_line_str(item: Any) -> str:
    if item is None:
        return ""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        for k in ("text", "summary", "scene", "step", "label"):
            v = item.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()[:800]
        return str(item)[:500]
    return str(item).strip()


def _default_subject_from_branch(branch: Dict) -> str:
    dials = branch.get("dialogues") or []
    if isinstance(dials, list):
        for d in dials:
            if isinstance(d, dict) and d.get("character"):
                return str(d.get("character"))[:80]
    s = branch.get("summary")
    if isinstance(s, str) and s.strip():
        t = s.strip()
        return (t[:36] + "…") if len(t) > 36 else t
    return "主角"


def _fallback_shots_from_branch(branch: Dict, intent: str) -> List[Dict]:
    """导演/分镜 LLM 均未产出时使用编剧 outline + 对白构造最小分镜。"""
    outline_raw = branch.get("outline") or []
    if isinstance(outline_raw, str):
        outline_raw = [outline_raw]
    if not isinstance(outline_raw, list):
        outline_raw = []

    lines = [_outline_line_str(x) for x in outline_raw]
    lines = [x for x in lines if x]
    subj = _default_subject_from_branch(branch)

    shots: List[Dict] = []
    for i, text in enumerate(lines[:8]):
        shots.append(
            {
                "id": f"outline_{i}",
                "duration": 5.0,
                "shot_type": "medium",
                "camera": "static",
                "subject": subj,
                "action": text[:900],
                "location": "",
                "summary": text[:800],
                "dialogue": [],
                "voice_over": "",
            }
        )

    if not shots:
        summ = (
            (branch.get("summary") or "").strip()
            or intent.strip()
            or "基于用户干预的剧情延展镜头"
        )
        shots.append(
            {
                "id": "outline_0",
                "duration": 6.0,
                "shot_type": "medium",
                "camera": "static",
                "subject": subj,
                "action": summ[:900],
                "location": "",
                "summary": summ[:800],
                "dialogue": [],
                "voice_over": "",
            }
        )

    dials = branch.get("dialogues") or []
    if isinstance(dials, list) and dials and shots:
        dl: List[Dict] = []
        for d in dials:
            if not isinstance(d, dict):
                continue
            ch = d.get("character") or subj
            line = d.get("line") or d.get("text") or ""
            if line:
                dl.append({"character": str(ch)[:80], "line": str(line)[:2000]})
        if dl:
            shots[-1]["dialogue"] = dl
    return shots


def _fallback_shots_minimal(*, intent: str, branch: Dict) -> List[Dict]:
    """最后兜底：单镜头承载干预意图 + 分支摘要。"""
    subj = _default_subject_from_branch(branch)
    summ = (branch.get("summary") or "").strip() or intent.strip() or "剧情干预镜头"
    dials = branch.get("dialogues") or []
    dialogue: List[Dict] = []
    if isinstance(dials, list):
        for d in dials:
            if isinstance(d, dict) and (d.get("line") or d.get("text")):
                dialogue.append(
                    {
                        "character": str(d.get("character") or subj)[:80],
                        "line": str(d.get("line") or d.get("text"))[:2000],
                    }
                )
    return [
        {
            "id": "minimal_0",
            "duration": 6.0,
            "shot_type": "medium",
            "camera": "static",
            "subject": subj,
            "action": summ[:900],
            "location": "",
            "summary": f"{summ}\n[干预]{intent[:500]}",
            "dialogue": dialogue,
            "voice_over": "",
        }
    ]


def _log(job: models.GenerationJob, stage: str, message: str, **payload) -> None:
    job.timeline_log = (job.timeline_log or []) + [
        {"ts": datetime.utcnow().isoformat(), "stage": stage, "message": message, "data": payload}
    ]


def _fail_intervention_job(
    job: models.GenerationJob,
    intervention: models.Intervention,
    exc: Optional[BaseException],
    *,
    stage: str,
) -> InterventionResult:
    """模型或下游调用失败：不写占位剧情，统一返回用户可读错误。"""
    if exc is not None:
        logger.exception("干预失败 stage=%s", stage, exc_info=exc)
    detail = str(exc).strip()[:3500] if exc else ""
    job.status = "failed"
    job.error = (
        f"{INTERVENTION_MODEL_FAILURE_MESSAGE}\n详情（{stage}）：{detail}" if detail else INTERVENTION_MODEL_FAILURE_MESSAGE
    )[:4000]
    job.finished_at = datetime.utcnow()
    intervention.status = "failed"
    _log(job, stage, "模型或流水线失败", error=detail or (type(exc).__name__ if exc else ""))
    return InterventionResult(
        {
            "status": "failed",
            "job_id": job.id,
            "error": INTERVENTION_MODEL_FAILURE_MESSAGE,
        }
    )


def _reconcile_generation_job(db: Session, job: models.GenerationJob) -> None:
    """并发 purge 可能删掉 ``generation_jobs`` 行；flush 异常后会话处于 rollback-only，禁止继续写库。

    若行缺失则用 ``merge`` 回补（避免 ``expunge``+``add`` 破坏会话 identity，诱发后续 FK 失败）。
    """

    try:
        db.flush()
    except Exception:
        return

    jid = job.id
    try:
        row_id = db.scalar(select(models.GenerationJob.id).where(models.GenerationJob.id == jid))
    except PendingRollbackError:
        logger.warning("generation_jobs 调和跳过：会话待 rollback（常见于上游 flush 外键失败）")
        return

    if row_id is None:
        logger.warning(
            "generation_jobs id=%s 在库中缺失，尝试 merge 回补当前会话状态",
            jid[:12],
        )
        try:
            db.merge(job)
            db.flush()
        except Exception as exc:
            logger.warning("generation_jobs merge 回补失败（忽略）：%s", exc)


def _scene_context_for_video_prompts(
    db: Session,
    *,
    timeline_obj: models.Timeline,
    intervention: models.Intervention,
    branch: Dict,
    video: Optional[models.VideoAsset],
) -> str:
    """生视频/生图提示词共用的场景块：成片名、播放点、当前镜头摘要、分支梗概、叙事时空。"""

    parts: List[str] = []
    if video and (video.title or "").strip():
        parts.append(f"成片：{str(video.title).strip()[:220]}")
    pt = float(intervention.play_time or 0.0)
    m = int(pt // 60)
    s = int(pt % 60)
    parts.append(f"发起干预时放映进度约 {pt:.1f}s（约 {m}:{s:02d}），新片段须与该时点前后叙事衔接。")
    try:
        blurb = story_state.shot_blurb_for_timeline_at_play_time(
            db, timeline_id=timeline_obj.id, play_time=pt
        )
    except Exception:  # noqa: BLE001
        blurb = ""
    if blurb:
        parts.append(f"该进度附近画面与对白上下文（必须承接，勿跳戏）：{blurb[:1100]}")
    summ = branch.get("summary")
    if summ:
        parts.append(f"分支剧情摘要：{str(summ)[:700]}")
    outline = branch.get("outline") or []
    if isinstance(outline, list) and outline:
        olines = []
        for i, item in enumerate(outline[:6]):
            olines.append(str(item).strip()[:200])
        if olines:
            parts.append("分支段落：" + "｜".join(olines))
    st = story_state.latest_state(db, timeline_obj.id)
    if st and st.location_time and isinstance(st.location_time, dict):
        loc = st.location_time.get("location") or ""
        tm = st.location_time.get("time") or ""
        if loc or tm:
            parts.append(f"当前剧情时空：{loc or '未标注地点'}，{tm or '未标注时间'}。")
    return "\n".join(parts)[:5000]


def detect_intent(content: str) -> str:
    payload = get_llm().chat_json(
        [
            {
                "role": "system",
                "content": (
                    "<task>intent_classify</task>"
                    "你是意图分类器。判断用户输入属于 qa（剧情问答）还是 intervention（剧情干预）。"
                    "返回 JSON：{intent, confidence, reason}。"
                ),
            },
            {"role": "user", "content": content},
        ]
    )
    intent = payload.get("intent") or "qa"
    if intent not in ("qa", "intervention"):
        intent = "qa"
    return intent


def run_intervention(
    db: Session,
    *,
    timeline_obj: models.Timeline,
    intervention: models.Intervention,
    profile: str = "fast",
) -> InterventionResult:
    """同步驱动一次干预的全流程。返回新时间线 id 和 job 元数据。"""

    started = time.monotonic()

    job = models.GenerationJob(
        id=str(uuid.uuid4()),
        intervention_id=intervention.id,
        timeline_id=timeline_obj.id,
        new_timeline_id=None,
        status="running",
        profile=profile,
        plan={},
        timeline_log=[],
        reuse_segments=[],
        generated_segments=[],
        estimated_seconds=0.0,
        cost_estimate=0.0,
        started_at=datetime.utcnow(),
    )
    db.add(job)
    db.flush()

    state = story_state.latest_state(db, timeline_obj.id)
    summary = story_state.state_summary_text(state)
    shot_analyses = story_state.shot_analyses_text_for_intervention(
        db,
        video_id=timeline_obj.video_id,
        play_time=intervention.play_time,
    )
    char_cat = character_catalog_for_intervention(db, timeline_obj.video_id)
    video_asset = db.get(models.VideoAsset, timeline_obj.video_id)

    # 1. 安全
    safety: SafetyDecision = safety_evaluate(db, intervention.user_text)
    db.add(
        models.SafetyLog(
            id=str(uuid.uuid4()),
            intervention_id=intervention.id,
            raw_text=intervention.user_text,
            decision=safety.decision,
            matched_policy=safety.matched_policy,
            rewrite_text=safety.rewrite_text,
            reason=safety.reason,
        )
    )
    intervention.safety_decision = safety.decision
    intervention.safety_reason = safety.reason
    intervention.normalized_text = safety.rewrite_text or intervention.user_text
    _log(job, "safety", "安全审查完成", decision=safety.decision, reason=safety.reason)

    if safety.decision == "reject":
        job.status = "failed"
        job.error = safety.reason or "安全策略拦截"
        intervention.status = "rejected"
        intervention.feasibility_level = "L0"
        job.finished_at = datetime.utcnow()
        _reconcile_generation_job(db, job)
        return InterventionResult(
            {"status": "rejected", "job_id": job.id, "reason": safety.reason}
        )

    # 2. 可执行性
    feas: FeasibilityDecision = classify_feasibility(intervention.normalized_text, summary)
    intervention.feasibility_level = feas.level
    intervention.rationale = feas.rationale
    _log(job, "feasibility", "可执行性分级", level=feas.level, rationale=feas.rationale)

    if feas.level == "L1":
        job.status = "done"
        job.finished_at = datetime.utcnow()
        intervention.status = "qa_only"
        _reconcile_generation_job(db, job)
        return InterventionResult({"status": "qa_only", "job_id": job.id, "level": "L1"})

    try:
        # 3. 编剧
        play_t = float(intervention.play_time or 0.0)
        branch = screenwriter.write_branch(
            story_summary=summary,
            intent=intervention.normalized_text,
            feasibility_level=feas.level,
            shot_analyses=shot_analyses,
            character_catalog=char_cat,
            apply_context=(
                f"干预发生在原片时间约 {play_t:.1f}s（用户当前播放点）。"
                "后续 outline 与对白须从该时刻「已经演到的剧情」自然延伸，保持人物动机与场景线索连续。"
            ),
        )
        _log(job, "screenwriter", "编剧完成", outline=branch.get("outline"))
    
        # 4. 导演
        plan = director.plan_shots(
            story_summary=summary,
            branch_outline=branch.get("outline") or [],
            intent=intervention.normalized_text,
            shot_analyses=shot_analyses,
            character_catalog=char_cat,
        )
        _log(job, "director", "导演完成", decision=plan.get("decision"))
    
        # 5. 分镜（LLM → 导演计划 → 编剧 outline → 单镜兜底，保证始终有可生成镜头）
        storyboard_payload = storyboard.make_storyboard(
            plan=plan,
            dialogues=branch.get("dialogues") or [],
            shot_analyses=shot_analyses,
            character_catalog=char_cat,
        )
        shots = storyboard_payload.get("shots") or []
        shot_fallback: Optional[str] = None
    
        if not settings.intervention_no_fallback:
            if not shots:
                fb = _fallback_shots_from_director_plan(plan)
                if fb:
                    shots = fb
                    shot_fallback = "director_plan"
                    _log(
                        job,
                        "storyboard",
                        "分镜 LLM 未返回 shots，已用导演 shots_plan 兜底",
                        count=len(shots),
                    )
    
            if not shots:
                fb2 = _fallback_shots_from_branch(branch, intervention.normalized_text)
                if fb2:
                    shots = fb2
                    shot_fallback = "branch_outline"
                    _log(
                        job,
                        "storyboard",
                        "导演 shots_plan 为空，已用编剧 outline 构造分镜",
                        count=len(shots),
                    )
    
            if not shots:
                shots = _fallback_shots_minimal(
                    intent=intervention.normalized_text,
                    branch=branch,
                )
                shot_fallback = "minimal"
                _log(job, "storyboard", "仍无分镜，已用干预意图+分支摘要最小兜底", count=len(shots))
    
        if not shots:
            return _fail_intervention_job(
                job,
                intervention,
                RuntimeError("分镜为空（模型未返回可用 shots，且未启用分镜兜底）"),
                stage="分镜",
            )
    
        _log(
            job,
            "storyboard",
            "分镜完成",
            count=len(shots),
            fallback=shot_fallback or "llm",
        )
    
        st_for_loc = story_state.latest_state(db, timeline_obj.id)
        loc_hint = ""
        if st_for_loc and getattr(st_for_loc, "location_time", None) and isinstance(
            st_for_loc.location_time, dict
        ):
            loc_hint = str(st_for_loc.location_time.get("location") or "").strip()
        for sh in shots:
            if isinstance(sh, dict):
                generated_shot_catalog.enrich_storyboard_shot(sh, default_location=loc_hint)
    
        # 6. 风险评估
        risk_assess = risk.assess(shots=shots, profile=profile)
        job.estimated_seconds = risk_assess.estimated_seconds
        job.cost_estimate = risk_assess.estimated_cost
        job.plan = {
            "branch": branch,
            "director": plan,
            "shots": shots,
            "shots_count": len(shots),
            "shot_analyses_used": bool((shot_analyses or "").strip()),
            "character_catalog_used": bool((char_cat or "").strip()),
            "shot_fallback": shot_fallback,
            "cost_plan": risk_assess.cost_plan.items,
            "risk_notes": risk_assess.risk_notes,
        }
        _log(
            job,
            "risk",
            "风险评估",
            seconds=risk_assess.estimated_seconds,
            cost=risk_assess.estimated_cost,
            notes=risk_assess.risk_notes,
        )
    
        intervention.estimated_gen_seconds = risk_assess.estimated_seconds
    
        # 7. 复用检索：每个镜头优先尝试复用
        reused_for_log: List[Dict] = []
        shots_to_generate: List[Dict] = []
        for s in shots:
            characters = []
            if s.get("dialogue"):
                characters = [d.get("character") for d in s["dialogue"] if d.get("character")]
            if not characters and s.get("subject"):
                characters = [s["subject"]]
            candidates = cache.find_reusable_shots(
                db,
                video_id=timeline_obj.video_id,
                characters=characters,
                location=s.get("location"),
                actions=[s.get("action")] if s.get("action") else [],
                keywords=[s.get("mood")] if s.get("mood") else [],
                limit=1,
                min_score=2.5,
            )
            if candidates:
                top = candidates[0]
                reused_for_log.append(
                    {
                        "shot_id": top.shot.id,
                        "file_path": top.shot.file_path,
                        "audio_path": top.shot.audio_path,
                        "duration": top.shot.duration,
                        "caption": top.shot.summary,
                        "reasons": top.reasons,
                        "score": top.score,
                    }
                )
            else:
                shots_to_generate.append(s)
    
        job.reuse_segments = reused_for_log
        _log(job, "reuse", "媒资库复用检索", reused=len(reused_for_log), need_generate=len(shots_to_generate))
    
        scene_ctx = _scene_context_for_video_prompts(
            db,
            timeline_obj=timeline_obj,
            intervention=intervention,
            branch=branch,
            video=video_asset,
        )
    
        # 8. 提示词 + 配音 + 生成
        generated_for_log: List[Dict] = []
        actual_cost = 0.0
        if not shots_to_generate:
            if not shots:
                _log(
                    job,
                    "generation",
                    "未生成：分镜仍为空（不应出现，已检查兜底链）",
                    generated=0,
                )
            elif reused_for_log:
                _log(
                    job,
                    "generation",
                    "跳过生成：镜头已由媒资库复用",
                    generated=0,
                    reused=len(reused_for_log),
                )
            else:
                _log(job, "generation", "无待生成镜头", generated=0)
        else:
            for s in shots_to_generate:
                # 便于解析「绑定哪个角色」：无 characters 时从对白汇总角色名，与角色库匹配
                if not s.get("characters") and s.get("dialogue"):
                    chs: List[str] = []
                    for d in s.get("dialogue") or []:
                        if isinstance(d, dict) and d.get("character"):
                            c = str(d.get("character")).strip()
                            if c and c not in chs:
                                chs.append(c)
                    if chs:
                        s["characters"] = chs
                prompts = prompt.shot_to_prompts(
                    shot=s,
                    character_catalog=char_cat or "",
                    scene_context=scene_ctx,
                    video_title=(video_asset.title or "") if video_asset else "",
                )
                # 配音
                lines: List[Dict] = []
                if s.get("voice_over"):
                    lines.append({"character": "旁白", "line": s["voice_over"]})
                if s.get("dialogue"):
                    lines.extend(s["dialogue"])
                voice_text = " / ".join(
                    (line.get("line") or line.get("text") or "")
                    for line in lines
                    if (line.get("line") or line.get("text"))
                )
                clip_intro = generated_shot_catalog.build_generated_clip_intro(
                    s,
                    prompts=prompts,
                    voice_text=voice_text,
                )
                # 生成视频（无兜底模式下占位片会抛错，在此收尾为失败任务）
                try:
                    generated = generator.generate_shot(
                        db,
                        job_id=job.id,
                        video_id=timeline_obj.video_id,
                        shot=s,
                        prompts=prompts,
                        voice_text=voice_text,
                        profile=profile,
                        clip_brief=clip_intro,
                        forbid_placeholder=True,
                    )
                except Exception as exc:
                    return _fail_intervention_job(job, intervention, exc, stage="镜头生成")
                if generated.fallback:
                    return _fail_intervention_job(
                        job,
                        intervention,
                        RuntimeError("视频生成为占位片段，请检查视频模型配置与密钥"),
                        stage="镜头生成",
                    )
                sp = s if isinstance(s, dict) else {}
                characters_list = sp.get("characters")
                if not isinstance(characters_list, list):
                    characters_list = []
                generated_for_log.append(
                    {
                        "file_path": generated.file_path,
                        "audio_path": generated.audio_path,
                        "duration": generated.duration,
                        "cost": generated.cost,
                        "fallback": generated.fallback,
                        "video_model": getattr(generated, "video_model", "") or "",
                        "caption": clip_intro,
                        "brief": clip_intro,
                        "prompts": prompts,
                        "voice_text": voice_text,
                        # 供媒资库镜头结构化回显（与 VLM 分析字段对齐）
                        "shot_plan": {
                            "summary": (sp.get("summary") or "").strip(),
                            "subject": (sp.get("subject") or "").strip(),
                            "action": (sp.get("action") or "").strip(),
                            "location": (sp.get("location") or "").strip(),
                            "characters": characters_list,
                            "dialogue": sp.get("dialogue"),
                            "voice_over": (sp.get("voice_over") or "").strip(),
                        },
                    }
                )
                actual_cost += generated.cost
            _log(
                job,
                "generation",
                f"镜头生成完成（{len(generated_for_log)} 条）",
                count=len(generated_for_log),
            )
    
        job.generated_segments = generated_for_log
        if generated_for_log:
            n_cat = generated_shot_catalog.record_generated_clips_as_shots(
                db,
                video_id=timeline_obj.video_id,
                job_id=job.id,
                items=generated_for_log,
            )
            if n_cat:
                _log(
                    job,
                    "shot_catalog",
                    "已登记生成片段到媒资库镜头列表",
                    count=n_cat,
                )
    
        vgc = get_video_gen(db, profile)
        n_fb = sum(1 for x in generated_for_log if x.get("fallback"))
        if isinstance(job.plan, dict):
            job.plan = {
                **job.plan,
                "video_generation": {
                    "provider": vgc.provider,
                    "model": vgc.model,
                    "fallback_clip_count": n_fb,
                    "real_minimax": vgc.provider == "minimax" and n_fb == 0,
                },
            }
    
        apply_time, schedule_rationale, used_sched_llm = apply_schedule.decide_apply_time(
            play_time=intervention.play_time or 0.0,
            video_duration=float(video_asset.duration) if video_asset and video_asset.duration else 0.0,
            intervention_text=(intervention.normalized_text or intervention.user_text or ""),
            branch_summary=(branch or {}).get("summary") or "",
            shots=shots,
            generated_for_log=generated_for_log,
            risk_assess=risk_assess,
            strict_no_fallback=settings.intervention_no_fallback,
        )
        intervention.apply_time = apply_time
        if isinstance(job.plan, dict):
            job.plan = {
                **job.plan,
                "apply_schedule": {
                    "apply_time": apply_time,
                    "rationale": schedule_rationale,
                    "llm": used_sched_llm,
                },
            }
        _log(
            job,
            "schedule",
            "确定替换时间点",
            apply_time=apply_time,
            agent=used_sched_llm,
            note=(schedule_rationale or "")[:400],
        )
    
        # 9. 质检
        base_segments = timeline.get_segments(db, timeline_obj.id)
        prev_seg = next((seg for seg in base_segments if seg.start_time <= apply_time < seg.end_time), None)
        next_seg_index = (prev_seg.index + 1) if prev_seg else 0
        next_seg = base_segments[next_seg_index] if next_seg_index < len(base_segments) else None
    
        qa = qa_check.review(
            prev_segment=prev_seg,
            next_segment=next_seg,
            shots=shots,
            story_summary=summary,
        )
        job.continuity_score = qa.continuity.score
        job.safety_score = 1.0 if safety.decision == "allow" else 0.7
        job.quality_score = sum(g.get("cost", 0.0) for g in generated_for_log) and 0.85 or 0.8
        _log(job, "qa", "质检", passed=qa.passed, continuity=qa.continuity.score, notes=qa.notes)
    
        if not qa.passed and not reused_for_log and not generated_for_log:
            job.status = "failed"
            job.error = "干预失败：质检未通过且无可用生成或复用片段。"
            intervention.status = "failed"
            job.finished_at = datetime.utcnow()
            return InterventionResult(
                {
                    "status": "failed",
                    "job_id": job.id,
                    "error": job.error,
                }
            )
    
        # 10. 剪辑：装配 SegmentSpec
        specs = editor.assemble_specs(generated_shots=generated_for_log, reused_shots=reused_for_log)
    
        # 11. 时间线补丁
        new_timeline = timeline.fork_timeline(
            db,
            base=timeline_obj,
            label=f"分支 · {intervention.normalized_text[:18]}",
            branch_reason=intervention.normalized_text,
            apply_time=apply_time,
            new_segments=specs,
        )
        timeline.append_patch(
            db,
            intervention_id=intervention.id,
            from_timeline_id=timeline_obj.id,
            to_timeline_id=new_timeline.id,
            replace_start=apply_time,
            replace_end=apply_time + sum(s.duration for s in specs),
            transition_note=plan.get("bridge_strategy", ""),
            continuity_score=job.continuity_score,
            safety_score=job.safety_score,
            quality_score=job.quality_score,
        )
    
        # 12. 更新剧情状态
        story_state.append_state(
            db,
            timeline_id=new_timeline.id,
            time_point=apply_time,
            update={
                "current_event": branch.get("summary"),
                "summary": (summary or "") + "\n[分支]" + (branch.get("summary") or ""),
            },
        )
    
        job.new_timeline_id = new_timeline.id
        job.fallback_used = any(g.get("fallback") for g in generated_for_log)
        job.actual_cost = actual_cost
        job.actual_seconds = round(time.monotonic() - started, 2)
        job.status = "done"
        job.finished_at = datetime.utcnow()
        intervention.status = "applied"
    
        return InterventionResult(
            {
                "status": "applied",
                "job_id": job.id,
                "new_timeline_id": new_timeline.id,
                "apply_time": apply_time,
                "estimated_seconds": risk_assess.estimated_seconds,
                "actual_seconds": job.actual_seconds,
                "feasibility_level": feas.level,
                "safety_decision": safety.decision,
            }
        )
    except Exception as _intervention_exc:
        return _fail_intervention_job(
            job, intervention, _intervention_exc, stage="干预流水线"
        )
    finally:
        _reconcile_generation_job(db, job)
