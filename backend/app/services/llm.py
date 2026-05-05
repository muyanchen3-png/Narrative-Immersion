"""LLM 提供商抽象：支持 OpenAI 兼容 API，缺省走 mock 规则引擎。"""

from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

import httpx

from ..config import settings
from ..text_sanitize import strip_thinking_blocks

logger = logging.getLogger(__name__)


def _normalize_openai_base_url(url: str) -> str:
    """界面若粘贴完整 …/v1/chat/completions，客户端会再拼 /chat/completions，此处去掉重复后缀。
    Ollama OpenAI 兼容根路径为 …/v1（最终请求 …/v1/chat/completions）；误填 …/api/chat 会得到 404。"""
    u = url.strip().rstrip("/")
    if u.endswith("/chat/completions"):
        u = u[: -len("/chat/completions")].rstrip("/")
    elif u.endswith("/chat/completion"):
        u = u[: -len("/chat/completion")].rstrip("/")
    # Ollama 原生接口是 /api/chat（非 OpenAI 形状）；兼容 Chat Completions 必须用 /v1
    if u.endswith("/api/chat"):
        root = u[: -len("/api/chat")].rstrip("/")
        u = root + "/v1"
    # 仅填 http://127.0.0.1:11434 未带 /v1 时，会变成 …/11434/chat/completions → 404
    try:
        parsed = urlparse(u if "://" in u else f"http://{u}")
        if parsed.port == 11434 and parsed.scheme in ("http", "https"):
            path = (parsed.path or "").rstrip("/")
            if path != "/v1":
                u = urlunparse((parsed.scheme, parsed.netloc, "/v1", "", "", "")).rstrip("/")
    except Exception:
        pass
    return u


_OLLAMA_404_HINT_SHOWN = False


class LLMClient:
    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        self.provider = provider or settings.llm_provider
        self.model = model or settings.llm_model
        raw_base = _normalize_openai_base_url(base_url) if base_url else _normalize_openai_base_url(settings.llm_base_url)
        self.base_url = raw_base
        self.api_key = api_key or settings.llm_api_key

    def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        json_mode: bool = False,
        temperature: float = 0.4,
        max_tokens: int = 1024,
    ) -> str:
        str_only = all(isinstance(m.get("content"), str) for m in messages)
        real = self.provider not in ("mock",) and self.api_key
        if (not real) and str_only:
            return _mock_chat(messages, json_mode=json_mode)  # type: ignore[arg-type]
        if not real:
            str_msgs = _messages_to_mock_shape(messages)
            return _mock_chat(str_msgs, json_mode=json_mode)
        return self._openai_chat(messages, json_mode=json_mode, temperature=temperature, max_tokens=max_tokens)

    def chat_json(self, messages: List[Dict[str, Any]], **kw) -> Dict[str, Any]:
        text = self.chat(messages, json_mode=True, **kw)
        return _safe_parse_json(text)

    def chat_json_multimodal(self, messages: List[Dict[str, Any]], **kw: Any) -> Dict[str, Any]:
        """OpenAI 兼容多模态 messages（content 可为文本块 + image_url），要求模型输出 JSON。"""
        text = self._openai_chat(
            messages,
            json_mode=True,
            temperature=float(kw.get("temperature", 0.35)),
            max_tokens=int(kw.get("max_tokens", 2048)),
        )
        return _safe_parse_json(text)

    def _openai_chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        json_mode: bool,
        temperature: float,
        max_tokens: int,
    ) -> str:
        global _OLLAMA_404_HINT_SHOWN
        url = self.base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(url, headers=headers, json=payload)
                # 部分本地网关（含旧版 Ollama）对 response_format=json_object 返回 4xx，去掉后重试一次
                if resp.status_code >= 400 and json_mode and "response_format" in payload:
                    payload_retry = {k: v for k, v in payload.items() if k != "response_format"}
                    resp = client.post(url, headers=headers, json=payload_retry)
                if resp.status_code == 404 and ":11434" in url:
                    if not _OLLAMA_404_HINT_SHOWN:
                        _OLLAMA_404_HINT_SHOWN = True
                        logger.warning(
                            "Ollama 兼容接口返回 404：请确认已安装并运行 Ollama（建议 ≥0.3），"
                            "且 HERMES_*_BASE_URL 为 http://127.0.0.1:11434/v1；"
                            "本机可执行 curl -sS http://127.0.0.1:11434/v1/models 检查。"
                        )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM 请求失败，回退到 mock：%s", exc)
            # 多模态 message 无法走 mock 文本引擎时直接抛出更清晰的日志
            str_msgs = _messages_to_mock_shape(messages)
            return _mock_chat(str_msgs, json_mode=json_mode)


def _messages_to_mock_shape(messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            out.append({"role": m.get("role", "user"), "content": c})
        else:
            out.append({"role": m.get("role", "user"), "content": json.dumps(c, ensure_ascii=False)})
    return out


def minimax_coding_plan_vlm_url(base_url: str) -> str:
    """由 OpenAI 兼容 base（如 https://api.minimaxi.com/v1）推导 MiniMax 配图理解接口。"""
    u = base_url.strip().rstrip("/")
    if u.endswith("/chat/completions"):
        u = u[: -len("/chat/completions")]
    if "://" not in u:
        u = "https://" + u
    parsed = urlparse(u)
    host = parsed.netloc
    scheme = parsed.scheme or "https"
    return urlunparse((scheme, host, "/v1/coding_plan/vlm", "", "", ""))


def is_minimax_endpoint(base_url: str) -> bool:
    return "minimax" in (base_url or "").lower()


def minimax_vlm_describe_frame(
    *,
    base_url: str,
    api_key: str,
    prompt: str,
    jpeg_path: str,
) -> str:
    """调用 MiniMax /v1/coding_plan/vlm（配图 + 文本提示），返回模型文本 content。"""
    data_uri = image_file_to_data_uri(jpeg_path)
    url = minimax_coding_plan_vlm_url(base_url)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"prompt": prompt, "image_url": data_uri}
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    base_resp = data.get("base_resp") or {}
    code = base_resp.get("status_code")
    if code is not None and int(code) != 0:
        msg = base_resp.get("status_msg", "MiniMax VLM 错误")
        raise RuntimeError(f"{msg} (code={code})")
    content = data.get("content")
    if not isinstance(content, str):
        return json.dumps(data, ensure_ascii=False)
    return content


def image_file_to_data_uri(jpeg_path: str) -> str:
    raw = Path(jpeg_path).read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def _safe_parse_json(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    text = strip_thinking_blocks(text.strip())
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).rstrip("`").strip()
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                return {}
    return {}


def _mock_chat(messages: List[Dict[str, str]], *, json_mode: bool) -> str:
    """根据系统/用户提示中的 task 标签返回结构化的规则化响应。

    Mock 引擎不是为了模拟真 LLM 智力，而是让整套流水线在无 API key 情况下也能完整跑通：
    每个 prompt 模板都会把 task 标签写进 system message 中，mock 引擎据此返回稳定结构。
    """

    sys = next((m for m in messages if m.get("role") == "system"), {}).get("content", "")
    user = next((m for m in reversed(messages) if m.get("role") == "user"), {}).get("content", "")
    task = _extract_tag(sys, "task")

    if task == "intent_classify":
        return _mock_intent(user, json_mode)
    if task == "qa":
        return _mock_qa(user, sys, json_mode)
    if task == "screenwriter":
        return _mock_screenwriter(user, sys, json_mode)
    if task == "director":
        return _mock_director(user, sys, json_mode)
    if task == "storyboard":
        return _mock_storyboard(user, sys, json_mode)
    if task == "prompt":
        return _mock_prompt(user, sys, json_mode)
    if task == "voice_lines":
        return _mock_voice_lines(user, sys, json_mode)
    if task == "story_state":
        return _mock_story_state(user, sys, json_mode)
    if task == "video_description":
        return _mock_video_description(user, sys, json_mode)
    if task == "shot_analysis":
        return _mock_shot_analysis(user, sys, json_mode)
    if task == "character_dedup":
        return _mock_character_dedup(user, sys, json_mode)
    if task == "feasibility":
        return _mock_feasibility(user, sys, json_mode)
    if task == "continuity_check":
        return _mock_continuity(user, sys, json_mode)
    if task == "predict_branches":
        return _mock_predict_branches(user, sys, json_mode)
    if task == "apply_schedule":
        return _mock_apply_schedule(user, sys, json_mode)

    if json_mode:
        return json.dumps({"text": "（mock）已接收任务，准备后续处理。"}, ensure_ascii=False)
    return "（mock）系统当前以演示模式运行，未配置真实 LLM。"


def _extract_tag(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}>([\s\S]*?)</{tag}>", text)
    return m.group(1).strip() if m else ""


def _mock_intent(user: str, json_mode: bool) -> str:
    intervention_keywords = [
        "让", "改成", "如果", "假如", "要是", "我想", "希望", "干预",
        "去做", "改变", "重写", "替换", "改", "做", "重新",
    ]
    qa_keywords = ["为什么", "是谁", "在哪", "什么", "怎么", "如何", "讲了"]
    text = user.strip()
    intent = "qa"
    if any(k in text for k in intervention_keywords) and not any(text.startswith(k) for k in qa_keywords):
        intent = "intervention"
    if any(k in text for k in qa_keywords) and not any(k in text for k in ["让", "改", "做"]):
        intent = "qa"
    payload = {"intent": intent, "confidence": 0.85, "reason": "基于关键词的 mock 判定"}
    return json.dumps(payload, ensure_ascii=False) if json_mode else payload["intent"]


def _mock_qa(user: str, sys: str, json_mode: bool) -> str:
    """无 API Key 时的解说兜底：只拼接已有摘要，禁止占位符套话。"""
    summary = _extract_tag(sys, "context")
    title = _extract_tag(sys, "video_title")
    desc = _extract_tag(sys, "video_description")
    seg = _extract_tag(sys, "current_segment")
    play_t = _extract_tag(sys, "play_time")
    user_q = user.strip()

    identity_kw = ("你是谁", "你是什么", "什么助手", "什么模型", "介绍一下你")
    if any(k in user_q for k in identity_kw):
        lines = [
            "我是叙境放映厅里的剧情解说：只根据当前这条成片的摘要与镜头信息回答你的问题，不编造摘要里没有的细节。",
        ]
        if title:
            lines.append(f"你正在看的成片标题是「{title[:120]}」。")
        if desc:
            lines.append(f"简介：{desc[:400]}{'…' if len(desc) > 400 else ''}")
        if summary:
            lines.append(f"剧情摘要摘录：{summary[:450]}{'…' if len(summary) > 450 else ''}")
        elif seg:
            lines.append(f"当前播放位置附近的镜头说明：{seg[:450]}")
        else:
            lines.append("本片剧情摘要尚在生成或为空，可先描述你看到的画面再问。")
        text = "\n".join(lines)
    else:
        if not summary.strip() and not seg.strip():
            text = (
                "当前没有可用的剧情摘要或镜头说明（可能成片仍在处理）。"
                "请稍后再问，或先用一句话描述你看到的画面，我可以结合后续生成的摘要一起解读。"
            )
        else:
            chunks: List[str] = []
            if title:
                chunks.append(f"「{title[:80]}」")
            if play_t:
                chunks.append(f"你提问时大致在 {play_t} 秒附近。")
            if seg:
                chunks.append(f"该时段镜头：{seg[:420]}{'…' if len(seg) > 420 else ''}")
            if summary:
                chunks.append(f"整体剧情摘要：{summary[:520]}{'…' if len(summary) > 520 else ''}")
            chunks.append(
                f"关于「{user_q[:80]}」：以上内容摘自系统为本片生成的摘要；若仍无法具体回答，说明摘要里尚未写到这一点，并非剧情里没有。"
            )
            text = "\n".join(chunks)

    if json_mode:
        return json.dumps({"answer": text}, ensure_ascii=False)
    return text


def _mock_screenwriter(user: str, sys: str, json_mode: bool) -> str:
    intent = _extract_tag(sys, "intent") or user
    payload = {
        "summary": f"基于干预“{intent[:30]}”，调整后续剧情走向。",
        "outline": [
            "新事件：主角因用户干预改变行动路线。",
            "中段冲突：主角遇到新事件并做出选择。",
            "结尾过渡：剧情自然回到主线，并保留原片情感弧线。",
        ],
        "dialogues": [
            {"character": "男主", "line": "看来今天我必须先处理这件事。"},
            {"character": "旁白", "line": "命运的列车在此刻悄然换轨。"},
        ],
        "constraints_kept": ["不破坏角色基本设定", "保留主线情感冲突", "时间地点合理过渡"],
    }
    return json.dumps(payload, ensure_ascii=False)


def _mock_director(user: str, sys: str, json_mode: bool) -> str:
    payload = {
        "decision": "局部分支",
        "reuse_strategy": "优先复用原片中性反应镜头与街道空镜",
        "bridge_strategy": "用 1 个旁白 + 1 个反应特写过渡",
        "shots_plan": [
            {"role": "bridge", "duration": 3, "summary": "男主停下脚步，目光转向声音来源"},
            {"role": "new_event", "duration": 6, "summary": "男主决定先处理新事件"},
            {"role": "consequence", "duration": 6, "summary": "新事件引发情绪与剧情变化"},
            {"role": "merge_back", "duration": 4, "summary": "镜头回到主线情绪基调"},
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def _mock_storyboard(user: str, sys: str, json_mode: bool) -> str:
    plan = _extract_tag(sys, "plan")
    payload = {
        "shots": [
            {
                "id": "s1",
                "duration": 3,
                "shot_type": "close-up",
                "camera": "缓慢推进",
                "subject": "男主",
                "action": "停下脚步，皱眉",
                "location": "街道",
                "lighting": "傍晚自然光",
                "mood": "迟疑",
                "voice_over": "他听见了一阵微弱的呜咽声。",
                "dialogue": [],
            },
            {
                "id": "s2",
                "duration": 6,
                "shot_type": "medium",
                "camera": "跟随",
                "subject": "男主",
                "action": "蹲下查看小狗",
                "location": "巷口",
                "lighting": "暖色路灯",
                "mood": "怜悯",
                "voice_over": "",
                "dialogue": [{"character": "男主", "line": "别怕，我带你回家。"}],
            },
            {
                "id": "s3",
                "duration": 6,
                "shot_type": "wide",
                "camera": "缓慢拉远",
                "subject": "男主与小狗",
                "action": "抱起小狗向另一条路走去",
                "location": "街道",
                "lighting": "傍晚",
                "mood": "决断",
                "voice_over": "他知道，今天的面试已经赶不上了。",
                "dialogue": [],
            },
            {
                "id": "s4",
                "duration": 4,
                "shot_type": "close-up",
                "camera": "静止",
                "subject": "男主",
                "action": "回望面试方向，平静微笑",
                "location": "街口",
                "lighting": "黄昏",
                "mood": "释然",
                "voice_over": "也许，错过了一次面试，却遇见了另一段故事。",
                "dialogue": [],
            },
        ]
    }
    return json.dumps(payload, ensure_ascii=False)


def _mock_prompt(user: str, sys: str, json_mode: bool) -> str:
    shot = _extract_tag(sys, "shot") or user[:60]
    scene = _extract_tag(sys, "scene_context")
    title = _extract_tag(sys, "piece_title")
    env = f"{title}, {scene[:120]}, " if (title or scene) else ""
    payload = {
        "image_prompt": f"cinematic, {env}{shot}, soft warm light, environmental detail, 35mm film",
        "video_prompt": f"smooth camera, {env}{shot}, on-location continuity, natural motion, match scene context",
        "negative_prompt": "blurry, wrong location, anachronistic props, distorted face, watermark",
        "style_tokens": ["cinematic", "scene-consistent", "natural light"],
    }
    return json.dumps(payload, ensure_ascii=False)


def _mock_voice_lines(user: str, sys: str, json_mode: bool) -> str:
    payload = {
        "lines": [
            {"character": "旁白", "text": "他在城市的喧嚣中停下了脚步。", "emotion": "thoughtful"},
            {"character": "男主", "text": "别怕，我带你回家。", "emotion": "warm"},
        ]
    }
    return json.dumps(payload, ensure_ascii=False)


def _mock_video_description(user: str, sys: str, json_mode: bool) -> str:
    """无 API Key：从镜头罗列摘前几段拼成占位梗概。"""
    title = _extract_tag(sys, "title")
    lines = [ln.strip() for ln in user.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    snippet = " ".join(lines[:8])[:900]
    head = (title or "该片").strip()[:120]
    if not snippet:
        return f"（演示模式）「{head}」的镜头摘要列表为空，暂无剧情梗概。"
    return f"（演示模式梗概）「{head}」：{snippet}" + ("…" if len(snippet) >= 900 else "")


def _mock_story_state(user: str, sys: str, json_mode: bool) -> str:
    payload = {
        "current_event": "男主前往面试途中遇到一只受伤小狗",
        "previous_events": ["男主早上准备面试", "出门走向地铁"],
        "causal_links": [
            {"from": "男主早上准备面试", "to": "出门走向地铁", "relation": "因果"}
        ],
        "characters_state": {
            "男主": {"goal": "顺利通过面试", "emotion": "紧张", "knows": ["面试地点"]},
        },
        "world_rules": {"genre": "都市生活", "era": "现代", "supernatural": False},
        "location_time": {"location": "城市街道", "time": "傍晚", "weather": "晴"},
        "open_threads": ["面试是否成功"],
        "constraints": ["不出现违法行为", "不出现极端暴力", "保持角色性格一致"],
        "summary": "男主即将参加重要面试，路上有意料之外的事情发生。",
    }
    return json.dumps(payload, ensure_ascii=False)


def _mock_shot_analysis(user: str, sys: str, json_mode: bool) -> str:
    """Mock 未接入画面：不写编造摘要，summary 为空；前端展示「字幕失败」。"""
    payload = {
        "summary": None,
        "characters": [],
        "location": "",
        "actions": [],
        "dialogue": "",
        "emotion": "",
        "objects": [],
        "visual_style": {},
        "continuity_anchors": {},
        "tags": [],
        "visible_person_count": 0,
        "character_facings": [],
    }
    return json.dumps(payload, ensure_ascii=False)


def _mock_character_dedup(user: str, sys: str, json_mode: bool) -> str:
    """无 API 时：每个 name_key 单独成簇，不合并。"""
    try:
        data = json.loads(user)
    except Exception:
        return json.dumps({"clusters": []}, ensure_ascii=False)
    cands = data.get("candidates") or []
    clusters = [
        [str(c["name_key"])]
        for c in cands
        if isinstance(c, dict) and c.get("name_key")
    ]
    return json.dumps({"clusters": clusters}, ensure_ascii=False)


def _mock_feasibility(user: str, sys: str, json_mode: bool) -> str:
    text = user
    level = "L3"
    rationale = "默认按局部分支处理。"
    if any(k in text for k in ["黄色", "色情", "毒品", "赌博", "杀人", "炸弹"]):
        level = "L0"
        rationale = "命中禁止类策略，不可执行。"
    elif any(k in text for k in ["全部改", "整部", "完全不同的故事"]):
        level = "L4"
        rationale = "影响后续整体剧情，需要新建时间线。"
    elif any(k in text for k in ["语气", "情绪", "风格"]):
        level = "L2"
        rationale = "仅影响表演风格，可作为轻量影响处理。"
    payload = {"level": level, "rationale": rationale}
    return json.dumps(payload, ensure_ascii=False)


def _mock_continuity(user: str, sys: str, json_mode: bool) -> str:
    payload = {
        "score": 0.86,
        "issues": [],
        "suggestions": [
            "保持男主灰色西装在新生成片段中一致",
            "新片段后接回原片时使用 0.5 秒淡入过渡",
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def _mock_predict_branches(user: str, sys: str, json_mode: bool) -> str:
    payload = {
        "branches": [
            {"label": "帮助路人", "summary": "男主停下来帮助一位陌生路人", "probability": 0.32},
            {"label": "意外事件", "summary": "男主遇到意外延误，错过面试", "probability": 0.28},
            {"label": "心境转变", "summary": "男主在路上重新思考自己想要的人生", "probability": 0.22},
        ]
    }
    return json.dumps(payload, ensure_ascii=False)


def _mock_apply_schedule(user: str, sys: str, json_mode: bool) -> str:
    """无 API：根据播放点与非兜底条数给出稳定接点（不与 risk 完全相同，便于区分走的是编排路径）。"""
    try:
        pt = float(_extract_tag(sys, "play_time") or 0)
    except ValueError:
        pt = 0.0
    try:
        dur = float(_extract_tag(sys, "video_duration") or 0)
    except ValueError:
        dur = 0.0
    try:
        n_nf = int(_extract_tag(sys, "non_fallback_count") or 1)
    except ValueError:
        n_nf = 1
    n_nf = max(1, n_nf)
    offset = 6.0 + min(30.0, n_nf * 4.0)
    t = pt + offset
    if dur and dur > 0:
        t = min(t, max(0.0, dur - 0.5))
    t = max(t, pt)
    payload = {
        "apply_time": t,
        "rationale": "（mock 编排）按用户播放点与非兜底镜头条数估计情绪缓冲后接入。",
    }
    return json.dumps(payload, ensure_ascii=False)


def get_llm() -> LLMClient:
    from .llm_context import get_llm_binding
    from .model_resolve import resolve_llm_client

    binding = get_llm_binding()
    if binding:
        db, profile, kind = binding
        return resolve_llm_client(db, profile, kind=kind)
    return LLMClient()
