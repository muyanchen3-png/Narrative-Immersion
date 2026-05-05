import type {
  ChatMessage,
  ChatResponse,
  GenerationJob,
  ModelConfig,
  SafetyPolicy,
  Shot,
  StoryState,
  Timeline,
  TimelineManifest,
  Video,
  IngestProgress,
  VideoCharacter,
  CharacterEnrichBatchResult,
  TranscribeAsrBulkResult,
  MinimaxVoiceUploadOut,
  MinimaxVoiceCloneIn,
  MinimaxVoiceCloneOut,
} from "./types";

/** 生产环境（如 Vercel）填写后端公网根地址；本地开发留空，走 Vite proxy */
const API_ORIGIN = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

function apiUrl(path: string): string {
  if (!path.startsWith("/")) return path;
  return `${API_ORIGIN}${path}`;
}

async function request<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const resolved = typeof input === "string" ? apiUrl(input) : input;
  // 只有非 multipart 请求才强制设 Content-Type；FormData / Blob / URLSearchParams 由浏览器自动处理
  const bodyType = Object.prototype.toString.call(init?.body);
  const isMultipart =
    bodyType === "[object FormData]" ||
    bodyType === "[object Blob]" ||
    bodyType === "[object URLSearchParams]";
  const headers = new Headers(init?.headers);
  if (!isMultipart && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const res = await fetch(resolved, {
    ...init,
    headers,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return (await res.json()) as T;
}

export const api = {
  listVideos: () => request<Video[]>("/api/videos"),
  getVideo: (id: string) => request<Video>(`/api/videos/${id}`),
  /** 删除成片及全部关联（镜头、时间线、角色等）与 uploads 成片文件、切片与角色生成目录 */
  deleteVideo: (id: string) => {
    if (!id?.trim()) throw new Error("缺少成片 ID");
    return request<{ ok: boolean; video_id: string }>(`/api/videos/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
  },
  getShots: (videoId: string, granularity?: string) => {
    const q = granularity ? `?granularity=${encodeURIComponent(granularity)}` : "";
    return request<Shot[]>(`/api/videos/${videoId}/shots${q}`);
  },
  /** 删除单条镜头（切片 mp4 等文件及库记录）；时间线片段上的 shot 引用会清空 */
  deleteShot: (videoId: string, shotId: string) => {
    if (!videoId?.trim() || !shotId?.trim()) throw new Error("缺少成片或镜头 ID");
    return request<{ ok: boolean; video_id: string; shot_id: string }>(
      `/api/videos/${encodeURIComponent(videoId)}/shots/${encodeURIComponent(shotId)}`,
      { method: "DELETE" }
    );
  },
  listTimelines: (videoId: string) =>
    request<Timeline[]>(`/api/videos/${videoId}/timelines`),
  /** 保留成片与切分，仅对该视频全部镜头重新跑分析模型（摘要等），同步时间线字幕 */
  reanalyzeVideoShots: (videoId: string, granularity?: string) => {
    if (!videoId?.trim()) throw new Error("缺少成片 ID");
    const q =
      granularity && granularity.trim()
        ? `?granularity=${encodeURIComponent(granularity.trim())}`
        : "";
    return request<{ video_id: string; shots_updated: number; characters_count?: number }>(
      `/api/videos/reanalyze-all-shots/${encodeURIComponent(videoId)}${q}`,
      { method: "POST" }
    );
  },
  listVideoCharacters: (videoId: string) =>
    request<VideoCharacter[]>(`/api/videos/${encodeURIComponent(videoId)}/characters`),
  extractVideoCharacters: (videoId: string, granularity?: string) => {
    const usp = new URLSearchParams();
    if (granularity?.trim()) usp.set("granularity", granularity.trim());
    const q = usp.toString();
    return request<{ video_id: string; count: number }>(
      `/api/videos/${encodeURIComponent(videoId)}/characters/extract${q ? `?${q}` : ""}`,
      { method: "POST" }
    );
  },
  enrichVideoCharacter: (videoId: string, characterId: string, profile?: string) => {
    const usp = new URLSearchParams();
    if (profile?.trim()) usp.set("profile", profile.trim());
    const q = usp.toString();
    return request<VideoCharacter>(
      `/api/videos/${encodeURIComponent(videoId)}/characters/${encodeURIComponent(characterId)}/enrich${q ? `?${q}` : ""}`,
      { method: "POST" }
    );
  },
  enrichAllVideoCharacters: (
    videoId: string,
    opts?: { profile?: string; onlyPending?: boolean }
  ) => {
    const usp = new URLSearchParams();
    if (opts?.profile?.trim()) usp.set("profile", opts.profile.trim());
    if (opts?.onlyPending === false) usp.set("only_pending", "false");
    const q = usp.toString();
    return request<CharacterEnrichBatchResult>(
      `/api/videos/${encodeURIComponent(videoId)}/characters/enrich-all${q ? `?${q}` : ""}`,
      { method: "POST" }
    );
  },
  /** 上传 / 覆盖角色参照图 */
  uploadCharacterRefImage: (videoId: string, characterId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<VideoCharacter>(
      `/api/videos/${encodeURIComponent(videoId)}/characters/${encodeURIComponent(characterId)}/upload-ref`,
      { method: "POST", body: form as unknown as string }
    );
  },
  /** 删除角色 */
  deleteCharacter: (videoId: string, characterId: string) =>
    request<{ ok: boolean; character_id: string }>(
      `/api/videos/${encodeURIComponent(videoId)}/characters/${encodeURIComponent(characterId)}`,
      { method: "DELETE" }
    ),
  /** 清除角色参照图（保留文件，可重新上传） */
  deleteCharacterRefImage: (videoId: string, characterId: string) =>
    request<{ ok: boolean; character_id: string }>(
      `/api/videos/${encodeURIComponent(videoId)}/characters/${encodeURIComponent(characterId)}/ref-image`,
      { method: "DELETE" }
    ),
  /** 上传/覆盖三视图设定图（不改镜头抽帧参照图） */
  uploadCharacterTurnaroundSheet: (videoId: string, characterId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<VideoCharacter>(
      `/api/videos/${encodeURIComponent(videoId)}/characters/${encodeURIComponent(characterId)}/upload-sheet`,
      { method: "POST", body: form as unknown as string }
    );
  },
  /** 更新角色用户备注等 */
  patchVideoCharacter: (videoId: string, characterId: string, body: { user_notes?: string | null }) =>
    request<VideoCharacter>(
      `/api/videos/${encodeURIComponent(videoId)}/characters/${encodeURIComponent(characterId)}`,
      { method: "PATCH", body: JSON.stringify(body) }
    ),
  /** 手动调整分支叙事切入时刻（秒）并重装该分支片段 */
  setBranchApplyTime: (timelineId: string, apply_time: number) =>
    request<Timeline>(`/api/timelines/${encodeURIComponent(timelineId)}/branch-apply-time`, {
      method: "PUT",
      body: JSON.stringify({ apply_time }),
    }),
  /** 仅重排分支时间线上片段的播放顺序（id 集合须与当前时间线一致） */
  reorderTimelineSegments: (timelineId: string, segment_ids: string[]) =>
    request<Timeline>(`/api/timelines/${encodeURIComponent(timelineId)}/segment-order`, {
      method: "PUT",
      body: JSON.stringify({ segment_ids }),
    }),
  /** 删除分支时间线上的生成/复用/兜底片段（不可删主线剪入段） */
  deleteBranchTimelineSegment: (timelineId: string, segmentId: string) =>
    request<Timeline>(
      `/api/timelines/${encodeURIComponent(timelineId)}/segments/${encodeURIComponent(segmentId)}`,
      { method: "DELETE" }
    ),
  /** 删除分支时间线（非主线）；成功返回 { ok, deleted_id, video_id } */
  deleteTimeline: (timelineId: string) => {
    if (!timelineId?.trim()) throw new Error("缺少时间线 ID");
    return request<{ ok: boolean; deleted_id: string; video_id: string }>(
      `/api/timelines/${encodeURIComponent(timelineId)}`,
      { method: "DELETE" }
    );
  },
  getManifest: (timelineId: string) =>
    request<TimelineManifest>(`/api/timelines/${timelineId}/manifest`),
  getStoryState: (timelineId: string) =>
    request<StoryState>(`/api/timelines/${timelineId}/story-state`),
  listChatMessages: (timelineId: string) =>
    request<ChatMessage[]>(`/api/chat/${timelineId}/messages`),
  /** 删除该时间线下全部对话消息 */
  clearChatMessages: (timelineId: string) =>
    request<{ deleted: number }>(`/api/chat/${timelineId}/messages`, { method: "DELETE" }),
  postChat: (payload: {
    timeline_id: string;
    play_time: number;
    content: string;
    force_intent?: "qa" | "intervention";
    profile?: string;
    /** 用户在确认弹窗中确认执行叙事干预后的第二次请求 */
    confirm_intervention?: boolean;
  }) =>
    request<ChatResponse>("/api/chat", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  listJobs: (timelineId?: string) => {
    const q = timelineId ? `?timeline_id=${encodeURIComponent(timelineId)}` : "";
    return request<GenerationJob[]>(`/api/jobs${q}`);
  },
  getJob: (id: string) => request<GenerationJob>(`/api/jobs/${id}`),

  /** MiniMax：上传复刻/示例短音频，获取 file_id */
  uploadMinimaxVoiceAudio: (purpose: "voice_clone" | "prompt_audio", file: File) => {
    const fd = new FormData();
    fd.append("purpose", purpose);
    fd.append("file", file);
    return request<MinimaxVoiceUploadOut>("/api/voice/minimax/upload", { method: "POST", body: fd });
  },
  /** MiniMax：快速复刻；可选写回某成片角色的 VoiceProfile */
  minimaxVoiceClone: (body: MinimaxVoiceCloneIn) =>
    request<MinimaxVoiceCloneOut>("/api/voice/minimax/clone", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  listAssetShots: (params: {
    video_id?: string;
    granularity?: string;
    source?: string;
    keyword?: string;
  }) => {
    const usp = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v) usp.set(k, v);
    });
    return request<Shot[]>(`/api/assets/shots?${usp.toString()}`);
  },
  listAssetTimelines: (videoId?: string) => {
    const q = videoId ? `?video_id=${encodeURIComponent(videoId)}` : "";
    return request<Timeline[]>(`/api/assets/timelines${q}`);
  },
  /** 重新跑镜头内容理解，刷新摘要等字段并同步时间线字幕 */
  reanalyzeShot: (shotId: string) => {
    if (!shotId?.trim()) throw new Error("缺少镜头 ID");
    return request<Shot>(`/api/assets/shots/${encodeURIComponent(shotId)}/reanalyze`, {
      method: "POST",
    });
  },
  /** 仅从镜头 mp4 抽音轨并做 ASR，合并到 dialogue（需后端配置 HERMES_ASR_*） */
  transcribeShotAsr: (videoId: string, shotId: string) => {
    if (!videoId?.trim() || !shotId?.trim()) throw new Error("缺少成片或镜头 ID");
    return request<Shot>(
      `/api/videos/${encodeURIComponent(videoId)}/shots/${encodeURIComponent(shotId)}/transcribe-asr`,
      { method: "POST" }
    );
  },
  /** 批量音轨 ASR，可选 granularity 与列表筛选项一致 */
  transcribeShotsAsrBulk: (videoId: string, granularity?: string) => {
    if (!videoId?.trim()) throw new Error("缺少成片 ID");
    const q =
      granularity && granularity.trim()
        ? `?granularity=${encodeURIComponent(granularity.trim())}`
        : "";
    return request<TranscribeAsrBulkResult>(
      `/api/videos/${encodeURIComponent(videoId)}/transcribe-asr-bulk${q}`,
      { method: "POST" }
    );
  },

  listModels: () => request<ModelConfig[]>("/api/configs/models"),
  saveModel: (payload: Partial<ModelConfig> & { kind: string; profile: string; name: string; provider: string; model: string }) =>
    request<ModelConfig>("/api/configs/models", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateModel: (id: string, payload: Partial<ModelConfig>) =>
    request<ModelConfig>(`/api/configs/models/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  deleteModel: (id: string) =>
    request<{ ok: boolean }>(`/api/configs/models/${id}`, { method: "DELETE" }),
  /** 从任务计划中删除指定索引的分镜镜头（尚未生成时） */
  deleteJobShot: (jobId: string, shotIndex: number) =>
    request<GenerationJob>(`/api/jobs/${encodeURIComponent(jobId)}/shots/${shotIndex}`, { method: "DELETE" }),

  listSafety: () => request<SafetyPolicy[]>("/api/configs/safety"),
  saveSafety: (payload: Partial<SafetyPolicy> & { label: string; category: string; keywords: string[] }) =>
    request<SafetyPolicy>("/api/configs/safety", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateSafety: (id: string, payload: Partial<SafetyPolicy>) =>
    request<SafetyPolicy>(`/api/configs/safety/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  deleteSafety: (id: string) =>
    request<{ ok: boolean }>(`/api/configs/safety/${id}`, { method: "DELETE" }),

  /** 使用 XHR 以支持 onUploadProgress（已发送字节/总字节） */
  uploadVideo: (formData: FormData, onUploadProgress?: (loaded: number, total: number) => void) =>
    new Promise<Video>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.upload.addEventListener("progress", (e) => {
        if (e.lengthComputable && onUploadProgress && e.total > 0) {
          onUploadProgress(e.loaded, e.total);
        }
      });
      xhr.addEventListener("load", () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText) as Video);
          } catch (err) {
            reject(err instanceof Error ? err : new Error(String(err)));
          }
        } else {
          reject(new Error(xhr.responseText || `${xhr.status}`));
        }
      });
      xhr.addEventListener("error", () => reject(new Error("网络错误，上传失败")));
      xhr.open("POST", apiUrl("/api/videos/upload"));
      xhr.send(formData);
    }),

  getIngestProgress: (videoId: string) =>
    request<IngestProgress>(`/api/videos/${encodeURIComponent(videoId)}/ingest-progress`),
};

export function fileUrl(path: string): string {
  return apiUrl(`/api/assets/file?path=${encodeURIComponent(path)}`);
}

export function formatDuration(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return "0:00";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}
