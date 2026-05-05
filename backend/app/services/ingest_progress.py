"""成片入库/切分过程中的线程安全进度（供轮询接口展示）。"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional

_lock = threading.Lock()
_store: Dict[str, Dict[str, Any]] = {}


def start(video_id: str, *, total: int) -> None:
    with _lock:
        _store[video_id] = {
            "total": max(0, int(total)),
            "current": 0,
            "phase": "analyzing_shots",
            "error": None,
        }


def tick(video_id: str) -> None:
    """每完成一个镜头的切片 + 内容理解后调用一次。"""
    with _lock:
        if video_id not in _store:
            return
        s = _store[video_id]
        t = s["total"]
        s["current"] = min(int(t), int(s["current"]) + 1)


def set_phase(video_id: str, phase: str) -> None:
    with _lock:
        if video_id in _store:
            _store[video_id]["phase"] = phase


def fail(video_id: str, message: str) -> None:
    with _lock:
        if video_id not in _store:
            _store[video_id] = {"total": 0, "current": 0, "phase": "error", "error": message}
        else:
            _store[video_id]["error"] = message
            _store[video_id]["phase"] = "error"


def snapshot(video_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        s = _store.get(video_id)
        return dict(s) if s else None


def mark_done(video_id: str) -> None:
    with _lock:
        if video_id in _store:
            _store[video_id]["phase"] = "done"
