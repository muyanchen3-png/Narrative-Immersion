#!/usr/bin/env python3
"""试跑一条 MiniMax **文生**视频（需已配置 HERMES_VIDEO_API_KEY 等）。

文生 API 不接受 ``MiniMax-Hailuo-2.3-Fast``（仅图生）；请使用 ``MiniMax-Hailuo-2.3`` 或留空默认。

**注意**：业务管线默认要求角色参照图；本脚本在导入 app 前关闭该策略以便纯文生试跑。

用法（在 backend 目录下）::

    source ../.venv/bin/activate
    python scripts/minimax_video_smoke.py

成片写入 ``storage/generated/_smoke_minimax.mp4``。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 试跑脚本允许无参照图文生；须在加载 Settings / VideoGenClient 之前设置
os.environ.setdefault("HERMES_VIDEO_REQUIRE_CHARACTER_REFERENCE", "false")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.video_gen import VideoGenClient  # noqa: E402


def main() -> None:
    dst = ROOT / "storage" / "generated" / "_smoke_minimax.mp4"
    dst.parent.mkdir(parents=True, exist_ok=True)
    client = VideoGenClient()
    clip = client.generate(
        prompt="电影感，雪地山谷中一名侠客回眸，慢镜头，晨光，无对白",
        duration=6.0,
        title="smoke",
        subtitle="",
        voice_text="",
        dst=str(dst),
        first_frame_image_path=None,
        subject_reference_paths=None,
    )
    print(
        "file:",
        clip.file_path,
        "| fallback:",
        clip.fallback,
        "| model:",
        clip.model,
        "| duration:",
        clip.duration,
    )
    if clip.fallback:
        sys.exit(1)


if __name__ == "__main__":
    main()
