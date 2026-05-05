import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";

import { fileUrl, formatDuration } from "../lib/api";
import type { TimelineSegment } from "../lib/types";

export interface PlayerState {
  playing: boolean;
  virtualClock: number;
  duration: number;
  currentSegmentIndex: number;
  currentSource: string;
}

export interface PlayerHandle {
  seek: (t: number) => void;
  pause: () => void;
  play: () => void;
}

interface Props {
  /** 切换叙事版本时传入，用于重置双缓冲与进度 */
  timelineId?: string;
  segments: TimelineSegment[];
  duration: number;
  onTimeUpdate?: (t: number) => void;
  onStateChange?: (state: PlayerState) => void;
  pendingSeek?: number | null;
  fillContainer?: boolean;
}

/**
 * 双缓冲播放：当前段在 slot A 播放时，slot B 已预加载下一段；ended 时切换槽位并立即 play，
 * 避免单 video 换 src 造成的「一段一卡」。
 */
const VideoTimelinePlayer = forwardRef<PlayerHandle, Props>(function VideoTimelinePlayer(
  { timelineId, segments, duration, onTimeUpdate, onStateChange, pendingSeek, fillContainer },
  ref
) {
  const shellRef = useRef<HTMLDivElement>(null);
  const videoRef0 = useRef<HTMLVideoElement>(null);
  const videoRef1 = useRef<HTMLVideoElement>(null);
  /** 当前画面所在的槽位 0 | 1（另一槽预加载下一段） */
  const leadSlotRef = useRef(0);

  const [leadSlot, setLeadSlot] = useState(0);
  const [virtualClock, setVirtualClock] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [currentIdx, setCurrentIdx] = useState(0);
  const [scrubTime, setScrubTime] = useState<number | null>(null);

  const currentIdxRef = useRef(0);
  currentIdxRef.current = currentIdx;

  const lastTimelineIdRef = useRef<string | undefined>(undefined);

  const currentSegment = segments[currentIdx];
  const totalDuration = Math.max(duration, 0.001);
  const segmentCount = segments.length;

  const getLeadVideo = () => (leadSlotRef.current === 0 ? videoRef0.current : videoRef1.current);
  const getFollowVideo = () => (leadSlotRef.current === 0 ? videoRef1.current : videoRef0.current);

  const findSegmentIndex = (t: number): number => {
    if (!segments.length) return 0;
    if (t <= 0) return 0;
    if (t >= segments[segments.length - 1].end_time) return segments.length - 1;
    for (let i = 0; i < segments.length; i++) {
      if (t >= segments[i].start_time && t < segments[i].end_time) return i;
    }
    return segments.length - 1;
  };

  const seekTo = (t: number) => {
    const idx = findSegmentIndex(t);
    const seg = segments[idx];
    if (!seg) return;

    leadSlotRef.current = 0;
    setLeadSlot(0);
    setCurrentIdx(idx);
    setVirtualClock(t);

    requestAnimationFrame(() => {
      const v0 = videoRef0.current;
      const v1 = videoRef1.current;
      const local = Math.max(0, Math.min(seg.duration, t - seg.start_time));

      if (v0) {
        v0.src = fileUrl(seg.file_path);
        v0.load();
        const setTime = () => {
          v0.currentTime = local;
          v0.removeEventListener("loadedmetadata", setTime);
        };
        v0.addEventListener("loadedmetadata", setTime);
      }

      const nextSeg = segments[idx + 1];
      if (v1 && nextSeg) {
        v1.src = fileUrl(nextSeg.file_path);
        v1.load();
      } else if (v1) {
        v1.removeAttribute("src");
        v1.load();
      }
    });
  };

  useImperativeHandle(ref, () => ({
    seek: (t: number) => seekTo(t),
    pause: () => getLeadVideo()?.pause(),
    play: () => {
      void getLeadVideo()?.play().catch(() => {});
    },
  }));

  useEffect(() => {
    if (pendingSeek !== null && pendingSeek !== undefined) {
      seekTo(pendingSeek);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingSeek]);

  useEffect(() => {
    if (!timelineId || !segments.length) return;
    if (lastTimelineIdRef.current === timelineId) return;
    lastTimelineIdRef.current = timelineId;
    seekTo(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timelineId, segments.length]);

  const handleTimeUpdate = () => {
    const v = getLeadVideo();
    const idx = currentIdxRef.current;
    const seg = segments[idx];
    if (!v || !seg) return;
    const local = v.currentTime;
    const newClock = seg.start_time + local;
    setVirtualClock(newClock);
    onTimeUpdate?.(newClock);
  };

  const handleEnded = () => {
    const idx = currentIdxRef.current;
    if (idx >= segments.length - 1) {
      setPlaying(false);
      onStateChange?.({
        playing: false,
        virtualClock: duration,
        duration,
        currentSegmentIndex: idx,
        currentSource: segments[idx]?.source || "",
      });
      return;
    }

    const nextIdx = idx + 1;
    const newLead = 1 - leadSlotRef.current;
    leadSlotRef.current = newLead;
    setLeadSlot(newLead);
    setCurrentIdx(nextIdx);
    setVirtualClock(segments[nextIdx].start_time);

    requestAnimationFrame(() => {
      const vLead = newLead === 0 ? videoRef0.current : videoRef1.current;
      const vFollow = newLead === 0 ? videoRef1.current : videoRef0.current;
      void vLead?.play().catch(() => {});

      const segAfter = segments[nextIdx + 1];
      if (vFollow && segAfter) {
        vFollow.src = fileUrl(segAfter.file_path);
        vFollow.load();
      } else if (vFollow) {
        vFollow.removeAttribute("src");
        vFollow.load();
      }
    });
  };

  useEffect(() => {
    onStateChange?.({
      playing,
      virtualClock,
      duration,
      currentSegmentIndex: currentIdx,
      currentSource: currentSegment?.source || "",
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, virtualClock, currentIdx, segments]);

  const togglePlay = () => {
    const v = getLeadVideo();
    if (!v) return;
    if (v.paused) void v.play().catch(() => {});
    else v.pause();
  };

  const displayClock = scrubTime !== null ? scrubTime : virtualClock;

  const shell = fillContainer
    ? "relative h-full min-h-[200px] w-full bg-zinc-950 rounded-2xl overflow-hidden border border-zinc-800 shadow-2xl shadow-black/40 ring-1 ring-white/[0.03]"
    : "relative aspect-video w-full bg-zinc-950 rounded-2xl overflow-hidden border border-zinc-800 shadow-2xl shadow-black/40 ring-1 ring-white/[0.03]";

  /** 双槽各自套一层 flex 居中，避免部分浏览器下 video+absolute+max-* 出现画面缩在左上角的问题 */
  const slotStage = "absolute inset-0 flex items-center justify-center bg-black overflow-hidden";
  const videoFit = "max-h-full max-w-full object-contain object-center bg-black";

  const leadLayer = "z-[2] opacity-100";
  const followLayer = "z-[1] opacity-0 pointer-events-none";

  const toggleFs = () => {
    const el = shellRef.current;
    if (!el) return;
    if (!document.fullscreenElement) void el.requestFullscreen?.().catch(() => {});
    else void document.exitFullscreen?.().catch(() => {});
  };

  return (
    <div ref={shellRef} className={`${shell} flex flex-col`}>
      <div className="relative flex-1 min-h-0 w-full bg-black overflow-hidden">
        {segments.length === 0 ? (
          <div className="w-full h-full flex items-center justify-center text-zinc-500 text-sm">
            暂无可播放片段
          </div>
        ) : (
          <>
            <div className={`${slotStage} ${leadSlot === 0 ? leadLayer : followLayer}`}>
              <video
                ref={videoRef0}
                className={videoFit}
                onPlay={() => leadSlotRef.current === 0 && setPlaying(true)}
                onPause={() => leadSlotRef.current === 0 && setPlaying(false)}
                onEnded={() => leadSlotRef.current === 0 && handleEnded()}
                onTimeUpdate={() => leadSlotRef.current === 0 && handleTimeUpdate()}
                playsInline
                preload="auto"
              />
            </div>
            <div className={`${slotStage} ${leadSlot === 1 ? leadLayer : followLayer}`}>
              <video
                ref={videoRef1}
                className={videoFit}
                onPlay={() => leadSlotRef.current === 1 && setPlaying(true)}
                onPause={() => leadSlotRef.current === 1 && setPlaying(false)}
                onEnded={() => leadSlotRef.current === 1 && handleEnded()}
                onTimeUpdate={() => leadSlotRef.current === 1 && handleTimeUpdate()}
                playsInline
                preload="auto"
              />
            </div>
          </>
        )}
        {segments.length > 0 && currentSegment ? (
          <div className="absolute bottom-3 left-0 right-0 flex justify-center pointer-events-none px-4 z-10">
            <div className="bg-black/55 backdrop-blur-md px-4 py-2 rounded-xl text-sm max-w-[90%] text-center border border-white/5 leading-snug">
              {currentSegment.caption?.trim() ? (
                <span className="text-zinc-100">{currentSegment.caption}</span>
              ) : (
                <span className="text-zinc-500">字幕失败</span>
              )}
            </div>
          </div>
        ) : null}
      </div>

      <div className="shrink-0 z-20 flex flex-col gap-1 px-2 py-2 bg-gradient-to-t from-black/90 via-black/70 to-transparent border-t border-white/5">
        <div className="flex items-center gap-2">
          <input
            type="range"
            min={0}
            max={totalDuration}
            step={0.05}
            value={Math.min(displayClock, totalDuration)}
            onPointerDown={() => setScrubTime(virtualClock)}
            onChange={(e) => setScrubTime(Number(e.target.value))}
            onPointerUp={(e) => {
              const t = Number((e.target as HTMLInputElement).value);
              setScrubTime(null);
              seekTo(t);
            }}
            className="flex-1 h-1.5 accent-sky-500 rounded-full cursor-pointer"
            aria-label="全片进度"
          />
        </div>
        <div className="flex items-center gap-2 text-[11px] text-zinc-300">
          <button
            type="button"
            onClick={togglePlay}
            className="shrink-0 w-9 h-9 rounded-lg bg-white/10 hover:bg-white/15 border border-white/10 flex items-center justify-center"
            aria-label={playing ? "暂停" : "播放"}
          >
            {playing ? <span className="text-xs">❚❚</span> : <span className="text-sm pl-0.5">▶</span>}
          </button>
          <span className="font-mono tabular-nums shrink-0">
            {formatDuration(displayClock)} / {formatDuration(duration)}
          </span>
          {segmentCount > 0 ? (
            <span className="text-zinc-500 truncate">
              第 {currentIdx + 1}/{segmentCount} 段 · 本段 {formatDuration(currentSegment?.duration ?? 0)}
            </span>
          ) : null}
          <span className="ml-auto flex items-center gap-1 shrink-0">
            <button
              type="button"
              onClick={toggleFs}
              className="px-2 py-1 rounded-md bg-white/10 hover:bg-white/15 text-zinc-200 text-[10px] border border-white/10"
            >
              全屏
            </button>
          </span>
        </div>
      </div>
    </div>
  );
});

export default VideoTimelinePlayer;
