"""生图：MiniMax `POST /v1/image_generation` 图生图；OpenAI `images/generations`；失败回退 Pillow 占位。"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from PIL import Image, ImageDraw, ImageFont

from ..config import settings

logger = logging.getLogger(__name__)

# MiniMax 参考图上限（文档：小于 10MB）
_MINIMAX_REF_MAX_BYTES = 10 * 1024 * 1024


def _is_minimax_api_host(url: str) -> bool:
    u = (url or "").lower()
    return "minimaxi.com" in u


def _normalize_minimax_host(base_url: str) -> str:
    """MiniMax 官网 API 根域名，路径固定为 /v1/image_generation。"""
    u = (base_url or "https://api.minimaxi.com").strip().rstrip("/")
    if u.endswith("/v1"):
        u = u[: -len("/v1")].rstrip("/")
    return u


def _file_to_data_url(path: Path) -> str:
    """MiniMax 支持公网 URL 或 Data URL（JPG/PNG，<10MB）。"""
    raw = path.read_bytes()
    if len(raw) > _MINIMAX_REF_MAX_BYTES:
        raise ValueError(f"参考图超过 {_MINIMAX_REF_MAX_BYTES} 字节")
    suf = path.suffix.lower()
    mime = "image/jpeg"
    if suf == ".png":
        mime = "image/png"
    elif suf in (".jpg", ".jpeg"):
        mime = "image/jpeg"
    b64 = base64.standard_b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _strip_images_suffix(base_url: str) -> str:
    u = base_url.strip().rstrip("/")
    for suf in ("/images/generations", "/v1/images/generations"):
        if u.endswith(suf):
            u = u[: -len(suf)].rstrip("/")
    return u


def _download_url(url: str, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120.0) as client:
        r = client.get(url, follow_redirects=True)
        r.raise_for_status()
        out.write_bytes(r.content)


def _download_url_bytes(url: str) -> bytes:
    with httpx.Client(timeout=120.0) as client:
        r = client.get(url, follow_redirects=True)
        r.raise_for_status()
        return r.content


def _annotate_views(canvas: Image.Image, view_labels: list[str]) -> Image.Image:
    """在三视图合成图的每个面板下方标注 front / side / back。"""
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.load_default()
    except Exception:  # noqa: BLE001
        font = None
    w, h = canvas.size
    panel_w = w // len(view_labels)
    footer_h = 48
    for i, label in enumerate(view_labels):
        x0 = i * panel_w
        box = [x0 + 4, h - footer_h, x0 + panel_w - 4, h]
        draw.rectangle(box, fill=(180, 180, 190), outline=(100, 100, 110))
        tx = x0 + 12
        ty = h - footer_h + 10
        fill = (30, 30, 30)
        if font:
            draw.text((tx, ty), label.title(), fill=fill, font=font)
        else:
            draw.text((tx, ty), label.title(), fill=fill)
    return canvas


def _write_placeholder_turnaround(
    out: Path,
    *,
    title: str,
    appearance: str,
    reference_note: str,
) -> None:
    """MiniMax/OpenAI 失败时的占位图。面板内需填充浅色底，否则仅有描边时整块画布同色会像「三张黑图」。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    w, h = 1200, 600
    bg_outer = (24, 24, 28)
    panel_fill = (52, 54, 62)
    panel_outline = (110, 112, 125)
    img = Image.new("RGB", (w, h), bg_outer)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default()
    except Exception:  # noqa: BLE001
        font = None
    panels = [("Front", 0), ("Side", w // 3), ("Back", 2 * w // 3)]
    pw = w // 3
    footer_h = 56
    for label, x0 in panels:
        box = [x0 + 8, 36, x0 + pw - 8, h - footer_h]
        draw.rectangle(box, fill=panel_fill, outline=panel_outline, width=2)
        tx = x0 + 16
        ty = 44
        if font:
            draw.text((tx, ty), label, fill=(220, 222, 230), font=font)
            draw.text((tx, ty + 12), "mock / no API", fill=(160, 162, 175), font=font)
        else:
            draw.text((tx, ty), label, fill=(220, 222, 230))
            draw.text((tx, ty + 12), "mock / no API", fill=(160, 162, 175))
    banner = f"{title[:80]} — placeholder (MiniMax/OpenAI 未返回图时生成)"
    sub = (appearance[:200] + " · " + reference_note[:120])[:280]
    if font:
        draw.text((16, h - 48), banner, fill=(150, 152, 168), font=font)
        draw.text((16, h - 28), sub, fill=(110, 112, 128), font=font)
    else:
        draw.text((16, h - 48), banner, fill=(150, 152, 168))
        draw.text((16, h - 28), sub, fill=(110, 112, 128))
    img.save(out, format="PNG", optimize=True)


def _minimax_image_generation(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    reference_image: Optional[Path],
    out_sheet: Path,
) -> bool:
    """
    MiniMax 图生图 / 文生图：POST /v1/image_generation。
    生成三视图（正面 / 侧面 / 背面），分别下载后 PIL 横向合成一张 PNG。
    """
    root = _normalize_minimax_host(base_url or settings.image_base_url or "https://api.minimaxi.com")
    url = f"{root}/v1/image_generation"
    mm_model = model if model in ("image-01", "image-01-live") else "image-01"
    # image-01-live 不支持 21:9（平台返回 2013）；单视图用 16:9
    aspect = "16:9"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # 三视图各自的视角修饰 prompt（每张图仅一人）
    view_suffixes = [
        "Front view only. Single full-body protagonist; no other people in frame. Looking directly at the camera, neutral standing pose.",
        "Side view only. Same single full-body protagonist; no second person. Three-quarter profile.",
        "Back view only. Same single full-body protagonist; no crowd; show back of head and outfit silhouette.",
    ]
    view_labels = ["front", "side", "back"]

    # 把参照图 data_url 提前准备好（三个请求共用）
    ref_data_url: Optional[str] = None
    if reference_image and reference_image.is_file():
        try:
            ref_data_url = _file_to_data_url(reference_image)
        except Exception as exc:  # noqa: BLE001
            logger.warning("MiniMax 参考图编码失败，改为纯文生图：%s", exc)

    def _call_view(view_prompt: str, view_label: str) -> Optional[bytes]:
        """对单个视角发一次 API，返回图片字节或 None。"""
        body: Dict[str, Any] = {
            "model": mm_model,
            "prompt": f"{prompt[:1400]}\n{view_prompt}".strip()[:1500],
            "aspect_ratio": aspect,
            "response_format": "url",
            "n": 1,
        }
        if ref_data_url:
            body["subject_reference"] = [{"type": "character", "image_file": ref_data_url}]
        with httpx.Client(timeout=180.0) as client:
            resp = client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
        base_resp = data.get("base_resp") or {}
        sc = base_resp.get("status_code")
        try:
            ok_business = int(sc) == 0
        except (TypeError, ValueError):
            ok_business = str(sc) in ("0", "")
        if not ok_business:
            logger.warning(
                "MiniMax 视角 %s 业务错误 status=%s msg=%s",
                view_label,
                base_resp.get("status_code"),
                base_resp.get("status_msg"),
            )
            return None
        blob = data.get("data") if isinstance(data.get("data"), dict) else {}
        urls = blob.get("image_urls") or data.get("image_urls") or []
        if isinstance(urls, str):
            urls = [urls]
        if urls:
            return _download_url_bytes(str(urls[0]))
        b64_list = blob.get("image_base64") or data.get("image_base64") or []
        if b64_list:
            return base64.b64decode(b64_list[0])
        return None

    # 三视图并发请求
    from concurrent.futures import ThreadPoolExecutor, as_completed

    view_images: Dict[str, Optional[bytes]] = {label: None for label in view_labels}
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(_call_view, suffix, label): label
            for suffix, label in zip(view_suffixes, view_labels)
        }
        for future in as_completed(futures):
            label = futures[future]
            try:
                view_images[label] = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.warning("MiniMax 视角 %s 请求异常：%s", label, exc)

    # 检查至少有两张成功才合成
    successful = {k: v for k, v in view_images.items() if v is not None}
    if len(successful) < 2:
        logger.warning("MiniMax 三视图仅成功 %d 张，无法合成", len(successful))
        return False

    # 用 PIL 合成横向拼图（front | side | back）
    from io import BytesIO

    sheets: Dict[str, Image.Image] = {}
    for label, raw in successful.items():
        try:
            sheets[label] = Image.open(BytesIO(raw)).convert("RGB")
        except Exception as exc:  # noqa: BLE001
            logger.warning("视角 %s 图片解码失败：%s", label, exc)

    if len(sheets) < 2:
        return False

    # 统一尺寸：取最大宽度和最小高度，避免某张过度拉伸
    target_w = max(img.width for img in sheets.values())
    target_h = min(img.height for img in sheets.values())
    resized = {}
    for label, img in sheets.items():
        # 等比缩放到 target_h，再用 center crop 到 target_w
        scale = target_h / img.height
        new_w = int(img.width * scale)
        img_resized = img.resize((new_w, target_h), Image.LANCZOS)
        # 中心裁剪到统一宽度
        left = (new_w - target_w) // 2
        resized[label] = img_resized.crop((left, 0, left + target_w, target_h))

    # 按 front / side / back 顺序横向拼接
    canvas = Image.new("RGB", (target_w * len(view_labels), target_h), (255, 255, 255))
    for i, label in enumerate(view_labels):
        if label in resized:
            canvas.paste(resized[label], (i * target_w, 0))

    # 标注三视图名称
    canvas = _annotate_views(canvas, view_labels)

    out_sheet.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_sheet, format="PNG", optimize=True)
    logger.info("MiniMax 三视图已合成 -> %s (%d views)", out_sheet, len(sheets))
    return True


def _openai_images_generations(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    out_sheet: Path,
    size: str,
) -> bool:
    root = _strip_images_suffix(base_url or settings.image_base_url or settings.llm_base_url)
    url = root.rstrip("/") + "/images/generations"
    payload = {
        "model": model,
        "prompt": prompt[:4000],
        "n": 1,
        "size": size if size in ("1024x1024", "1792x1024", "1024x1792") else "1792x1024",
    }
    out_sheet.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=180.0) as client:
        resp = client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
    item = (data.get("data") or [{}])[0]
    b64 = item.get("b64_json")
    u = item.get("url")
    if u:
        _download_url(u, out_sheet)
        logger.info("OpenAI 兼容生图已保存 url -> %s", out_sheet)
        return True
    if b64:
        out_sheet.write_bytes(base64.b64decode(b64))
        logger.info("OpenAI 兼容生图已保存 b64 -> %s", out_sheet)
        return True
    return False


def generate_turnaround_sheet(
    *,
    provider: str,
    model: str,
    base_url: str,
    api_key: Optional[str],
    character_label: str,
    appearance_prompt: str,
    reference_image: Optional[Path],
    out_sheet: Path,
    size: str = "1792x1024",
) -> bool:
    """
    生成「三视图 / 转向」sheet。当前以单张横向设定图为交付（含 front/side/back 布局语义）。
    若提供商未实现或调用失败，写入占位 PNG 并返回 False。
    """
    ref_note = "with reference frame (subject_reference anchored)" if reference_image and reference_image.exists() else "text-only"
    # 单人主人公硬约束置于最前，避免截断时丢失（MiniMax image-01 上限 1500 字符）
    _single_subject = (
        "【单人主人公】全图仅允许一名主角全身；禁止多人同框、禁止第二人、禁止群体作主视角。"
        " Single protagonist only; no extra people.\n\n"
    )
    base = _single_subject + (appearance_prompt or character_label)
    prompt = base[:1500]

    # MiniMax 官方为 POST /v1/image_generation；勿对 api.minimaxi.com 调 OpenAI 的 /images/generations
    if api_key and (provider == "minimax" or _is_minimax_api_host(base_url)):
        try:
            if _minimax_image_generation(
                base_url=base_url,
                api_key=api_key,
                model=model,
                prompt=prompt,
                reference_image=reference_image,
                out_sheet=out_sheet,
            ):
                return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("MiniMax 图生图失败，尝试后续回退：%s", exc)

    if api_key and provider == "openai" and not _is_minimax_api_host(base_url):
        try:
            if _openai_images_generations(
                base_url=base_url,
                api_key=api_key,
                model=model,
                prompt=prompt,
                out_sheet=out_sheet,
                size=size,
            ):
                return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("OpenAI 兼容生图失败，回退占位图：%s", exc)

    _write_placeholder_turnaround(
        out_sheet,
        title=character_label,
        appearance=appearance_prompt,
        reference_note=ref_note,
    )
    return False
