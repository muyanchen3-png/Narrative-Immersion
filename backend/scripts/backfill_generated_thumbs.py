#!/usr/bin/env python3
"""为「干预生成」类镜头补抽缩略图（历史数据登记时未写 thumbnail_path 时可用）。

用法（在 backend 目录）::

    python scripts/backfill_generated_thumbs.py
    python scripts/backfill_generated_thumbs.py --video-id <uuid>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import SessionLocal  # noqa: E402
from app.services import generated_shot_catalog  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video-id", default=None, help="仅处理该成片下的生成镜头")
    ap.add_argument(
        "--fields-from-jobs",
        action="store_true",
        help="从该成片的最新生成任务 JSON 回填人物/地点/对白等（需传 --video-id）",
    )
    args = ap.parse_args()
    db = SessionLocal()
    try:
        n = generated_shot_catalog.backfill_missing_thumbnails(
            db, video_id=(args.video_id or None)
        )
        nf = 0
        if args.fields_from_jobs and args.video_id:
            nf = generated_shot_catalog.backfill_structured_fields_from_jobs(
                db, video_id=args.video_id
            )
        db.commit()
        print(f"已补 {n} 条缩略图" + (f"，结构化字段 {nf} 条" if args.fields_from_jobs else ""))
    finally:
        db.close()


if __name__ == "__main__":
    main()
