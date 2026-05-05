from __future__ import annotations

import copy
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Tuple

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import settings
from ..database import get_db

router = APIRouter(prefix="/api/configs", tags=["configs"])
logger = logging.getLogger(__name__)


def _params_for_api_response(params: Any) -> Tuple[Dict[str, Any], bool]:
    """返回给前端的 params（**永不包含** api_key 字段）及是否已保存密钥。"""
    out = dict(copy.deepcopy(params) or {})
    raw = out.pop("api_key", None)
    has_key = bool(raw and str(raw).strip())
    return out, has_key


def _merge_params(old: Any, new: Any) -> Dict[str, Any]:
    merged: Dict[str, Any] = dict(copy.deepcopy(old) or {})
    inc = new or {}
    for key, val in inc.items():
        if key == "api_key":
            if not val or not str(val).strip() or str(val).startswith("****"):
                continue
            merged["api_key"] = str(val).strip()
        else:
            merged[key] = val
    return merged


def _to_model_out(cfg: models.ModelConfig) -> schemas.ModelConfigOut:
    params, has_api_key = _params_for_api_response(cfg.params)
    return schemas.ModelConfigOut(
        id=cfg.id,
        kind=cfg.kind,
        profile=cfg.profile,
        name=cfg.name,
        provider=cfg.provider,
        model=cfg.model,
        base_url=cfg.base_url,
        api_key_alias=cfg.api_key_alias,
        params=params,
        has_api_key=has_api_key,
        is_default=cfg.is_default,
        enabled=cfg.enabled,
        priority=int(cfg.priority or 0),
        created_at=cfg.created_at,
        source="database",
        read_only=False,
    )


def _hk(val: Any) -> bool:
    return bool(val and str(val).strip())


def _env_model_snapshots() -> List[schemas.ModelConfigOut]:
    """把当前进程从 .env 加载的各链路配置打成只读条目，供前端与数据库配置一并展示。"""
    ts = datetime.utcnow()
    out: List[schemas.ModelConfigOut] = []

    def append_row(
        *,
        kind: str,
        name: str,
        provider: str,
        model: str,
        base_url: Any,
    ) -> None:
        out.append(
            schemas.ModelConfigOut(
                id=f"env:{kind}",
                kind=kind,
                profile="fast",
                name=name,
                provider=str(provider),
                model=str(model),
                base_url=str(base_url).strip() if base_url else None,
                api_key_alias=None,
                params={},
                has_api_key=True,
                is_default=True,
                enabled=True,
                priority=0,
                created_at=ts,
                source="environment",
                read_only=True,
            )
        )

    if _hk(settings.llm_api_key):
        append_row(
            kind="llm",
            name="环境变量 · 对话 / 文本",
            provider=settings.llm_provider,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
        )

    if _hk(settings.vlm_api_key) or _hk(settings.llm_api_key):
        nm = "环境变量 · 镜头理解 VLM"
        if not _hk(settings.vlm_api_key):
            nm += "（密钥沿用 LLM）"
        append_row(
            kind="vlm",
            name=nm,
            provider=settings.vlm_provider,
            model=settings.vlm_model,
            base_url=settings.vlm_base_url or settings.llm_base_url,
        )

    if _hk(settings.image_api_key) or _hk(settings.llm_api_key):
        nm = "环境变量 · 图像生成"
        if not _hk(settings.image_api_key):
            nm += "（密钥沿用 LLM）"
        append_row(
            kind="image",
            name=nm,
            provider=settings.image_provider,
            model=settings.image_model,
            base_url=settings.image_base_url or settings.llm_base_url,
        )

    if _hk(settings.video_api_key) or _hk(settings.llm_api_key):
        nm = "环境变量 · 视频生成"
        if not _hk(settings.video_api_key):
            nm += "（密钥沿用 LLM）"
        append_row(
            kind="video",
            name=nm,
            provider=settings.video_provider,
            model=settings.video_model,
            base_url=settings.video_base_url or settings.llm_base_url,
        )

    if _hk(settings.tts_api_key) or _hk(settings.llm_api_key):
        nm = "环境变量 · 配音 TTS"
        if not _hk(settings.tts_api_key):
            nm += "（密钥沿用 LLM）"
        append_row(
            kind="tts",
            name=nm,
            provider=settings.tts_provider,
            model=settings.tts_model,
            base_url=settings.tts_base_url or settings.llm_base_url,
        )

    if _hk(settings.asr_api_key) or _hk(settings.llm_api_key):
        nm = "环境变量 · 音轨 ASR"
        if not _hk(settings.asr_api_key):
            nm += "（密钥沿用 LLM）"
        append_row(
            kind="asr",
            name=nm,
            provider=settings.asr_provider,
            model=settings.asr_model,
            base_url=settings.asr_base_url or settings.llm_base_url,
        )

    return out


@router.get("/models", response_model=List[schemas.ModelConfigOut])
def list_models(db: Session = Depends(get_db)) -> List[schemas.ModelConfigOut]:
    rows = (
        db.query(models.ModelConfig)
        .order_by(
            models.ModelConfig.kind,
            models.ModelConfig.priority.desc(),
            models.ModelConfig.profile,
        )
        .all()
    )
    outs = [_to_model_out(c) for c in rows]
    # 未写入 API Key 的配置不参与前端列表（避免「未配置密钥」占位卡）。
    db_list = [o for o in outs if o.has_api_key]
    return db_list + _env_model_snapshots()


@router.post("/models", response_model=schemas.ModelConfigOut)
def create_model(payload: schemas.ModelConfigIn, db: Session = Depends(get_db)) -> schemas.ModelConfigOut:
    if payload.is_default:
        db.query(models.ModelConfig).filter(
            models.ModelConfig.kind == payload.kind,
            models.ModelConfig.profile == payload.profile,
        ).update({models.ModelConfig.is_default: False})

    cfg = models.ModelConfig(
        id=str(uuid.uuid4()),
        kind=payload.kind,
        profile=payload.profile,
        name=payload.name,
        provider=payload.provider,
        model=payload.model,
        base_url=payload.base_url,
        api_key_alias=payload.api_key_alias,
        params=payload.params or {},
        is_default=payload.is_default,
        enabled=payload.enabled,
        priority=int(payload.priority or 0),
    )
    db.add(cfg)
    db.commit()
    db.refresh(cfg)
    logger.info(
        "模型配置已创建 id=%s kind=%s profile=%s name=%s enabled=%s",
        cfg.id[:12],
        cfg.kind,
        cfg.profile,
        cfg.name,
        cfg.enabled,
    )
    return _to_model_out(cfg)


@router.put("/models/{config_id}", response_model=schemas.ModelConfigOut)
def update_model(
    config_id: str, payload: schemas.ModelConfigIn, db: Session = Depends(get_db)
) -> schemas.ModelConfigOut:
    if config_id.startswith("env:"):
        raise HTTPException(
            status_code=400,
            detail="该条目来自服务端 .env，请修改后重启进程",
        )
    cfg = db.get(models.ModelConfig, config_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="配置不存在")
    cfg.kind = payload.kind
    cfg.profile = payload.profile
    cfg.name = payload.name
    cfg.provider = payload.provider
    cfg.model = payload.model
    cfg.base_url = payload.base_url
    cfg.api_key_alias = payload.api_key_alias
    cfg.params = _merge_params(cfg.params, payload.params)
    cfg.is_default = payload.is_default
    cfg.enabled = payload.enabled
    cfg.priority = int(payload.priority or 0)
    db.commit()
    db.refresh(cfg)
    logger.info(
        "模型配置已更新 id=%s kind=%s profile=%s name=%s",
        cfg.id[:12],
        cfg.kind,
        cfg.profile,
        cfg.name,
    )
    return _to_model_out(cfg)


@router.delete("/models/{config_id}")
def delete_model(config_id: str, db: Session = Depends(get_db)) -> dict:
    if config_id.startswith("env:"):
        raise HTTPException(
            status_code=400,
            detail="环境变量条目不可删除，请编辑 .env 后重启",
        )
    cfg = db.get(models.ModelConfig, config_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="配置不存在")
    db.delete(cfg)
    db.commit()
    logger.info("模型配置已删除 id=%s", config_id[:12])
    return {"ok": True}


@router.get("/safety", response_model=List[schemas.SafetyPolicyOut])
def list_safety(db: Session = Depends(get_db)) -> List[models.SafetyPolicy]:
    return db.query(models.SafetyPolicy).order_by(models.SafetyPolicy.created_at).all()


@router.post("/safety", response_model=schemas.SafetyPolicyOut)
def create_safety(payload: schemas.SafetyPolicyIn, db: Session = Depends(get_db)) -> models.SafetyPolicy:
    policy = models.SafetyPolicy(
        id=str(uuid.uuid4()),
        label=payload.label,
        category=payload.category,
        keywords=payload.keywords,
        description=payload.description,
        rewrite_template=payload.rewrite_template,
        enabled=payload.enabled,
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    logger.info("安全策略已创建 id=%s label=%s", policy.id[:12], policy.label)
    return policy


@router.put("/safety/{policy_id}", response_model=schemas.SafetyPolicyOut)
def update_safety(
    policy_id: str, payload: schemas.SafetyPolicyIn, db: Session = Depends(get_db)
) -> models.SafetyPolicy:
    policy = db.get(models.SafetyPolicy, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="策略不存在")
    policy.label = payload.label
    policy.category = payload.category
    policy.keywords = payload.keywords
    policy.description = payload.description
    policy.rewrite_template = payload.rewrite_template
    policy.enabled = payload.enabled
    db.commit()
    db.refresh(policy)
    logger.info("安全策略已更新 id=%s label=%s", policy.id[:12], policy.label)
    return policy


@router.delete("/safety/{policy_id}")
def delete_safety(policy_id: str, db: Session = Depends(get_db)) -> dict:
    policy = db.get(models.SafetyPolicy, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="策略不存在")
    db.delete(policy)
    db.commit()
    logger.info("安全策略已删除 id=%s", policy_id[:12])
    return {"ok": True}
