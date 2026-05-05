"""SQLAlchemy ORM 模型，覆盖镜头资产、剧情状态、时间线版本、生成任务、安全策略等。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _now() -> datetime:
    return datetime.utcnow()


class VideoAsset(Base):
    """原始上传视频，承载主线时间线和媒资库的源素材。"""

    __tablename__ = "video_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text, default=None)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    fps: Mapped[float] = mapped_column(Float, default=0.0)
    file_path: Mapped[str] = mapped_column(String(512))
    poster_path: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    status: Mapped[str] = mapped_column(String(32), default="processing")
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    segments: Mapped[List["ShotSegment"]] = relationship(back_populates="video", cascade="all, delete-orphan")
    timelines: Mapped[List["Timeline"]] = relationship(back_populates="video", cascade="all, delete-orphan")
    cast_members: Mapped[List["VideoCharacter"]] = relationship(
        back_populates="video",
        cascade="all, delete-orphan",
        order_by="VideoCharacter.name_key",
    )


class ShotSegment(Base):
    """切分出来的镜头片段，多种粒度共存（1s / 5s / 10s / scene / story）。"""

    __tablename__ = "shot_segments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("video_assets.id"))
    granularity: Mapped[str] = mapped_column(String(32))
    index: Mapped[int] = mapped_column(Integer, default=0)
    start_time: Mapped[float] = mapped_column(Float)
    end_time: Mapped[float] = mapped_column(Float)
    duration: Mapped[float] = mapped_column(Float)

    file_path: Mapped[str] = mapped_column(String(512))
    thumbnail_path: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    audio_path: Mapped[Optional[str]] = mapped_column(String(512), default=None)

    summary: Mapped[Optional[str]] = mapped_column(Text)
    characters: Mapped[list] = mapped_column(JSON, default=list)
    location: Mapped[Optional[str]] = mapped_column(String(255))
    actions: Mapped[list] = mapped_column(JSON, default=list)
    dialogue: Mapped[Optional[str]] = mapped_column(Text)
    emotion: Mapped[Optional[str]] = mapped_column(String(64))
    objects: Mapped[list] = mapped_column(JSON, default=list)
    visual_style: Mapped[dict] = mapped_column(JSON, default=dict)
    continuity_anchors: Mapped[dict] = mapped_column(JSON, default=dict)
    safety_labels: Mapped[list] = mapped_column(JSON, default=list)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    embedding: Mapped[Optional[list]] = mapped_column(JSON, default=None)

    source: Mapped[str] = mapped_column(String(32), default="origin")
    quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    video: Mapped[VideoAsset] = relationship(back_populates="segments")


class VideoCharacter(Base):
    """成片级角色库：由镜头分析中的 characters 字段聚合得到，每视频一份独立列表。"""

    __tablename__ = "video_characters"
    __table_args__ = (UniqueConstraint("video_id", "name_key", name="uq_video_character_name_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("video_assets.id", ondelete="CASCADE"))
    #: 归一化键（去重），通常为首条出现的展示名经规范化
    name_key: Mapped[str] = mapped_column(String(256))
    #: 展示名（优先使用最常见或首次出现的写法）
    display_name: Mapped[str] = mapped_column(String(256))
    #: 各镜头里出现过的其他写法
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    mention_count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen_s: Mapped[float] = mapped_column(Float, default=0.0)
    last_seen_s: Mapped[float] = mapped_column(Float, default=0.0)
    #: 出现过该角色的镜头 id（便于回溯）
    source_shot_ids: Mapped[list] = mapped_column(JSON, default=list)
    description: Mapped[Optional[str]] = mapped_column(Text, default=None)
    #: 用户自填备注（与智能体 description 独立）
    user_notes: Mapped[Optional[str]] = mapped_column(Text, default=None)
    #: 智能体综合镜头字幕后的人物判定（JSON：identity_summary / appearance_for_art 等）
    agent_profile: Mapped[dict] = mapped_column(JSON, default=dict)
    #: 参照镜头与抽帧（人物从镜头中来）
    reference_shot_id: Mapped[Optional[str]] = mapped_column(String(36), default=None)
    reference_image_path: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    #: 与参照图对应的切片视频路径（成片镜头文件）
    reference_video_path: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    #: 三视图等生成图路径 JSON，如 {"front":"...","side":"...","back":"...","sheet":"..."}
    three_views: Mapped[dict] = mapped_column(JSON, default=dict)
    #: pending | analyzing | visual_ready | partial | failed
    enrichment_status: Mapped[str] = mapped_column(String(32), default="pending")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    video: Mapped[VideoAsset] = relationship(back_populates="cast_members")


class StoryState(Base):
    """剧情状态：角色目标、人物关系、事件因果、世界规则、剧情约束。"""

    __tablename__ = "story_states"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    timeline_id: Mapped[str] = mapped_column(ForeignKey("timelines.id"))
    time_point: Mapped[float] = mapped_column(Float, default=0.0)

    current_event: Mapped[Optional[str]] = mapped_column(Text)
    previous_events: Mapped[list] = mapped_column(JSON, default=list)
    causal_links: Mapped[list] = mapped_column(JSON, default=list)
    characters_state: Mapped[dict] = mapped_column(JSON, default=dict)
    world_rules: Mapped[dict] = mapped_column(JSON, default=dict)
    location_time: Mapped[dict] = mapped_column(JSON, default=dict)
    open_threads: Mapped[list] = mapped_column(JSON, default=list)
    constraints: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Timeline(Base):
    """时间线版本：每个用户实际观看的版本独立存在。"""

    __tablename__ = "timelines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("video_assets.id"))
    parent_id: Mapped[Optional[str]] = mapped_column(ForeignKey("timelines.id"), default=None)
    label: Mapped[str] = mapped_column(String(128), default="主线")
    status: Mapped[str] = mapped_column(String(32), default="ready")  # ready / patching / failed
    branch_reason: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    apply_time: Mapped[Optional[float]] = mapped_column(Float, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    video: Mapped[VideoAsset] = relationship(back_populates="timelines")
    segments: Mapped[List["TimelineSegment"]] = relationship(
        back_populates="timeline",
        cascade="all, delete-orphan",
        order_by="TimelineSegment.index",
    )


class TimelineSegment(Base):
    """时间线上的有序片段，可指向原片镜头、复用片段或新生成片段。"""

    __tablename__ = "timeline_segments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    timeline_id: Mapped[str] = mapped_column(ForeignKey("timelines.id"))
    index: Mapped[int] = mapped_column(Integer, default=0)
    start_time: Mapped[float] = mapped_column(Float, default=0.0)
    end_time: Mapped[float] = mapped_column(Float, default=0.0)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    shot_id: Mapped[Optional[str]] = mapped_column(ForeignKey("shot_segments.id"), default=None)
    source: Mapped[str] = mapped_column(String(32), default="origin")  # origin / reused / generated / bridge / fallback
    file_path: Mapped[str] = mapped_column(String(512))
    audio_path: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    caption: Mapped[Optional[str]] = mapped_column(Text)
    note: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    timeline: Mapped[Timeline] = relationship(back_populates="segments")


class TimelinePatch(Base):
    """一次干预产生的时间线补丁。"""

    __tablename__ = "timeline_patches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    from_timeline_id: Mapped[str] = mapped_column(ForeignKey("timelines.id"))
    to_timeline_id: Mapped[str] = mapped_column(ForeignKey("timelines.id"))
    intervention_id: Mapped[Optional[str]] = mapped_column(ForeignKey("interventions.id"), default=None)
    replace_start_time: Mapped[float] = mapped_column(Float, default=0.0)
    replace_end_time: Mapped[float] = mapped_column(Float, default=0.0)
    transition_note: Mapped[Optional[str]] = mapped_column(Text)
    continuity_score: Mapped[float] = mapped_column(Float, default=0.0)
    safety_score: Mapped[float] = mapped_column(Float, default=0.0)
    quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ChatMessage(Base):
    """对话框中的所有消息，包括问答和干预指令。"""

    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    timeline_id: Mapped[str] = mapped_column(ForeignKey("timelines.id"))
    role: Mapped[str] = mapped_column(String(16))  # user / system / agent
    intent: Mapped[str] = mapped_column(String(32), default="qa")  # qa / intervention / system
    play_time: Mapped[float] = mapped_column(Float, default=0.0)
    content: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Intervention(Base):
    """用户的一次剧情干预。"""

    __tablename__ = "interventions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    timeline_id: Mapped[str] = mapped_column(ForeignKey("timelines.id"))
    user_text: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[Optional[str]] = mapped_column(Text)
    feasibility_level: Mapped[str] = mapped_column(String(8), default="L1")  # L0..L5
    safety_decision: Mapped[str] = mapped_column(String(32), default="allow")  # allow / rewrite / reject
    safety_reason: Mapped[Optional[str]] = mapped_column(Text)
    play_time: Mapped[float] = mapped_column(Float, default=0.0)
    estimated_gen_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    apply_time: Mapped[Optional[float]] = mapped_column(Float, default=None)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    rationale: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class GenerationJob(Base):
    """生成任务，记录每次干预的 model 调度、复用、生成、成本与质检。"""

    __tablename__ = "generation_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    intervention_id: Mapped[str] = mapped_column(ForeignKey("interventions.id"))
    timeline_id: Mapped[str] = mapped_column(ForeignKey("timelines.id"))
    new_timeline_id: Mapped[Optional[str]] = mapped_column(ForeignKey("timelines.id"), default=None)
    status: Mapped[str] = mapped_column(String(32), default="queued")  # queued / running / done / failed
    profile: Mapped[str] = mapped_column(String(16), default="fast")
    plan: Mapped[dict] = mapped_column(JSON, default=dict)
    timeline_log: Mapped[list] = mapped_column(JSON, default=list)
    reuse_segments: Mapped[list] = mapped_column(JSON, default=list)
    generated_segments: Mapped[list] = mapped_column(JSON, default=list)
    estimated_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    actual_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    cost_estimate: Mapped[float] = mapped_column(Float, default=0.0)
    actual_cost: Mapped[float] = mapped_column(Float, default=0.0)
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    continuity_score: Mapped[float] = mapped_column(Float, default=0.0)
    safety_score: Mapped[float] = mapped_column(Float, default=0.0)
    quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class VoiceProfile(Base):
    """角色音色档案，保证后续视频声音连续。"""

    __tablename__ = "voice_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("video_assets.id"))
    character: Mapped[str] = mapped_column(String(128))
    language: Mapped[str] = mapped_column(String(16), default="zh-CN")
    gender: Mapped[str] = mapped_column(String(16), default="neutral")
    age_band: Mapped[str] = mapped_column(String(16), default="adult")
    style: Mapped[str] = mapped_column(String(64), default="natural")
    voice_id: Mapped[str] = mapped_column(String(64), default="mock-voice-1")
    allow_clone: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ModelConfig(Base):
    """模型配置项：对话、理解、生图、生视频、配音。"""

    __tablename__ = "model_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))  # llm / vlm / image / video / tts
    profile: Mapped[str] = mapped_column(String(16), default="fast")  # fast / quality / fallback / consistency
    name: Mapped[str] = mapped_column(String(128))
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    base_url: Mapped[Optional[str]] = mapped_column(String(512))
    api_key_alias: Mapped[Optional[str]] = mapped_column(String(64))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    #: 同 kind + profile 下多档配置时，数值越大越优先被解析选用（需已保存 api_key 且启用）。
    priority: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class SafetyPolicy(Base):
    """可配置的安全策略：禁止 / 限制 / 改写。"""

    __tablename__ = "safety_policies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    label: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(32), default="forbid")  # forbid / restrict / rewrite
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    description: Mapped[str] = mapped_column(Text, default="")
    rewrite_template: Mapped[Optional[str]] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class SafetyLog(Base):
    """安全审核日志：原始输入 / 改写版本 / 决策。"""

    __tablename__ = "safety_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    intervention_id: Mapped[Optional[str]] = mapped_column(ForeignKey("interventions.id"))
    raw_text: Mapped[str] = mapped_column(Text)
    decision: Mapped[str] = mapped_column(String(16), default="allow")
    matched_policy: Mapped[Optional[str]] = mapped_column(String(64))
    rewrite_text: Mapped[Optional[str]] = mapped_column(Text)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class GeneratedAsset(Base):
    """生成产物（图片/视频/音频）的元信息，便于复用与审计。"""

    __tablename__ = "generated_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[Optional[str]] = mapped_column(ForeignKey("generation_jobs.id"))
    kind: Mapped[str] = mapped_column(String(16))  # image / video / audio
    file_path: Mapped[str] = mapped_column(String(512))
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    prompt: Mapped[Optional[str]] = mapped_column(Text)
    seed: Mapped[Optional[int]] = mapped_column(Integer)
    model: Mapped[Optional[str]] = mapped_column(String(128))
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    embedding: Mapped[Optional[list]] = mapped_column(JSON, default=None)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
