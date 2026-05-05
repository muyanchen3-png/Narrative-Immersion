#!/usr/bin/env python3
"""按编排智能体（与线上一致：有非兜底生成时才调用 LLM）重算 apply_time，并重装分支 segment。

用法（在 backend 目录）::

    python scripts/rebuild_branch_from_job.py --job-id <generation_job_id> [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import SessionLocal  # noqa: E402
from app import models  # noqa: E402
from app.services.agents import apply_schedule, risk  # noqa: E402
from app.services.agents import editor  # noqa: E402
from app.services.timeline import get_segments, rebuild_branch_fork  # noqa: E402


def _inject_specs_from_branch(
    specs: list, branch_segs: list[models.TimelineSegment]
) -> list:
    """用当前分支上已落库的生成段路径/时长覆盖（保留如 _dubbed 等二次合成路径）。"""
    inject = [s for s in branch_segs if s.source in ("generated", "fallback", "reused")]
    if not inject:
        return specs
    if len(inject) != len(specs):
        inj2 = [s for s in branch_segs if "shot_outline" in (s.file_path or "")]
        if len(inj2) == len(specs):
            inject = inj2
    if len(inject) != len(specs):
        return specs
    out = []
    for spec, seg in zip(specs, inject):
        out.append(
            replace(
                spec,
                file_path=seg.file_path,
                duration=seg.duration,
                audio_path=seg.audio_path or spec.audio_path,
            )
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job-id", required=True, help="GenerationJob.id")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        job = db.get(models.GenerationJob, args.job_id)
        if not job or not job.new_timeline_id:
            print("任务不存在或未关联分支时间线", file=sys.stderr)
            sys.exit(1)

        branch_id = job.new_timeline_id
        branch_tl = db.get(models.Timeline, branch_id)
        if not branch_tl or not branch_tl.parent_id:
            print("分支时间线无效", file=sys.stderr)
            sys.exit(1)

        base_tl = db.get(models.Timeline, job.timeline_id)
        video = db.get(models.VideoAsset, base_tl.video_id) if base_tl else None
        inv = db.get(models.Intervention, job.intervention_id)
        if not inv:
            print("干预记录缺失", file=sys.stderr)
            sys.exit(1)

        shots = (job.plan or {}).get("shots") or []
        ra = risk.assess(shots=shots, profile=job.profile or "fast")
        branch_plan = (job.plan or {}).get("branch") or {}
        apply_time, sched_note, used_llm = apply_schedule.decide_apply_time(
            play_time=inv.play_time or 0.0,
            video_duration=float(video.duration) if video and video.duration else 0.0,
            intervention_text=(inv.normalized_text or inv.user_text or ""),
            branch_summary=(branch_plan.get("summary") or "") if isinstance(branch_plan, dict) else "",
            shots=shots,
            generated_for_log=list(job.generated_segments or []),
            risk_assess=ra,
        )

        specs = editor.assemble_specs(
            generated_shots=job.generated_segments or [],
            reused_shots=job.reuse_segments or [],
        )
        branch_segs = get_segments(db, branch_id)
        specs = _inject_specs_from_branch(specs, branch_segs)

        replace_dur = sum(s.duration for s in specs)
        print(
            f"job={job.id[:8]}… branch={branch_id[:8]}… "
            f"apply_time {branch_tl.apply_time:.3f} -> {apply_time:.3f} "
            f"(inject_dur={replace_dur:.3f}, llm={used_llm})\n  {sched_note}"
        )

        if args.dry_run:
            return

        rebuild_branch_fork(db, branch_timeline_id=branch_id, apply_time=apply_time, new_segments=specs)
        inv.apply_time = apply_time

        patch = (
            db.query(models.TimelinePatch)
            .filter(models.TimelinePatch.to_timeline_id == branch_id)
            .order_by(models.TimelinePatch.id)
            .first()
        )
        if patch:
            patch.replace_start_time = apply_time
            patch.replace_end_time = apply_time + replace_dur

        db.commit()
        print("已提交：分支 segment / intervention.apply_time / timeline_patch 已更新")
    finally:
        db.close()


if __name__ == "__main__":
    main()
