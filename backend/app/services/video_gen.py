"""图像/视频生成提供商抽象。

MiniMax 官方异步流程（见 https://platform.minimaxi.com 视频生成文档）：

1. ``POST {root}/v1/video_generation`` → ``task_id``
2. ``GET {root}/v1/query/video_generation?task_id=`` 轮询直至成功
3. ``GET {root}/v1/files/retrieve?file_id=`` 取得 ``download_url`` 并下载到本地

支持：文生视频、首帧图生视频（本地路径→Data URL）、主体参考（``S2V-01`` + ``subject_reference``）。

文生仅 ``prompt`` 时，官方 ``model`` 枚举**不含** ``MiniMax-Hailuo-2.3-Fast``（Fast 仅图生，见 T2V / I2V 文档）。

Mock：FFmpeg 合成占位片段（无密钥或失败时回退）。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from ..config import settings
from . import media
from .image_generate import _file_to_data_url, _normalize_minimax_host

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_DEFAULT_WAN_T2V = "wan2.6-t2v"


class DashScopeVideoAuthError(RuntimeError):
    """DashScope 文生视频返回 401：密钥/权限问题，更换 model 无法解决。"""


def _dashscope_wan_api_root(base_url: Optional[str]) -> str:
    """DashScope 异步视频合成接口用域名根路径；不能与 ``…/compatible-mode/v1`` 直接拼接（否则会 404）。

    设置里填的 compatible-mode 多用于 OpenAI 兼容对话；视频合成仍走 ``/api/v1/services/aigc/video-generation/...``，故只保留 scheme+host。
    """

    default = "https://dashscope.aliyuncs.com"
    raw = (base_url or default).strip().rstrip("/")
    if not raw:
        return default
    low = raw.lower()
    if "/compatible-mode" in low:
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
        if parsed.scheme and parsed.netloc:
            root = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
            logger.info(
                "视频 DashScope：已将 compatible-mode 基址还原为视频合成 API 根域名 %s",
                root,
            )
            return root
        return default
    return raw


def _is_dashscope_chat_only_model(model: Optional[str]) -> bool:
    """对话类模型 id 不可用于 DashScope 文生视频接口（未调用 API，直接跳过）。"""

    m = (model or "").strip()
    if not m:
        return False
    low = m.lower()
    return (
        "deepseek" in low
        or low.startswith("gpt-")
        or "claude" in low
        or "llama" in low
    )


def _dashscope_build_model_chain(
    *,
    video_model_candidates: Optional[List[str]],
    primary_model: str,
) -> List[str]:
    """合并库内多条配置中的 model 字段（保持顺序、去重），再在末尾追加内置兜底（若尚未出现）。"""

    raw = video_model_candidates if video_model_candidates else []
    if not raw:
        pm = (primary_model or "").strip()
        raw = [pm] if pm else []
    seen: set[str] = set()
    chain: List[str] = []
    for x in raw:
        t = (x or "").strip()
        if not t:
            continue
        k = t.lower()
        if k not in seen:
            seen.add(k)
            chain.append(t)
    if not chain:
        chain.append(_DEFAULT_WAN_T2V)
    elif _DEFAULT_WAN_T2V.lower() not in seen:
        chain.append(_DEFAULT_WAN_T2V)
    return chain


@dataclass
class GeneratedClip:
    file_path: str
    audio_path: Optional[str]
    duration: float
    cost: float
    model: str
    prompt: str
    fallback: bool


def _clamp_minimax_duration_seconds(duration: float) -> int:
    """将时长对齐到 MiniMax 支持的值（6s / 10s），兼顾 5s 这种会被拒的情况。"""
    raw = max(1.0, duration)
    x = int(round(raw))
    clamped = max(5, min(x, 10))
    # MiniMax 仅支持 6s 和 10s；5s 会被拒，向下取 6，向上取 10
    if clamped == 5:
        clamped = 6
    return clamped


def _canonical_minimax_video_model(raw: str) -> str:
    """
    将控制台/套餐文案里的简称映射为 ``POST /v1/video_generation`` 要求的 **MiniMax-*** 模型 id。
    （裸写 ``Hailuo-2.3-Fast`` 会 2013 incorrect model param）
    """
    s = (raw or "").strip()
    if not s:
        return "MiniMax-Hailuo-2.3"
    key = s.lower().replace(" ", "")
    aliases: dict[str, str] = {
        "hailuo-2.3-fast": "MiniMax-Hailuo-2.3-Fast",
        "hailuo-2.3": "MiniMax-Hailuo-2.3",
        "runway-gen3": "MiniMax-Hailuo-2.3",
        "minimax-hailuo-2.3-fast": "MiniMax-Hailuo-2.3-Fast",
        "minimax-hailuo-2.3": "MiniMax-Hailuo-2.3",
    }
    if key in aliases:
        fixed = aliases[key]
        if s != fixed:
            logger.info("MiniMax 视频 model 简称已映射：%r -> %r", s, fixed)
        return fixed
    return s


# 文生视频（T2V）官方枚举；与图生（I2V）不同，见文档 video-generation-t2v / video-generation-i2v
_T2V_MODELS_CANONICAL = frozenset(
    {
        "MiniMax-Hailuo-2.3",
        "MiniMax-Hailuo-02",
        "T2V-01-Director",
        "T2V-01",
    }
)


def _t2v_model_only(model_id: str) -> str:
    """纯文生时 ``model`` 不能使用 ``*-Fast``，否则 2013。"""
    m = (model_id or "").strip()
    for allowed in _T2V_MODELS_CANONICAL:
        if allowed.lower() == m.lower():
            return allowed
    ml = m.lower()
    if "fast" in ml and "hailuo" in ml:
        logger.info(
            "文生视频不接受 %s（MiniMax-Hailuo-2.3-Fast 仅用于图生）；已改用 MiniMax-Hailuo-2.3",
            m,
        )
        return "MiniMax-Hailuo-2.3"
    logger.warning("文生视频 model %s 不在官方 T2V 枚举内，已改用 MiniMax-Hailuo-2.3", m)
    return "MiniMax-Hailuo-2.3"


def _effective_minimax_resolution(model_id: str, configured: str) -> str:
    """Hailuo-2.3 系在 Token 套餐多为 768P；若仍配 1080P 易触发套餐不支持 1080p。"""
    c = (configured or "768P").strip() or "768P"
    mid = (model_id or "").lower()
    if "hailuo-2.3" in mid and c.upper() == "1080P":
        logger.info(
            "Hailuo-2.3 系模型在 Token 套餐通常为 768P 6s；已将 resolution 从 1080P 调整为 768P（"
            "若你的套餐支持 1080P，请在 .env 将 HERMES_VIDEO_MINIMAX_RESOLUTION 设为 768P 以外的可用档位并确认模型名）"
        )
        return "768P"
    return c


def _parse_video_model_fallback(raw: str) -> Optional[tuple[str, Optional[str]]]:
    """
    解析保底模型配置。返回 ``(model_id, resolution_override)``；
    ``resolution_override`` 为 ``None`` 表示沿用 ``HERMES_VIDEO_MINIMAX_RESOLUTION``。

    支持：``Hailuo-2.3-Fast-768P`` → Fast + 强制 768P（套餐文档常见写法）。
    """
    s = (raw or "").strip()
    if not s:
        return None
    key = s.lower().replace(" ", "").replace("_", "-")
    if key in ("hailuo-2.3-fast-768p", "minimax-hailuo-2.3-fast-768p"):
        return ("MiniMax-Hailuo-2.3-Fast", "768P")
    return (_canonical_minimax_video_model(s), None)


def _build_minimax_payload(
    *,
    text_prompt: str,
    dur_s: int,
    resolution_configured: str,
    model_raw: str,
    refs: List[str],
) -> tuple[dict, str]:
    """组装 ``POST /v1/video_generation`` 的 JSON；返回 ``(payload, 实际使用的 model 字段)``。"""
    mid = _canonical_minimax_video_model(model_raw)
    use_subject_api = "s2v" in mid.lower()
    res = _effective_minimax_resolution(mid, resolution_configured)
    payload: dict = {
        "prompt": text_prompt,
        "duration": dur_s,
        "resolution": res,
    }
    if use_subject_api and refs:
        imgs: List[str] = []
        for p in refs[:3]:
            imgs.append(_file_to_data_url(Path(p)))
        payload["model"] = mid
        payload["subject_reference"] = [{"type": "character", "image": imgs}]
    elif refs:
        payload["model"] = mid
        payload["first_frame_image"] = _file_to_data_url(Path(refs[0]))
    else:
        payload["model"] = _t2v_model_only(mid)
    sent = str(payload.get("model") or mid)
    return payload, sent


class VideoGenClient:
    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        video_model_candidates: Optional[List[str]] = None,
    ) -> None:
        self.provider = provider or settings.video_provider
        self.model = model or settings.video_model
        self.api_key = api_key or settings.video_api_key or settings.llm_api_key
        self.base_url = base_url or settings.video_base_url or settings.llm_base_url
        #: 来自 SQLite 多条 kind=video 配置的 model 字段（有序）；DashScope 路径下依次尝试，失败再用内置 Wan 兜底
        self.video_model_candidates = video_model_candidates
        # 常见误配：VIDEO_PROVIDER 仍为 mock，但已填 api.minimaxi.com 与密钥 → 自动走真实 MiniMax 视频 API
        _bu = (self.base_url or "").lower()
        _key = (self.api_key or "").strip()
        if self.provider == "mock" and _key and "minimaxi.com" in _bu:
            logger.info(
                "视频：检测到 MiniMax 域名与密钥，已从 mock 切换为 minimax（建议在 .env 设置 HERMES_VIDEO_PROVIDER=minimax）"
            )
            self.provider = "minimax"

    def generate(
        self,
        *,
        prompt: str,
        duration: float,
        title: str,
        subtitle: str = "",
        voice_text: str = "",
        dst: str,
        first_frame_image_path: Optional[str] = None,
        subject_reference_paths: Optional[List[str]] = None,
        forbid_placeholder: bool = False,
    ) -> GeneratedClip:
        Path(dst).parent.mkdir(parents=True, exist_ok=True)

        if self.provider == "minimax" and (self.api_key or "").strip():
            if settings.video_require_character_reference:
                has_ref = bool(
                    first_frame_image_path and Path(str(first_frame_image_path)).is_file()
                )
                if not has_ref:
                    for p in subject_reference_paths or []:
                        if p and Path(str(p)).is_file():
                            has_ref = True
                            break
                if not has_ref:
                    msg = (
                        "须角色库参照图才能生成；未提供有效参考图"
                    )
                    if forbid_placeholder:
                        raise RuntimeError(f"{msg}（已启用 HERMES_INTERVENTION_NO_FALLBACK，禁止占位片）")
                    logger.warning(
                        "已启用「须角色库参照图」：未提供有效参考图，跳过 MiniMax 调用，使用本地占位视频（避免与角色外观不一致）。"
                    )
                    return self._mock(
                        prompt=prompt,
                        duration=duration,
                        title=title,
                        subtitle=subtitle,
                        voice_text=voice_text,
                        dst=dst,
                    )
            try:
                return self._generate_minimax(
                    prompt=prompt,
                    duration=duration,
                    dst=dst,
                    first_frame_image_path=first_frame_image_path,
                    subject_reference_paths=subject_reference_paths,
                )
            except Exception as exc:  # noqa: BLE001
                if forbid_placeholder:
                    raise
                logger.warning("MiniMax 视频生成失败，回退占位：%s", exc)

        if self.provider == "deepseek" and (self.api_key or "").strip():
            try:
                return self._generate_deepseek(
                    prompt=prompt,
                    duration=duration,
                    title=title,
                    subtitle=subtitle,
                    voice_text=voice_text,
                    dst=dst,
                )
            except Exception as exc:  # noqa: BLE001
                if forbid_placeholder:
                    raise
                logger.warning("DeepSeek/DashScope 视频生成失败，回退占位：%s", exc)

        if forbid_placeholder:
            raise RuntimeError(
                "无法生成非占位视频：请配置 HERMES_VIDEO_PROVIDER=minimax 与有效 KEY、参照图，"
                "并确保 MiniMax 调用成功（已启用 HERMES_INTERVENTION_NO_FALLBACK）"
            )
        return self._mock(
            prompt=prompt,
            duration=duration,
            title=title,
            subtitle=subtitle,
            voice_text=voice_text,
            dst=dst,
        )

    def _generate_minimax(
        self,
        *,
        prompt: str,
        duration: float,
        dst: str,
        first_frame_image_path: Optional[str],
        subject_reference_paths: Optional[List[str]],
    ) -> GeneratedClip:
        root = _normalize_minimax_host(self.base_url or "https://api.minimaxi.com")
        headers = {"Authorization": f"Bearer {self.api_key}"}

        dur_s = _clamp_minimax_duration_seconds(duration)
        base_resolution = (settings.video_minimax_resolution or "768P").strip() or "768P"
        poll_iv = max(3.0, float(settings.video_minimax_poll_interval or 10.0))

        refs: List[str] = []
        for p in subject_reference_paths or []:
            sp = str(p).strip()
            if sp and Path(sp).is_file() and sp not in refs:
                refs.append(sp)
        ff = first_frame_image_path
        if ff and Path(str(ff)).is_file():
            sp = str(ff)
            if sp not in refs:
                refs.insert(0, sp)

        text_prompt = (prompt or "").strip()[:4000] or "电影感镜头，画面连贯，动作自然。"

        primary_model_raw = self.model or "MiniMax-Hailuo-2.3"
        attempts: List[tuple[str, str, str]] = [
            (primary_model_raw, base_resolution, "primary"),
        ]
        fb_spec = (settings.video_model_fallback or "").strip()
        fb_parsed = _parse_video_model_fallback(fb_spec)
        if fb_parsed:
            fm, fres_ov = fb_parsed
            res_fb = fres_ov if fres_ov is not None else base_resolution
            attempts.append((fm, res_fb, "fallback"))

        create_url = f"{root}/v1/video_generation"
        with httpx.Client(timeout=120.0) as client:
            for model_raw, res_cfg, label in attempts:
                try:
                    payload, sent_model = _build_minimax_payload(
                        text_prompt=text_prompt,
                        dur_s=dur_s,
                        resolution_configured=res_cfg,
                        model_raw=model_raw,
                        refs=refs,
                    )
                    r = client.post(create_url, headers=headers, json=payload)
                    r.raise_for_status()
                    body = r.json()
                    task_id = body.get("task_id")
                    if not task_id:
                        base = body.get("base_resp") or {}
                        if isinstance(base, dict):
                            task_id = base.get("task_id")
                    if not task_id:
                        logger.warning("MiniMax 创建任务响应无 task_id：%s", body)
                        raise RuntimeError("MiniMax 未返回 task_id")

                    file_id = self._minimax_poll(client, root, headers, str(task_id), poll_iv)
                    self._minimax_download_file(client, root, headers, file_id, dst)

                    cost = max(0.05, 0.08 * dur_s)
                    return GeneratedClip(
                        file_path=dst,
                        audio_path=None,
                        duration=float(dur_s),
                        cost=cost,
                        model=sent_model,
                        prompt=payload["prompt"][:2000],
                        fallback=False,
                    )
                except Exception as exc:
                    if label == "primary" and fb_parsed is not None:
                        logger.warning(
                            "MiniMax 视频主模型失败，启用保底 %s：%s",
                            fb_spec,
                            exc,
                        )
                        continue
                    raise

        raise RuntimeError("MiniMax 视频生成失败")

    def _dashscope_try_model_once(
        self,
        client: httpx.Client,
        *,
        root: str,
        headers: Dict[str, str],
        model_id: str,
        dur_s: int,
        text_prompt: str,
        dst: str,
        duration: float,
    ) -> GeneratedClip:
        """单次 DashScope 文生视频：创建任务 → 轮询 → 下载 ``video_url``。"""
        create_payload = {
            "model": model_id,
            "input": {
                "prompt": text_prompt,
            },
            "parameters": {
                "duration": dur_s,
            },
        }
        create_url = f"{root}/api/v1/services/aigc/video-generation/video-synthesis"
        r = client.post(create_url, headers=headers, json=create_payload)
        if r.status_code == 401:
            raise DashScopeVideoAuthError(
                "DashScope 文生视频 HTTP 401：请检查 API Key 与 endpoint 地域是否一致。"
            )
        if r.status_code >= 400:
            snippet = (r.text or "").strip().replace("\n", " ")[:900]
            hint = ""
            if r.status_code == 403:
                hint = (
                    " 常见原因：账号未开通 Wan 文生视频、欠费、或密钥地域与 endpoint 不一致"
                    "（北京 https://dashscope.aliyuncs.com 与新加坡 https://dashscope-intl.aliyuncs.com"
                    " 需与同地域创建的 API Key 配对）。"
                )
            elif r.status_code == 400 and "url error" in snippet.lower():
                hint = (
                    " 对话模型（如 deepseek-*）不可用文生视频；将尝试配置中的下一项或内置 Wan。"
                    " 并确认已发送 X-DashScope-Async: enable。"
                )
            raise RuntimeError(
                f"DashScope 创建视频任务失败 HTTP {r.status_code}: {snippet}{hint}"
            )
        body = r.json()
        task_id: Optional[str] = None
        if isinstance(body, dict) and isinstance(body.get("output"), dict):
            task_id = body["output"].get("task_id")
        if not task_id and isinstance(body, dict):
            task_id = body.get("task_id")
        if not task_id:
            logger.warning("DeepSeek/DashScope 创建任务响应无 task_id：%s", body)
            raise RuntimeError("DashScope 未返回 task_id")

        query_url = f"{root}/api/v1/tasks/{task_id}"
        poll_headers = {"Authorization": headers["Authorization"]}
        poll_iv = max(5.0, float(settings.video_minimax_poll_interval or 10.0))
        for attempt in range(120):
            if attempt > 0:
                time.sleep(poll_iv)
            try:
                qr = client.get(query_url, headers=poll_headers)
                qr.raise_for_status()
            except Exception as exc:
                logger.warning("DeepSeek 轮询请求失败（attempt %d）：%s", attempt, exc)
                continue
            qdata = qr.json()
            out = qdata.get("output") if isinstance(qdata, dict) else None
            st_raw = None
            if isinstance(out, dict):
                st_raw = out.get("task_status")
            if not st_raw and isinstance(qdata, dict):
                st_raw = qdata.get("task_status") or qdata.get("status")
            st = str(st_raw or "").strip().upper()
            if st == "SUCCEEDED":
                video_url: Optional[str] = None
                if isinstance(out, dict):
                    video_url = out.get("video_url") or out.get("video")
                if not video_url and isinstance(qdata, dict):
                    video_url = qdata.get("video_url")
                if not video_url:
                    raise RuntimeError("DashScope 任务成功但未返回 video_url")
                dl = client.get(str(video_url), follow_redirects=True, timeout=600.0)
                dl.raise_for_status()
                Path(dst).parent.mkdir(parents=True, exist_ok=True)
                Path(dst).write_bytes(dl.content)
                cost = 0.0
                return GeneratedClip(
                    file_path=dst,
                    audio_path=None,
                    duration=duration,
                    cost=cost,
                    model=model_id,
                    prompt=text_prompt,
                    fallback=False,
                )
            if st == "FAILED":
                err = "未知错误"
                if isinstance(out, dict):
                    err = (
                        out.get("message")
                        or out.get("error_message")
                        or out.get("code")
                        or err
                    )
                raise RuntimeError(f"DashScope 视频生成失败：{err}")
            if st in ("CANCELED", "UNKNOWN"):
                raise RuntimeError(f"DashScope 任务结束状态异常：{st_raw}")
            logger.debug("DeepSeek task %s status=%s", str(task_id)[:16], st_raw)

        raise TimeoutError("DeepSeek/DashScope 视频生成轮询超时")

    def _generate_deepseek(
        self,
        *,
        prompt: str,
        duration: float,
        title: str,
        subtitle: str,
        voice_text: str,
        dst: str,
    ) -> GeneratedClip:
        """DashScope 异步文生视频：优先按 SQLite 中多条 video 配置的 model 依次尝试，失败后使用内置 ``wan2.6-t2v``。"""
        root = _dashscope_wan_api_root(self.base_url)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }

        dur_s = max(5, min(int(round(duration)), 10))
        text_prompt = (prompt or "").strip()[:2000] or "电影感镜头，画面连贯，动作自然。"
        chain = _dashscope_build_model_chain(
            video_model_candidates=self.video_model_candidates,
            primary_model=self.model or "",
        )
        logger.info("DashScope 文生视频候选顺序（含兜底）：%s", chain)

        attempted_key: set[str] = set()
        last_exc: Optional[Exception] = None

        with httpx.Client(timeout=60.0) as client:
            for raw_mid in chain:
                if _is_dashscope_chat_only_model(raw_mid):
                    logger.info(
                        "DashScope 文生视频跳过对话类 model=%r，尝试下一项",
                        raw_mid,
                    )
                    continue
                mid = raw_mid.strip()
                lk = mid.lower()
                if lk in attempted_key:
                    continue
                attempted_key.add(lk)
                try:
                    return self._dashscope_try_model_once(
                        client,
                        root=root,
                        headers=headers,
                        model_id=mid,
                        dur_s=dur_s,
                        text_prompt=text_prompt,
                        dst=dst,
                        duration=duration,
                    )
                except DashScopeVideoAuthError:
                    raise
                except Exception as exc:
                    last_exc = exc
                    logger.warning(
                        "DashScope 文生视频 model=%s 失败，尝试下一项：%s",
                        mid,
                        exc,
                    )
                    continue

        if last_exc is not None:
            raise RuntimeError(
                "DashScope 文生视频已遍历配置的候选模型及内置兜底，仍全部失败。"
            ) from last_exc
        raise RuntimeError(
            "DashScope 文生视频：无可用模型（候选均为对话类或其它不可用项）。"
        )

    def _minimax_poll(
        self,
        client: httpx.Client,
        root: str,
        headers: dict,
        task_id: str,
        interval: float,
    ) -> str:
        query_url = f"{root}/v1/query/video_generation"
        max_rounds = 200
        for i in range(max_rounds):
            if i > 0:
                time.sleep(interval)
            r = client.get(query_url, headers=headers, params={"task_id": task_id})
            r.raise_for_status()
            data = r.json()
            st_raw = data.get("status") or data.get("task_status")
            st = str(st_raw or "").strip().lower()
            if st in ("success", "succeed", "completed", "complete"):
                fid = data.get("file_id")
                if not fid and isinstance(data.get("file"), dict):
                    fid = data["file"].get("id") or data["file"].get("file_id")
                if fid:
                    return str(fid)
                logger.warning("MiniMax 任务成功但无 file_id：%s", data)
                raise RuntimeError("MiniMax 任务成功但未返回 file_id")
            if st in ("fail", "failed", "error"):
                err = (
                    data.get("error_message")
                    or data.get("message")
                    or data.get("msg")
                    or "未知错误"
                )
                base = data.get("base_resp") or {}
                if isinstance(base, dict):
                    err = base.get("status_msg") or err
                raise RuntimeError(f"MiniMax 视频失败：{err}")
            logger.debug("MiniMax task %s status=%s", task_id[:16], st_raw)

        raise TimeoutError("MiniMax 视频生成轮询超时")

    def _minimax_download_file(
        self,
        client: httpx.Client,
        root: str,
        headers: dict,
        file_id: str,
        dst: str,
    ) -> None:
        retrieve_url = f"{root}/v1/files/retrieve"
        r = client.get(retrieve_url, headers=headers, params={"file_id": file_id})
        r.raise_for_status()
        data = r.json()
        file_obj = data.get("file") if isinstance(data.get("file"), dict) else {}
        download_url = (
            file_obj.get("download_url")
            or data.get("download_url")
            or (data.get("file") or {}).get("url")
        )
        if not download_url:
            logger.warning("MiniMax retrieve 无下载地址：%s", data)
            raise RuntimeError("MiniMax 未返回视频下载地址")

        rd = client.get(str(download_url), follow_redirects=True, timeout=600.0)
        rd.raise_for_status()
        Path(dst).write_bytes(rd.content)

    def _mock(
        self,
        *,
        prompt: str,
        duration: float,
        title: str,
        subtitle: str,
        voice_text: str,
        dst: str,
    ) -> GeneratedClip:
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        audio_path = media.make_color_video_with_voice(
            duration=duration,
            title=title,
            subtitle=subtitle,
            voice_text=voice_text,
            dst=dst,
        )
        cost = 0.0
        return GeneratedClip(
            file_path=dst,
            audio_path=audio_path,
            duration=duration,
            cost=cost,
            model=f"mock-{self.model}",
            prompt=prompt,
            fallback=True,
        )


def get_video_gen(db: Optional["Session"] = None, profile: str = "fast") -> VideoGenClient:
    """db 存在时优先使用数据库 kind=video 的配置（含 api_key、base_url）。"""

    if db is not None:
        from .model_resolve import resolve_video_gen_client

        return resolve_video_gen_client(db, profile)
    return VideoGenClient()
