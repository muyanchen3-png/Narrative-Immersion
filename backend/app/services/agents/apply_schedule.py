"""分叉接点（apply_time）编排：在生成完成后由智能体建议播放轴上的接入时刻。

规则：
- 仅当存在至少一条 **非兜底**（真实 API / 非占位）生成片段时，才调用 LLM 研判；
- 若全部为兜底占位、或没有任何生成片段（纯媒资复用），则 **不调用** 编排智能体，沿用风险模型的耗时估算接点。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Tuple

from ..llm import get_llm
from .risk import RiskAssessment

logger = logging.getLogger(__name__)


def _clamp_apply_time(t: float, play_time: float, video_duration: float) -> float:
    t = max(0.0, float(t))
    t = max(t, float(play_time or 0.0))
    if video_duration and video_duration > 0:
        t = min(t, max(0.0, float(video_duration) - 0.5))
    return t


def _heuristic_from_risk(play_time: float, video_duration: float, risk_assess: RiskAssessment) -> float:
    t = (play_time or 0.0) + risk_assess.suggested_apply_offset
    return _clamp_apply_time(t, play_time or 0.0, video_duration)


def decide_apply_time(
    *,
    play_time: float,
    video_duration: float,
    intervention_text: str,
    branch_summary: str,
    shots: List[Dict[str, Any]],
    generated_for_log: List[Dict[str, Any]],
    risk_assess: RiskAssessment,
    strict_no_fallback: bool = False,
) -> Tuple[float, str, bool]:
    """返回 ``(apply_time, rationale, used_llm)``。"""
    pt = float(play_time or 0.0)
    dur = float(video_duration or 0.0)

    non_fallback = [g for g in generated_for_log if not g.get("fallback")]
    all_fallback = bool(generated_for_log) and not non_fallback
    no_generation = not generated_for_log

    if no_generation:
        t = _heuristic_from_risk(pt, dur, risk_assess)
        return (
            t,
            "无新生成视频片段（可能全部为媒资复用），未调用编排智能体，沿用耗时估算接点。",
            False,
        )

    if all_fallback:
        t = _heuristic_from_risk(pt, dur, risk_assess)
        return (
            t,
            "生成结果均为兜底占位片段，不参与编排智能体决策，沿用耗时估算接点。",
            False,
        )

    # 编排智能体：仅依据「非兜底」生成条的叙事摘要与时长，不影响兜底条在时间线上的拼接顺序。
    nf_lines: List[str] = []
    for i, g in enumerate(non_fallback, start=1):
        brief = (g.get("brief") or g.get("caption") or "").strip()
        nf_lines.append(
            f"{i}. 时长≈{float(g.get('duration') or 0):.2f}s · "
            f"{brief[:420]}{'…' if len(brief) > 420 else ''}"
        )
    nf_blob = "\n".join(nf_lines) if nf_lines else ""
    shots_blob = json.dumps(shots[:12], ensure_ascii=False)[:6000] if shots else "[]"

    system = (
        "<task>apply_schedule</task>"
        "你是互动影视剪辑策划。根据用户干预意图、分支摘要、分镜与「已生成且非兜底的」镜头说明，"
        "决定主时间轴上**新剧情应在第几秒开始接入**（apply_time，单位秒）。"
        "接点应不早于用户发干预时的播放点，且必须落在正片时长内；需考虑情绪与节奏，而非简单加固定秒数。"
        "输出严格 JSON 对象，仅含键：apply_time (number), rationale (string, 中文一句)。"
        f"<play_time>{pt}</play_time>"
        f"<video_duration>{dur}</video_duration>"
        f"<non_fallback_count>{len(non_fallback)}</non_fallback_count>"
    )
    user = (
        f"【用户干预】\n{intervention_text[:2000]}\n\n"
        f"【分支摘要】\n{(branch_summary or '')[:2000]}\n\n"
        f"【非兜底生成片段】（编排仅依据这些；兜底占位不参与定接点）\n{nf_blob}\n\n"
        f"【分镜结构参考】\n{shots_blob}"
    )

    try:
        data = get_llm().chat_json(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.35,
            max_tokens=512,
        )
    except Exception as exc:  # noqa: BLE001
        if strict_no_fallback:
            raise
        logger.warning("编排智能体调用失败，回退估算：%s", exc)
        t = _heuristic_from_risk(pt, dur, risk_assess)
        return t, f"编排智能体不可用，已回退耗时估算接点：{exc}", False

    raw_t = data.get("apply_time")
    try:
        t = float(raw_t)
    except (TypeError, ValueError):
        t = _heuristic_from_risk(pt, dur, risk_assess)
        return t, "编排智能体返回的 apply_time 非法，已回退耗时估算接点。", True

    t = _clamp_apply_time(t, pt, dur)
    rationale = (data.get("rationale") or "由编排智能体确定接点。").strip()
    return t, rationale, True
