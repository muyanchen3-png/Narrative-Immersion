#!/usr/bin/env python3
"""用 SQLite ``model_configs`` 里解析到的「视频」配置（含 provider / model / base_url）跑 DashScope 异步视频合成。

依赖：``kind=video`` 且已启用、``params.api_key`` 已保存；或 .env 中 ``HERMES_VIDEO_*`` / LLM key。

用法::

    cd backend && python scripts/smoke_deepseek_video.py
    python scripts/smoke_deepseek_video.py --prompt \"日落下的海浪，电影感\" --duration 5
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.services.video_gen import get_video_gen  # noqa: E402


def _mask(s: str | None) -> str:
    if not s:
        return "(empty)"
    t = str(s).strip()
    if len(t) <= 8:
        return "***"
    return f"{t[:4]}…{t[-4:]}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Smoke: DashScope video synthesis via resolved ModelConfig")
    ap.add_argument("--profile", default="fast", help="model_configs profile")
    ap.add_argument("--duration", type=float, default=5.0, help="秒（DashScope 文档依模型而定，多为 5–10）")
    ap.add_argument(
        "--prompt",
        default="电影感镜头：晨光中的城市天际线，缓慢推进，色调温暖。",
        help="文生视频提示词",
    )
    args = ap.parse_args()

    db = SessionLocal()
    try:
        client = get_video_gen(db, profile=args.profile)
        print(
            "resolved:",
            f"provider={client.provider}",
            f"model={client.model}",
            f"base_url={client.base_url}",
            f"api_key={_mask(client.api_key)}",
        )
        if (client.provider or "").lower() != "deepseek":
            print(
                "提示：当前解析到的 provider 不是 deepseek。"
                "请在设置中将「视频」提供商设为 deepseek，或检查 HERMES_VIDEO_PROVIDER。",
                file=sys.stderr,
            )
        dst = settings.generated_dir / "_smoke_deepseek_t2v.mp4"
        clip = client.generate(
            prompt=args.prompt,
            duration=float(args.duration),
            title="smoke",
            subtitle="deepseek/dashscope",
            voice_text="",
            dst=str(dst),
            forbid_placeholder=True,
        )
        print("ok:", clip.file_path, "model=", clip.model, "fallback=", clip.fallback)
        print("打开文件:", dst.resolve())
        return 0
    except Exception as exc:
        print("failed:", exc, file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
