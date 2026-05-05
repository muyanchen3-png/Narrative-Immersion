from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _enrich_job_out(db: Session, job: models.GenerationJob) -> schemas.JobOut:
    out = schemas.JobOut.model_validate(job)
    iv = db.get(models.Intervention, job.intervention_id)
    if not iv:
        return out
    return out.model_copy(
        update={
            "playback_position_s": iv.play_time,
            "branch_apply_time_s": iv.apply_time,
        }
    )


@router.get("", response_model=List[schemas.JobOut])
def list_jobs(
    timeline_id: Optional[str] = None,
    db: Session = Depends(get_db),
) -> List[schemas.JobOut]:
    q = db.query(models.GenerationJob)
    if timeline_id:
        q = q.filter(models.GenerationJob.timeline_id == timeline_id)
    order_col = func.coalesce(
        models.GenerationJob.finished_at,
        models.GenerationJob.started_at,
        models.GenerationJob.created_at,
    )
    rows = q.order_by(order_col.desc()).limit(50).all()
    return [_enrich_job_out(db, j) for j in rows]


@router.get("/{job_id}", response_model=schemas.JobOut)
def get_job(job_id: str, db: Session = Depends(get_db)) -> schemas.JobOut:
    job = db.get(models.GenerationJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _enrich_job_out(db, job)


@router.get("/intervention/{intervention_id}", response_model=schemas.InterventionOut)
def get_intervention(intervention_id: str, db: Session = Depends(get_db)) -> models.Intervention:
    obj = db.get(models.Intervention, intervention_id)
    if not obj:
        raise HTTPException(status_code=404, detail="干预记录不存在")
    return obj


@router.delete("/{job_id}/shots/{shot_index}", response_model=schemas.JobOut)
def delete_job_shot(
    job_id: str,
    shot_index: int,
    db: Session = Depends(get_db),
) -> schemas.JobOut:
    """从任务的 `plan.shots` 列表中移除指定索引的镜头（尚未生成时可用）。"""
    job = db.get(models.GenerationJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    plan = job.plan
    if not isinstance(plan, dict):
        raise HTTPException(status_code=400, detail="该任务无分镜计划，无法删除镜头")
    shots = plan.get("shots")
    if not isinstance(shots, list):
        raise HTTPException(status_code=400, detail="分镜计划中无镜头列表")
    if shot_index < 0 or shot_index >= len(shots):
        raise HTTPException(status_code=400, detail=f"镜头索引 {shot_index} 超出范围（当前 {len(shots)} 条）")
    removed = shots.pop(shot_index)
    plan["shots"] = shots
    job.plan = plan
    # 同步 shots_count
    if isinstance(plan, dict) and "shots_count" in plan:
        plan["shots_count"] = len(shots)
    db.commit()
    db.refresh(job)
    return _enrich_job_out(db, job)
