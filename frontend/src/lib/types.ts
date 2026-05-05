/** 成片级角色库条目（由镜头分析 characters 聚合） */
export interface VideoCharacter {
  id: string;
  video_id: string;
  name_key: string;
  display_name: string;
  aliases: string[];
  mention_count: number;
  first_seen_s: number;
  last_seen_s: number;
  source_shot_ids: string[];
  description?: string | null;
  /** 用户自填备注（与系统描述独立） */
  user_notes?: string | null;
  /** 人物身份智能体输出：identity_summary / who_is / appearance_for_art 等 */
  agent_profile: Record<string, unknown>;
  reference_shot_id?: string | null;
  reference_image_path?: string | null;
  reference_video_path?: string | null;
  three_views: Record<string, unknown>;
  enrichment_status: string;
  created_at: string;
  updated_at: string;
}

export interface CharacterEnrichBatchResult {
  video_id: string;
  items: Array<{
    character_id: string;
    ok: boolean;
    enrichment_status: string;
    detail?: string | null;
  }>;
}

export interface IngestProgress {
  video_id: string;
  total: number;
  current: number;
  phase: string;
  error?: string | null;
}

export interface Video {
  id: string;
  title: string;
  description?: string | null;
  duration: number;
  width: number;
  height: number;
  fps: number;
  file_path: string;
  poster_path?: string | null;
  status: string;
  config: Record<string, unknown>;
  created_at: string;
}

/** POST /api/videos/{id}/transcribe-asr-bulk 返回 */
export interface TranscribeAsrBulkResult {
  video_id: string;
  shots_processed: number;
  shots_with_transcript: number;
}

export interface Shot {
  id: string;
  video_id: string;
  granularity: string;
  index: number;
  start_time: number;
  end_time: number;
  duration: number;
  file_path: string;
  thumbnail_path?: string | null;
  audio_path?: string | null;
  summary?: string | null;
  characters: string[];
  location?: string | null;
  actions: string[];
  dialogue?: string | null;
  emotion?: string | null;
  objects: string[];
  visual_style: Record<string, unknown>;
  continuity_anchors: Record<string, unknown>;
  safety_labels: string[];
  tags: string[];
  source: string;
  quality_score: number;
  created_at: string;
}

export interface TimelineSegment {
  id: string;
  timeline_id: string;
  index: number;
  start_time: number;
  end_time: number;
  duration: number;
  shot_id?: string | null;
  source: string;
  file_path: string;
  audio_path?: string | null;
  caption?: string | null;
  note?: string | null;
}

export interface Timeline {
  id: string;
  video_id: string;
  parent_id?: string | null;
  label: string;
  status: string;
  branch_reason?: string | null;
  apply_time?: number | null;
  created_at: string;
  segments: TimelineSegment[];
}

export interface TimelineManifest {
  timeline_id: string;
  version: number;
  label: string;
  status: string;
  apply_time?: number | null;
  duration: number;
  segments: TimelineSegment[];
}

export interface StoryState {
  id: string;
  timeline_id: string;
  time_point: number;
  current_event?: string | null;
  previous_events: unknown[];
  causal_links: unknown[];
  characters_state: Record<string, unknown>;
  world_rules: Record<string, unknown>;
  location_time: Record<string, unknown>;
  open_threads: unknown[];
  constraints: unknown[];
  summary?: string | null;
}

export interface ChatMessage {
  id: string;
  timeline_id: string;
  role: "user" | "assistant" | "system" | "agent";
  intent: string;
  play_time: number;
  content: string;
  metadata_json: Record<string, any>;
  created_at: string;
}

export interface ChatResponse {
  intent: string;
  user_message?: ChatMessage;
  assistant_message?: ChatMessage;
  /** 为 true 时应询问用户，确认后带 confirm_intervention 重发同一条内容 */
  needs_intervention_confirm?: boolean;
  intervention_id?: string | null;
  job_id?: string | null;
  /** 干预成功后新时间线 id（与 assistant_message.metadata_json 一致） */
  new_timeline_id?: string | null;
  feasibility_level?: string | null;
  safety_decision?: string | null;
  apply_time?: number | null;
  estimated_seconds?: number | null;
  actual_seconds?: number | null;
}

/** MiniMax 音色复刻：上传素材返回 file_id */
export interface MinimaxVoiceUploadOut {
  file_id: string;
  purpose: string;
  duration_seconds: number;
}

export interface MinimaxVoiceCloneIn {
  file_id: string;
  voice_id: string;
  text: string;
  model?: string | null;
  prompt_file_id?: string | null;
  prompt_text?: string | null;
  video_id?: string | null;
  character?: string | null;
}

export interface MinimaxVoiceCloneOut {
  ok: boolean;
  voice_id: string;
  clone_raw: Record<string, unknown>;
  voice_profile_updated: boolean;
}

export interface GenerationJob {
  id: string;
  intervention_id: string;
  timeline_id: string;
  new_timeline_id?: string | null;
  status: string;
  profile: string;
  plan: Record<string, any>;
  timeline_log: Array<Record<string, any>>;
  reuse_segments: Array<Record<string, any>>;
  generated_segments: Array<Record<string, any>>;
  estimated_seconds: number;
  actual_seconds: number;
  cost_estimate: number;
  actual_cost: number;
  fallback_used: boolean;
  continuity_score: number;
  safety_score: number;
  quality_score: number;
  error?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  created_at: string;
  /** 发起干预时播放器进度（秒），剧情时间轴 */
  playback_position_s?: number | null;
  /** 流水线写入的分支切入时间（秒） */
  branch_apply_time_s?: number | null;
}

export interface ModelConfig {
  id: string;
  kind: string;
  profile: string;
  name: string;
  provider: string;
  model: string;
  base_url?: string | null;
  api_key_alias?: string | null;
  params: Record<string, unknown>;
  /** 数据库是否保存过 API Key（响应中永不返回密钥内容） */
  has_api_key?: boolean;
  is_default: boolean;
  enabled: boolean;
  /** 同分类同 profile 下越大越优先 */
  priority?: number;
  created_at: string;
  /** database | environment（.env 快照为只读） */
  source?: string;
  read_only?: boolean;
}

export interface SafetyPolicy {
  id: string;
  label: string;
  category: string;
  keywords: string[];
  description: string;
  rewrite_template?: string | null;
  enabled: boolean;
  created_at: string;
}
