"""API I/O 用的 Pydantic schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from .text_sanitize import strip_thinking_blocks


class ORMBase(BaseModel):
    model_config = {"from_attributes": True}


class VideoDeletedOut(BaseModel):
    ok: bool = True
    video_id: str


class ShotDeletedOut(BaseModel):
    ok: bool = True
    video_id: str
    shot_id: str


class IngestProgressOut(BaseModel):
    video_id: str
    total: int = 0
    current: int = 0
    phase: str = "pending"
    error: Optional[str] = None


class VideoOut(ORMBase):
    id: str
    title: str
    description: Optional[str] = None
    duration: float
    width: int
    height: int
    fps: float
    file_path: str
    poster_path: Optional[str] = None
    status: str
    config: dict = Field(default_factory=dict)
    created_at: datetime


class ReanalyzeShotsOut(BaseModel):
    video_id: str
    shots_updated: int
    characters_count: int = 0


class TranscribeAsrBulkOut(BaseModel):
    video_id: str
    shots_processed: int
    shots_with_transcript: int


class VideoCharacterOut(ORMBase):
    id: str
    video_id: str
    name_key: str
    display_name: str
    aliases: List[str] = Field(default_factory=list)
    mention_count: int = 0
    first_seen_s: float = 0.0
    last_seen_s: float = 0.0
    source_shot_ids: List[str] = Field(default_factory=list)
    description: Optional[str] = None
    user_notes: Optional[str] = None
    agent_profile: dict = Field(default_factory=dict)
    reference_shot_id: Optional[str] = None
    reference_image_path: Optional[str] = None
    reference_video_path: Optional[str] = None
    three_views: dict = Field(default_factory=dict)
    enrichment_status: str = "pending"
    created_at: datetime
    updated_at: datetime

    @field_validator("agent_profile", "three_views", mode="before")
    @classmethod
    def _json_dicts(cls, v: Any) -> dict:
        return v if isinstance(v, dict) else {}

    @field_validator("aliases", "source_shot_ids", mode="before")
    @classmethod
    def _json_lists(cls, v: Any) -> list:
        return v if isinstance(v, list) else []

    @field_validator("enrichment_status", mode="before")
    @classmethod
    def _status(cls, v: Any) -> str:
        s = (v or "pending") if isinstance(v, str) else "pending"
        return s or "pending"


class CharacterEnrichItemOut(BaseModel):
    character_id: str
    ok: bool
    enrichment_status: str
    detail: Optional[str] = None


class CharacterEnrichBatchOut(BaseModel):
    video_id: str
    items: List[CharacterEnrichItemOut] = Field(default_factory=list)


class ExtractCharactersOut(BaseModel):
    video_id: str
    count: int


class VideoCharacterPatch(BaseModel):
    """用户可更新的成片角色库字段。"""

    user_notes: Optional[str] = None


class BranchApplyTimeIn(BaseModel):
    """手动调整叙事分支在成片时间轴上的切入时刻（秒）。"""

    apply_time: float


class TimelineSegmentReorderIn(BaseModel):
    """分支时间线片段的播放顺序（仅重排、不改媒体文件）；须包含当前时间线下全部片段 id。"""

    segment_ids: List[str]


class ShotOut(ORMBase):
    id: str
    video_id: str
    granularity: str
    index: int
    start_time: float
    end_time: float
    duration: float
    file_path: str
    thumbnail_path: Optional[str] = None
    audio_path: Optional[str] = None
    summary: Optional[str] = None
    characters: List[str] = Field(default_factory=list)
    location: Optional[str] = None
    actions: List[str] = Field(default_factory=list)
    dialogue: Optional[str] = None
    emotion: Optional[str] = None
    objects: List[str] = Field(default_factory=list)
    visual_style: dict = Field(default_factory=dict)
    continuity_anchors: dict = Field(default_factory=dict)
    safety_labels: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    source: str
    quality_score: float = 0.0
    created_at: datetime


class TimelineSegmentOut(ORMBase):
    id: str
    timeline_id: str
    index: int
    start_time: float
    end_time: float
    duration: float
    shot_id: Optional[str] = None
    source: str
    file_path: str
    audio_path: Optional[str] = None
    caption: Optional[str] = None
    note: Optional[str] = None


class TimelineOut(ORMBase):
    id: str
    video_id: str
    parent_id: Optional[str] = None
    label: str
    status: str
    branch_reason: Optional[str] = None
    apply_time: Optional[float] = None
    created_at: datetime
    segments: List[TimelineSegmentOut] = Field(default_factory=list)


class StoryStateOut(ORMBase):
    id: str
    timeline_id: str
    time_point: float
    current_event: Optional[str] = None
    previous_events: List[Any] = Field(default_factory=list)
    causal_links: List[Any] = Field(default_factory=list)
    characters_state: Dict[str, Any] = Field(default_factory=dict)
    world_rules: Dict[str, Any] = Field(default_factory=dict)
    location_time: Dict[str, Any] = Field(default_factory=dict)
    open_threads: List[Any] = Field(default_factory=list)
    constraints: List[Any] = Field(default_factory=list)
    summary: Optional[str] = None


class ChatMessageOut(ORMBase):
    id: str
    timeline_id: str
    role: str
    intent: str
    play_time: float
    content: str
    metadata_json: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @field_validator("content", mode="before")
    @classmethod
    def _strip_thinking_echo(cls, v: object) -> object:
        if isinstance(v, str):
            return strip_thinking_blocks(v)
        return v


class ChatRequest(BaseModel):
    timeline_id: str
    play_time: float = 0.0
    content: str
    force_intent: Optional[str] = None  # qa | intervention，可强制
    profile: Optional[str] = None
    #: 用户在确认弹窗中选择「确定」后二次提交，执行叙事干预（避免误触干预）
    confirm_intervention: bool = False


class RegenerateAssistantRequest(BaseModel):
    timeline_id: str
    assistant_message_id: str
    profile: Optional[str] = None


class ChatMessagesDeletedOut(BaseModel):
    deleted: int


class ChatResponse(BaseModel):
    intent: str
    user_message: Optional[ChatMessageOut] = None
    assistant_message: Optional[ChatMessageOut] = None
    #: 为 True 时客户端应询问用户；确认后再带 confirm_intervention 重发同一条 content
    needs_intervention_confirm: bool = False
    intervention_id: Optional[str] = None
    job_id: Optional[str] = None
    #: 叙事干预成功时的新时间线版本 id（与 assistant_message.metadata_json 一致，便于客户端不解析嵌套）
    new_timeline_id: Optional[str] = None
    feasibility_level: Optional[str] = None
    safety_decision: Optional[str] = None
    apply_time: Optional[float] = None
    estimated_seconds: Optional[float] = None
    actual_seconds: Optional[float] = None


class InterventionOut(ORMBase):
    id: str
    timeline_id: str
    user_text: str
    normalized_text: Optional[str] = None
    feasibility_level: str
    safety_decision: str
    safety_reason: Optional[str] = None
    play_time: float
    estimated_gen_seconds: float
    apply_time: Optional[float] = None
    status: str
    rationale: Optional[str] = None
    created_at: datetime


class JobOut(ORMBase):
    id: str
    intervention_id: str
    timeline_id: str
    new_timeline_id: Optional[str] = None
    status: str
    profile: str
    plan: dict = Field(default_factory=dict)
    timeline_log: list = Field(default_factory=list)
    reuse_segments: list = Field(default_factory=list)
    generated_segments: list = Field(default_factory=list)
    estimated_seconds: float = 0.0
    actual_seconds: float = 0.0
    cost_estimate: float = 0.0
    actual_cost: float = 0.0
    fallback_used: bool = False
    continuity_score: float = 0.0
    safety_score: float = 0.0
    quality_score: float = 0.0
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime
    #: 关联干预：用户点击叙事时的播放进度（秒），供前端区分「任务墙钟时间」与「剧情时间」
    playback_position_s: Optional[float] = None
    #: 流水线计算后的分支切入时间（秒），可能与 play_time 略有偏移
    branch_apply_time_s: Optional[float] = None


class MinimaxVoiceUploadOut(BaseModel):
    file_id: str
    purpose: str
    duration_seconds: float


class MinimaxVoiceCloneIn(BaseModel):
    """调用 MiniMax /v1/voice_clone；成功后可将 voice_id 写入成片角色音色。"""

    file_id: str
    #: 用户自定义的克隆音色 id，后续 TTS 的 voice 参数与此一致
    voice_id: str = Field(..., min_length=2, max_length=64)
    #: 复刻时的试听合成文本
    text: str = Field(..., min_length=1, max_length=8000)
    model: Optional[str] = None
    prompt_file_id: Optional[str] = None
    prompt_text: Optional[str] = Field(None, max_length=4000)
    #: 若提供，则将 voice_id 写入该成片下角色的 VoiceProfile
    video_id: Optional[str] = None
    character: Optional[str] = Field(None, max_length=128)


class MinimaxVoiceCloneOut(BaseModel):
    ok: bool = True
    voice_id: str
    clone_raw: Dict[str, Any] = Field(default_factory=dict)
    voice_profile_updated: bool = False


class ModelConfigIn(BaseModel):
    kind: str
    profile: str = "fast"
    name: str
    provider: str
    model: str
    base_url: Optional[str] = None
    api_key_alias: Optional[str] = None
    params: dict = Field(default_factory=dict)
    is_default: bool = False
    enabled: bool = True
    #: 同分类同 profile 下多条配置时越大越优先（仅对已保存密钥且启用的行参与解析）。
    priority: int = 0


class ModelConfigOut(ORMBase):
    id: str
    kind: str
    profile: str
    name: str
    provider: str
    model: str
    base_url: Optional[str] = None
    api_key_alias: Optional[str] = None
    #: 返回给前端的 params **不含** api_key 明文；是否曾写入密钥见 has_api_key。
    params: dict = Field(default_factory=dict)
    has_api_key: bool = False
    is_default: bool
    enabled: bool
    priority: int = 0
    created_at: datetime
    #: ``database`` 为 SQLite 行；``environment`` 为当前进程从环境变量解析的快照（只读展示）。
    source: str = "database"
    read_only: bool = False


class SafetyPolicyIn(BaseModel):
    label: str
    category: str = "forbid"
    keywords: List[str] = Field(default_factory=list)
    description: str = ""
    rewrite_template: Optional[str] = None
    enabled: bool = True


class SafetyPolicyOut(ORMBase):
    id: str
    label: str
    category: str
    keywords: List[str]
    description: str
    rewrite_template: Optional[str] = None
    enabled: bool
    created_at: datetime


class UploadConfig(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    granularities: List[str] = Field(default_factory=lambda: ["1s", "5s", "10s", "scene", "story"])
    scene_threshold: float = 0.4
    sample_fps: float = 1.0
    profile: str = "fast"


class TimelineManifest(BaseModel):
    timeline_id: str
    version: int
    label: str
    status: str
    apply_time: Optional[float] = None
    duration: float
    segments: List[TimelineSegmentOut]
