#!/usr/bin/env python3
"""把二次合成的 *_dubbed.mp4 写回数据库，使媒资库 / 时间线引用配音+Bgm 成片。

联调脚本生成的 dubbed 文件默认不入库；本脚本按文件名替换：
``shot_outline_0_dubbed.mp4`` → 更新原先指向 ``shot_outline_0.mp4`` 的记录。

用法（在 backend 目录）::

    python scripts/point_assets_to_dubbed.py \\
      --dir storage/generated/e68b790b-1fdb-4e5d-ba37-103d2c5561d1

可加 ``--dry-run`` 只打印将要更新的行。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import Session  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app import models  # noqa: E402


def _basename_tail(path: str) -> str:
    return Path(path).name


def main() -> None:
    ap = argparse.ArgumentParser(description="将 dubbed 成片路径写回 shot_segments / timeline_segments / generated_assets")
    ap.add_argument(
        "--dir",
        type=Path,
        required=True,
        help="含 *_dubbed.mp4 的目录（一般为 generated/<job_id>/）",
    )
    ap.add_argument("--dry-run", action="store_true", help="不提交，仅打印")
    args = ap.parse_args()
    d = args.dir.resolve()
    if not d.is_dir():
        print(f"目录不存在：{d}", file=sys.stderr)
        sys.exit(1)

    dubbed_files = sorted(d.glob("*_dubbed.mp4"))
    if not dubbed_files:
        print(f"未找到 *_dubbed.mp4：{d}", file=sys.stderr)
        sys.exit(1)

    db: Session = SessionLocal()
    try:
        n_seg = n_tl = n_ga = 0
        for dub in dubbed_files:
            base_name = dub.name.replace("_dubbed.mp4", ".mp4")
            dub_str = str(dub)

            shots = (
                db.query(models.ShotSegment)
                .filter(models.ShotSegment.file_path.like(f"%{base_name}"))
                .all()
            )
            for row in shots:
                if _basename_tail(row.file_path) != base_name:
                    continue
                print(f"ShotSegment {row.id[:8]}… {row.file_path} -> {dub_str}")
                if not args.dry_run:
                    row.file_path = dub_str
                n_seg += 1

            segs = (
                db.query(models.TimelineSegment)
                .filter(models.TimelineSegment.file_path.like(f"%{base_name}"))
                .all()
            )
            for row in segs:
                if _basename_tail(row.file_path) != base_name:
                    continue
                print(f"TimelineSegment {row.id[:8]}… {row.file_path} -> {dub_str}")
                if not args.dry_run:
                    row.file_path = dub_str
                n_tl += 1

            assets = (
                db.query(models.GeneratedAsset)
                .filter(
                    models.GeneratedAsset.kind == "video",
                    models.GeneratedAsset.file_path.like(f"%{base_name}"),
                )
                .all()
            )
            for row in assets:
                if _basename_tail(row.file_path) != base_name:
                    continue
                print(f"GeneratedAsset {row.id[:8]}… {row.file_path} -> {dub_str}")
                if not args.dry_run:
                    row.file_path = dub_str
                n_ga += 1

        if args.dry_run:
            print(f"[dry-run] 将更新 shot_segments={n_seg}, timeline_segments={n_tl}, generated_assets={n_ga}")
            sys.exit(0)

        db.commit()
        print(
            f"已提交：shot_segments={n_seg}, timeline_segments={n_tl}, generated_assets={n_ga}"
        )
        if n_seg + n_tl + n_ga == 0:
            print(
                "提示：未匹配到任何记录。请确认库内路径仍以「不带 _dubbed」的文件名为准；"
                "若从未登记干预镜头，请先跑一次干预或手工插入 ShotSegment。",
                file=sys.stderr,
            )
            sys.exit(2)
    finally:
        db.close()


if __name__ == "__main__":
    main()
