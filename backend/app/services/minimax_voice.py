"""MiniMax 快速音色复刻：上传音频 → /v1/voice_clone。

文档：https://platform.minimaxi.com （files/upload purpose=voice_clone | prompt_audio）
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from .image_generate import _normalize_minimax_host

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def upload_audio_for_purpose(
    *,
    api_key: str,
    base_url: str,
    file_path: Path,
    purpose: str,
) -> str:
    """
    POST /v1/files/upload，返回 ``file_id``。
    ``purpose``: ``voice_clone`` | ``prompt_audio``
    """

    root = _normalize_minimax_host(base_url or "https://api.minimaxi.com")
    url = f"{root}/v1/files/upload"
    headers = {"Authorization": f"Bearer {api_key}"}

    with httpx.Client(timeout=180.0) as client:
        with file_path.open("rb") as fh:
            files = {"file": (file_path.name, fh)}
            data = {"purpose": purpose}
            resp = client.post(url, headers=headers, data=data, files=files)
    resp.raise_for_status()
    body = resp.json()
    fid = (body.get("file") or {}).get("file_id")
    if not fid:
        base = body.get("base_resp") or {}
        logger.warning("MiniMax upload 无 file_id：%s", body)
        raise RuntimeError(base.get("status_msg") or "上传成功但未返回 file_id")
    return str(fid)


def quick_voice_clone(
    *,
    api_key: str,
    base_url: str,
    file_id: str,
    voice_id: str,
    text: str,
    model: str,
    prompt_file_id: Optional[str] = None,
    prompt_text: Optional[str] = None,
) -> Dict[str, Any]:
    """
    POST /v1/voice_clone。``text`` 为试听合成文案。
    若提供示例音，需同时传 ``prompt_file_id`` 与 ``prompt_text``。
    """

    root = _normalize_minimax_host(base_url or "https://api.minimaxi.com")
    url = f"{root}/v1/voice_clone"
    payload: Dict[str, Any] = {
        "file_id": file_id,
        "voice_id": voice_id,
        "text": text,
        "model": model,
    }
    if prompt_file_id and prompt_text:
        payload["clone_prompt"] = {"prompt_audio": prompt_file_id, "prompt_text": prompt_text}
    elif prompt_file_id or prompt_text:
        raise ValueError("示例音复刻需同时提供 prompt_file_id 与 prompt_text")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=300.0) as client:
        resp = client.post(url, headers=headers, json=payload)
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        logger.warning("MiniMax voice_clone HTTP %s: %s", resp.status_code, detail)
        raise RuntimeError(str(detail)) from exc

    if not resp.content:
        return {}
    try:
        return resp.json()
    except Exception:
        return {"raw_text": resp.text}
