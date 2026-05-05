import { useEffect, useMemo, useState } from "react";

import BranchTimelineEditDialog from "./BranchTimelineEditDialog";
import ChatPanel from "./ChatPanel";
import VideoTimelinePlayer from "./VideoTimelinePlayer";
import { api, formatDuration } from "../lib/api";
import type { ChatResponse, Timeline, TimelineManifest, TimelineSegment, Video } from "../lib/types";

const EMPTY_SEGMENTS: TimelineSegment[] = [];

/**
 * 赛题主体验：选片时间线、连续播放、对话（问答/改写剧情）。
 * 不展示干预流水线与分轨条，分支切换在后台完成。
 */
export default function PlaybackWorkspace({ videoId }: { videoId: string }) {
  const [video, setVideo] = useState<Video | null>(null);
  const [timelines, setTimelines] = useState<Timeline[]>([]);
  const [currentTimelineId, setCurrentTimelineId] = useState("");
  const [manifest, setManifest] = useState<TimelineManifest | null>(null);
  const [virtualClock, setVirtualClock] = useState(0);
  const [pendingSeek, setPendingSeek] = useState<number | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  interface PendingBranch {
    newTimelineId: string;
    applyTime: number;
  }
  const [pending, setPending] = useState<PendingBranch | null>(null);
  const [branchOrchestrateOpen, setBranchOrchestrateOpen] = useState(false);

  useEffect(() => {
    api.getVideo(videoId).then(setVideo);
    api.listTimelines(videoId).then((tls) => {
      setTimelines(tls);
      if (tls.length) {
        const main = tls.find((t) => t.label === "主线") || tls[0];
        setCurrentTimelineId((id) => id || main.id);
      }
    });
  }, [videoId]);

  useEffect(() => {
    if (!currentTimelineId) return;
    api.getManifest(currentTimelineId).then(setManifest);
  }, [currentTimelineId, refreshKey]);

  useEffect(() => {
    if (!pending) return;
    if (virtualClock >= pending.applyTime - 0.05) {
      setCurrentTimelineId(pending.newTimelineId);
      setPendingSeek(pending.applyTime);
      setPending(null);
      setRefreshKey((k) => k + 1);
      api.listTimelines(videoId).then(setTimelines);
    }
  }, [virtualClock, pending, videoId]);

  const reloadTimelines = () => api.listTimelines(videoId).then(setTimelines);

  const currentTimeline = useMemo(
    () => timelines.find((t) => t.id === currentTimelineId) ?? null,
    [timelines, currentTimelineId]
  );

  const handleDeleteCurrentBranch = async () => {
    if (!currentTimeline?.parent_id) return;
    if (
      !confirm(
        `删除当前叙事版本「${currentTimeline.label}」？将移除该分支下时间线、对话与关联记录，不可恢复。`
      )
    ) {
      return;
    }
    try {
      await api.deleteTimeline(currentTimelineId);
      const tls = await api.listTimelines(videoId);
      setTimelines(tls);
      const main = tls.find((t) => t.label === "主线") || tls[0];
      if (main) {
        setCurrentTimelineId(main.id);
        setPending(null);
        setRefreshKey((k) => k + 1);
      } else {
        setCurrentTimelineId("");
      }
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    }
  };

  const handleIntervention = (resp: ChatResponse) => {
    if (resp.intent === "intervention" && resp.apply_time != null && resp.job_id) {
      reloadTimelines();
      setRefreshKey((k) => k + 1);
      const nid =
        resp.new_timeline_id ??
        resp.assistant_message?.metadata_json?.new_timeline_id;
      if (nid) {
        setPending({
          newTimelineId: nid,
          applyTime: Number(resp.apply_time),
        });
      }
    }
  };

  const timelineLabel = useMemo(
    () => timelines.find((t) => t.id === currentTimelineId)?.label ?? "",
    [timelines, currentTimelineId]
  );

  const segments = manifest?.segments ?? EMPTY_SEGMENTS;
  const manifestDuration = manifest?.duration ?? 0;

  return (
    <div className="flex flex-col lg:flex-row flex-1 min-h-0 gap-4 lg:gap-5 lg:items-stretch overflow-hidden">
      <div className="flex flex-col min-w-0 min-h-0 flex-1 lg:basis-0 gap-3 overflow-hidden">
        <div className="flex flex-wrap items-center gap-3 text-sm shrink-0">
          <h2 className="text-lg font-medium text-zinc-100 truncate">{video?.title ?? "…"}</h2>
          <span className="text-zinc-500 shrink-0">{formatDuration(video?.duration ?? 0)}</span>
          {timelines.length > 1 ? (
            <label className="flex items-center gap-2 ml-auto text-zinc-400 min-w-0">
              <span className="text-xs uppercase tracking-wider shrink-0">叙事版本</span>
              <select
                className="bg-zinc-900/80 border border-zinc-700/80 rounded-lg px-3 py-1.5 text-zinc-200 text-sm focus:outline-none focus:ring-1 focus:ring-sky-500/40 max-w-[12rem]"
                value={currentTimelineId}
                onChange={(e) => {
                  setCurrentTimelineId(e.target.value);
                  setPendingSeek(0);
                  setPending(null);
                }}
              >
                {timelines.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.label}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {currentTimeline?.parent_id ? (
            <button
              type="button"
              title="删除当前分支（主线不可删）"
              onClick={() => void handleDeleteCurrentBranch()}
              className="shrink-0 text-xs px-2.5 py-1 rounded-md border border-red-500/40 text-red-300/90 hover:bg-red-500/10"
            >
              删此分支
            </button>
          ) : null}
        </div>
        {timelineLabel ? (
          <p className="text-xs text-zinc-500 -mt-1 shrink-0">{timelineLabel}</p>
        ) : null}

        {currentTimeline?.parent_id && video ? (
          <>
            <div className="shrink-0">
              <button
                type="button"
                onClick={() => setBranchOrchestrateOpen(true)}
                className="w-full sm:w-auto px-3 py-1.5 rounded-lg border border-sky-500/40 text-sky-200 text-xs hover:bg-sky-500/10"
              >
                编辑分支编排（切入 / 片段顺序）
              </button>
            </div>
            {branchOrchestrateOpen && currentTimelineId ? (
              <BranchTimelineEditDialog
                open
                timelineId={currentTimelineId}
                videoDuration={video.duration}
                initialApply={typeof currentTimeline.apply_time === "number" ? currentTimeline.apply_time : 0}
                onClose={() => setBranchOrchestrateOpen(false)}
                onSaved={async () => {
                  setRefreshKey((k) => k + 1);
                  const tls = await api.listTimelines(videoId);
                  setTimelines(tls);
                }}
              />
            ) : null}
          </>
        ) : null}

        <div className="flex-1 min-h-[220px] lg:min-h-0 flex flex-col">
          <VideoTimelinePlayer
            timelineId={currentTimelineId || undefined}
            segments={segments}
            duration={manifestDuration}
            pendingSeek={pendingSeek}
            fillContainer
            onTimeUpdate={(t) => {
              setVirtualClock(t);
              setPendingSeek(null);
            }}
          />
        </div>
      </div>

      <div className="flex flex-col flex-1 min-h-0 lg:h-full lg:w-[min(420px,38vw)] lg:min-w-[300px] lg:max-w-[440px] shrink-0 lg:flex-none overflow-hidden">
        {currentTimelineId ? (
          <ChatPanel
            timelineId={currentTimelineId}
            playTime={virtualClock}
            onIntervention={handleIntervention}
          />
        ) : (
          <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-6 text-zinc-500 text-sm h-full min-h-[200px] flex items-center justify-center">
            正在加载时间线…
          </div>
        )}
      </div>
    </div>
  );
}
