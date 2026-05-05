from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services import shot_reanalyze

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/assets", tags=["assets"])


# 必须先于 GET /shots 注册，避免个别环境下子路径与列表路由匹配歧义。
@router.post("/shots/{shot_id}/reanalyze", response_model=schemas.ShotOut)
def reanalyze_shot_endpoint(shot_id: str, db: Session = Depends(get_db)) -> models.ShotSegment:
    """重新调用镜头分析模型，刷新摘要等结构化字段，并同步指向该镜头的时间线 caption。"""
    try:
        shot = shot_reanalyze.reanalyze_shot(db, shot_id)
        db.commit()
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("镜头重新生成失败")
        raise HTTPException(status_code=500, detail=f"镜头重新生成失败：{exc}") from exc
    db.refresh(shot)
    logger.info("单镜头重新生成完成 shot=%s video=%s", shot_id[:12], shot.video_id[:12])
    return shot


@router.get("/shots", response_model=List[schemas.ShotOut])
def list_shots(
    video_id: Optional[str] = None,
    granularity: Optional[str] = None,
    source: Optional[str] = None,
    keyword: Optional[str] = None,
    db: Session = Depends(get_db),
) -> List[models.ShotSegment]:
    q = db.query(models.ShotSegment)
    if video_id:
        q = q.filter(models.ShotSegment.video_id == video_id)
    if granularity:
        q = q.filter(models.ShotSegment.granularity == granularity)
    if source:
        q = q.filter(models.ShotSegment.source == source)
    rows = q.order_by(models.ShotSegment.created_at.desc()).limit(500).all()
    if keyword:
        kw = keyword.lower()
        rows = [
            r
            for r in rows
            if kw in (r.summary or "").lower()
            or any(kw in (t or "").lower() for t in (r.tags or []))
            or any(kw in (c or "").lower() for c in (r.characters or []))
        ]
    return rows


@router.get("/timelines", response_model=List[schemas.TimelineOut])
def list_timelines(
    video_id: Optional[str] = None,
    db: Session = Depends(get_db),
) -> List[models.Timeline]:
    q = db.query(models.Timeline)
    if video_id:
        q = q.filter(models.Timeline.video_id == video_id)
    return q.order_by(models.Timeline.created_at).all()


def _resolve_asset_file(path: str) -> Tuple[Path, str]:
    p = Path(path)
    if not p.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    media_type = "video/mp4"
    if p.suffix.lower() in (".jpg", ".jpeg"):
        media_type = "image/jpeg"
    elif p.suffix.lower() == ".png":
        media_type = "image/png"
    elif p.suffix.lower() in (".m4a", ".aac"):
        media_type = "audio/mp4"
    elif p.suffix.lower() == ".mp3":
        media_type = "audio/mpeg"
    return p, media_type


@router.head("/file")
def head_asset_file(path: str) -> Response:
    """支持 HEAD（视频 probe / Range 预检），避免 405。"""
    p, media_type = _resolve_asset_file(path)
    size = p.stat().st_size
    return Response(
        status_code=200,
        media_type=media_type,
        headers={"Content-Length": str(size), "Accept-Ranges": "bytes"},
    )


@router.get("/file")
def get_asset_file(path: str):
    p, media_type = _resolve_asset_file(path)
    return FileResponse(str(p), media_type=media_type)
