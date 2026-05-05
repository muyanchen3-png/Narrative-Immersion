from __future__ import annotations

import json
import logging
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db, run_with_sqlite_lock_retry
from ..services import story_state
from ..services.intervention_context import character_catalog_for_intervention
from ..services.agents import orchestrator
from ..services.agents.orchestrator import INTERVENTION_MODEL_FAILURE_MESSAGE
from ..services.llm import get_llm
from ..services.llm_context import bind_llm, unbind_llm
from ..text_sanitize import strip_thinking_blocks

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)


def _store_message(
    db: Session,
    *,
    timeline_id: str,
    role: str,
    intent: str,
    play_time: float,
    content: str,
    metadata: dict | None = None,
) -> models.ChatMessage:
    msg = models.ChatMessage(
        id=str(uuid.uuid4()),
        timeline_id=timeline_id,
        role=role,
        intent=intent,
        play_time=play_time,
        content=content,
        metadata_json=metadata or {},
    )
    db.add(msg)
    db.flush()
    return msg


def _to_out(msg: models.ChatMessage) -> schemas.ChatMessageOut:
    return schemas.ChatMessageOut.model_validate(msg)


def _generate_qa_answer_text(
    db: Session,
    *,
    timeline: models.Timeline,
    play_time: float,
    user_content: str,
    profile: str,
) -> str:
    """生成解说正文（与 chat 路由中 qa 分支一致）。"""

    bind_llm(db, profile)
    try:
        state = story_state.latest_state(db, timeline.id)
        summary = story_state.state_summary_text(state)
        video = db.get(models.VideoAsset, timeline.video_id)
        seg_blurb = story_state.shot_blurb_for_timeline_at_play_time(
            db, timeline_id=timeline.id, play_time=play_time
        )
        vtitle = (video.title if video else "") or ""
        vdesc = (video.description if video else "") or ""
        ctx_trim = summary[:1200] if summary else ""
        char_cat = (character_catalog_for_intervention(db, timeline.video_id) or "").strip()
        cc_xml = (
            f"<character_catalog>{char_cat[:8000]}</character_catalog>" if char_cat else ""
        )
        answer = get_llm().chat(
            [
                {
                    "role": "system",
                    "content": (
                        "<task>qa</task>"
                        "你是互动叙事放映厅的解说助手。你虽无法看到真实像素，但**回答用户时**要像在聊剧情：直接说人物与情节，"
                        "**禁止**在句首或正文中说明信息来源或工作方式。尤其不要用：「根据角色库」「根据当前画面/片段」"
                        "「根据系统/摘要/资料」「根据用户备注」「据创作者备注」「备注里写道」"
                        "「从提供的信息来看」「作为 AI」等元话语；不要解释你是根据什么标签在推理。\n"
                        "创作者在角色卡写的后续走向（如某能力觉醒、伏笔）须**直接以剧情口吻**说出，例如「他日后会在机缘巧合下……」「这条线指向……」，"
                        "勿加「根据用户备注」式 attribution。\n"
                        "好的开头示例：直接「这位是楚凡，他是仙门里……」；差的开头：「根据角色库与当前画面，这位是……」。\n"
                        "你无法直接看到视频像素，只能依赖下列结构化文本进行内部推理（但输出中不要向用户重复这一点）。"
                        "禁止使用「目标A、事件B、第几个事件」等占位符或模板套话。\n"
                        "【角色库与用户备注】若存在 character_catalog，其中含创作者在角色卡填写的「用户备注」及身份摘要；"
                        "与自动镜头分析（current_segment）冲突时，人物设定与备注优先，但当前画面上「这一段在演什么」仍以 current_segment 为准。\n"
                        "【当前片段优先】标签 current_segment 描述的是播放进度所在片段的分析结果（与屏上字幕同源）。"
                        "用户问「这一段谁在做什么、画面上有什么」时：必须以 current_segment（及 video_title）为第一依据；"
                        "不得把 context 里的人物、性别、地点、职业剧情（如面试、街道）硬套到当前片段，"
                        "除非这些内容也能在 current_segment 或 video_description 中找到依据。\n"
                        "【全局摘要慎用】标签 context 是根据全片早期镜头自动整理的剧情记忆，可能与当前时刻不一致或过时。"
                        "仅当用户问「整部片的脉络、前后因果」时可简要参考；若与 current_segment 冲突，以 current_segment 为准并一笔带过矛盾。\n"
                        "【元数据可能出错】若 current_segment 与标题/常识明显不符（例如摘要写小狗但标题暗示玄幻打斗），"
                        "应说明「自动分析结果可能与画面不符」，建议用户在媒资库对该镜头「重新生成分析」或「音轨识别对白」，不要编造具体画面细节。\n"
                        "若摘要未包含用户所问细节，明确写「当前摘要未提及」，可请用户口述画面补充。\n"
                        f"<video_title>{vtitle}</video_title>"
                        f"<video_description>{vdesc[:1200]}</video_description>"
                        f"{cc_xml}"
                        f"<play_time>{play_time:.1f}</play_time>"
                        f"<current_segment>{seg_blurb}</current_segment>"
                        f"<context>{ctx_trim}</context>"
                    ),
                },
                {"role": "user", "content": user_content},
            ]
        )
    finally:
        unbind_llm()
    try:
        data = json.loads(answer) if answer.startswith("{") else {"answer": answer}
        raw = data.get("answer", answer)
    except Exception:
        raw = answer
    return strip_thinking_blocks(raw if isinstance(raw, str) else str(raw))


@router.post("/regenerate", response_model=schemas.ChatMessageOut)
def regenerate_assistant_answer(
    req: schemas.RegenerateAssistantRequest, db: Session = Depends(get_db)
) -> models.ChatMessage:
    timeline = db.get(models.Timeline, req.timeline_id)
    if not timeline:
        raise HTTPException(status_code=404, detail="时间线不存在")

    assistant = db.get(models.ChatMessage, req.assistant_message_id)
    if not assistant or assistant.timeline_id != req.timeline_id:
        raise HTTPException(status_code=404, detail="消息不存在")
    if assistant.role != "assistant" or assistant.intent != "qa":
        raise HTTPException(status_code=400, detail="仅支持重新生成「解说」类助手回复")

    user_prev = (
        db.query(models.ChatMessage)
        .filter(
            models.ChatMessage.timeline_id == req.timeline_id,
            models.ChatMessage.role == "user",
            models.ChatMessage.created_at < assistant.created_at,
        )
        .order_by(models.ChatMessage.created_at.desc())
        .first()
    )
    if not user_prev:
        raise HTTPException(status_code=400, detail="找不到该回复对应的用户提问")

    profile = req.profile or "fast"
    text = _generate_qa_answer_text(
        db,
        timeline=timeline,
        play_time=user_prev.play_time,
        user_content=user_prev.content,
        profile=profile,
    )
    assistant.content = strip_thinking_blocks(text)
    db.commit()
    db.refresh(assistant)
    logger.info(
        "解说重新生成完成 assistant=%s timeline=%s",
        req.assistant_message_id[:12],
        req.timeline_id[:12],
    )
    return assistant


@router.get("/{timeline_id}/messages", response_model=List[schemas.ChatMessageOut])
def list_messages(timeline_id: str, db: Session = Depends(get_db)) -> List[models.ChatMessage]:
    return (
        db.query(models.ChatMessage)
        .filter(models.ChatMessage.timeline_id == timeline_id)
        .order_by(models.ChatMessage.created_at)
        .all()
    )


@router.delete("/{timeline_id}/messages", response_model=schemas.ChatMessagesDeletedOut)
def clear_messages(timeline_id: str, db: Session = Depends(get_db)) -> schemas.ChatMessagesDeletedOut:
    timeline = db.get(models.Timeline, timeline_id)
    if not timeline:
        raise HTTPException(status_code=404, detail="时间线不存在")
    q = db.query(models.ChatMessage).filter(models.ChatMessage.timeline_id == timeline_id)
    deleted = q.delete(synchronize_session=False)
    db.commit()
    logger.info("已清除对话 timeline=%s deleted=%s", timeline_id[:12], deleted)
    return schemas.ChatMessagesDeletedOut(deleted=deleted)


@router.post("", response_model=schemas.ChatResponse)
def chat(req: schemas.ChatRequest, db: Session = Depends(get_db)) -> schemas.ChatResponse:
    timeline = db.get(models.Timeline, req.timeline_id)
    if not timeline:
        raise HTTPException(status_code=404, detail="时间线不存在")

    profile = req.profile or "fast"
    if req.confirm_intervention:
        intent = "intervention"
    else:
        bind_llm(db, profile)
        try:
            intent = req.force_intent or orchestrator.detect_intent(req.content)
        finally:
            unbind_llm()

    if (
        intent == "intervention"
        and not req.confirm_intervention
        and (req.force_intent or "") != "intervention"
    ):
        return schemas.ChatResponse(
            intent="intervention_confirm",
            needs_intervention_confirm=True,
        )

    user_msg = _store_message(
        db,
        timeline_id=timeline.id,
        role="user",
        intent=intent,
        play_time=req.play_time,
        content=req.content,
    )
    logger.info(
        "对话请求 intent=%s timeline=%s play=%.2fs profile=%s chars=%s",
        intent,
        timeline.id[:12],
        req.play_time,
        profile,
        len(req.content or ""),
    )

    if intent == "qa":
        answer_text = _generate_qa_answer_text(
            db,
            timeline=timeline,
            play_time=req.play_time,
            user_content=req.content,
            profile=profile,
        )
        assistant = _store_message(
            db,
            timeline_id=timeline.id,
            role="assistant",
            intent="qa",
            play_time=req.play_time,
            content=answer_text,
        )
        db.commit()
        return schemas.ChatResponse(
            intent="qa",
            user_message=_to_out(user_msg),
            assistant_message=_to_out(assistant),
        )

    intervention = models.Intervention(
        id=str(uuid.uuid4()),
        timeline_id=timeline.id,
        user_text=req.content,
        play_time=req.play_time,
    )
    db.add(intervention)
    db.flush()

    bind_llm(db, profile)
    try:
        result = orchestrator.run_intervention(
            db, timeline_obj=timeline, intervention=intervention, profile=profile
        )
    except Exception:
        db.rollback()
        raise
    finally:
        unbind_llm()

    # 先提交干预全流程（含 generation_jobs），再写入助手回复。
    # 否则单次超大 flush 可能触发 Session 对 generation_jobs 的 UPDATE 与 ChatMessage 交错，
    # 在 SQLite 并发或与陈旧实例叠加时出现「expected to update 1 row(s); 0 were matched」。
    try:
        run_with_sqlite_lock_retry(lambda: db.commit(), db=db)
    except Exception:
        db.rollback()
        raise

    if result.get("status") == "rejected":
        text = (
            f"该干预不符合内容规则，已为你拦截：{result.get('reason', '安全策略命中')}。\n"
            "你可以换一种表达，让剧情更安全有趣。"
        )
    elif result.get("status") == "qa_only":
        text = "该干预属于轻量影响，已记录但不会改变时间线。"
    elif result.get("status") == "applied":
        apply_time = result.get("apply_time", 0)
        text = (
            f"已为你创建分支剧情。剧情将在约第 {apply_time:.1f} 秒（"
            f"还有 {max(0.0, apply_time - req.play_time):.1f} 秒）发生变化。"
        )
    elif result.get("status") == "failed":
        text = result.get("error") or INTERVENTION_MODEL_FAILURE_MESSAGE
    else:
        text = "暂时无法生成新剧情，请稍后重试或换一种干预表达。"

    assistant = _store_message(
        db,
        timeline_id=timeline.id,
        role="assistant",
        intent="intervention",
        play_time=req.play_time,
        content=text,
        metadata={
            "job_id": result.get("job_id"),
            "new_timeline_id": result.get("new_timeline_id"),
            "feasibility_level": result.get("feasibility_level"),
            "safety_decision": result.get("safety_decision"),
            "apply_time": result.get("apply_time"),
            "estimated_seconds": result.get("estimated_seconds"),
            "actual_seconds": result.get("actual_seconds"),
            "status": result.get("status"),
        },
    )
    try:
        run_with_sqlite_lock_retry(lambda: db.commit(), db=db)
    except Exception:
        db.rollback()
        raise

    return schemas.ChatResponse(
        intent="intervention",
        user_message=_to_out(user_msg),
        assistant_message=_to_out(assistant),
        intervention_id=intervention.id,
        job_id=result.get("job_id"),
        new_timeline_id=result.get("new_timeline_id"),
        feasibility_level=result.get("feasibility_level"),
        safety_decision=result.get("safety_decision"),
        apply_time=result.get("apply_time"),
        estimated_seconds=result.get("estimated_seconds"),
        actual_seconds=result.get("actual_seconds"),
    )
