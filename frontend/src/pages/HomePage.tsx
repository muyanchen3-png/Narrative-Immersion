import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import PlaybackWorkspace from "../components/PlaybackWorkspace";
import { api, fileUrl, formatDuration } from "../lib/api";
import type { Video } from "../lib/types";

export default function HomePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [videos, setVideos] = useState<Video[]>([]);
  const [loading, setLoading] = useState(true);
  const [pickerOpen, setPickerOpen] = useState(false);

  const qVid = searchParams.get("v");

  useEffect(() => {
    api
      .listVideos()
      .then(setVideos)
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!pickerOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setPickerOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pickerOpen]);

  const selectedId = useMemo(() => {
    if (qVid && videos.some((v) => v.id === qVid)) return qVid;
    return videos[0]?.id ?? "";
  }, [qVid, videos]);

  const selectedVideo = useMemo(
    () => videos.find((v) => v.id === selectedId) ?? null,
    [videos, selectedId]
  );

  const setVideoParam = useCallback(
    (id: string) => {
      setSearchParams(id ? { v: id } : {}, { replace: true });
    },
    [setSearchParams]
  );

  const pickVideo = (id: string) => {
    setVideoParam(id);
    setPickerOpen(false);
  };

  return (
    <div className="flex flex-col flex-1 min-h-0 max-w-[1600px] mx-auto w-full px-4 sm:px-6 py-4 sm:py-5">
      <header className="shrink-0 flex flex-col sm:flex-row sm:items-center gap-3 sm:gap-4 border-b border-zinc-800/80 pb-4">
        <div className="min-w-0 flex-1">
          <p className="text-xs uppercase tracking-[0.2em] text-sky-500/90 mb-1">交互叙事 · 长视频沉浸体验</p>
          <h1 className="text-xl sm:text-2xl font-semibold text-zinc-50 tracking-tight">放映厅</h1>
          <p className="text-sm text-zinc-500 mt-1 max-w-xl">
            观看并按你的想法追问剧情或推动叙事走向。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 shrink-0">
          <button
            type="button"
            disabled={loading || videos.length === 0}
            onClick={() => setPickerOpen(true)}
            className="inline-flex items-center justify-center gap-2 px-4 py-2 rounded-xl bg-zinc-800 hover:bg-zinc-700 border border-zinc-600 text-sm font-medium text-zinc-100 disabled:opacity-40 disabled:pointer-events-none transition-colors"
          >
            <span className="text-zinc-400">成片</span>
            <span className="truncate max-w-[12rem] sm:max-w-[16rem]">
              {loading ? "加载中…" : selectedVideo ? selectedVideo.title : "选择成片"}
            </span>
            <svg className="w-4 h-4 text-zinc-500 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </button>
          <Link
            to="/upload"
            className="text-sm px-3 py-2 rounded-xl text-sky-400/90 hover:text-sky-300 hover:bg-sky-500/10 transition-colors"
          >
            上传成片
          </Link>
        </div>
      </header>

      <section className="mt-4 flex flex-col min-h-0 overflow-hidden h-[calc(100dvh-10.5rem)] max-h-[calc(100dvh-10.5rem)]">
        {loading ? (
          <div className="flex-1 flex items-center justify-center text-zinc-500 text-sm rounded-2xl border border-zinc-800 bg-zinc-900/30">
            加载成片列表…
          </div>
        ) : videos.length === 0 ? (
          <div className="flex-1 flex flex-col items-center justify-center rounded-2xl border border-zinc-800 bg-zinc-900/40 px-6 py-12 text-center">
            <p className="text-zinc-400 text-sm">暂无成片，请先上传或等待处理完成。</p>
            <Link to="/upload" className="inline-block mt-4 text-sm text-sky-400 hover:text-sky-300">
              去上传 →
            </Link>
          </div>
        ) : selectedId ? (
          <PlaybackWorkspace videoId={selectedId} />
        ) : null}
      </section>

      {pickerOpen ? (
        <div
          className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-3 sm:p-6 bg-black/60 backdrop-blur-sm"
          role="dialog"
          aria-modal="true"
          aria-labelledby="picker-title"
          onClick={() => setPickerOpen(false)}
        >
          <div
            className="w-full max-w-2xl max-h-[85dvh] flex flex-col rounded-2xl border border-zinc-700 bg-zinc-950 shadow-2xl shadow-black/50 overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-zinc-800 shrink-0">
              <h2 id="picker-title" className="text-base font-medium text-zinc-100">
                选择成片
              </h2>
              <button
                type="button"
                onClick={() => setPickerOpen(false)}
                className="p-2 rounded-lg text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800"
                aria-label="关闭"
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="overflow-y-auto p-3 sm:p-4 grid grid-cols-1 sm:grid-cols-2 gap-3">
              {videos.map((v) => {
                const active = v.id === selectedId;
                return (
                  <button
                    key={v.id}
                    type="button"
                    onClick={() => pickVideo(v.id)}
                    className={`text-left rounded-xl overflow-hidden border transition-colors ${
                      active
                        ? "border-sky-500/70 ring-1 ring-sky-500/30 bg-zinc-900/90"
                        : "border-zinc-800 bg-zinc-900/50 hover:border-zinc-600 hover:bg-zinc-900/80"
                    }`}
                  >
                    <div className="aspect-video bg-zinc-950 flex items-center justify-center">
                      {v.poster_path ? (
                        <img src={fileUrl(v.poster_path)} alt="" className="w-full h-full object-cover" />
                      ) : (
                        <span className="text-xs text-zinc-600">无封面</span>
                      )}
                    </div>
                    <div className="p-3">
                      <div className="text-sm font-medium text-zinc-100 line-clamp-2 leading-snug">{v.title}</div>
                      <div className="text-[11px] text-zinc-500 mt-1">{formatDuration(v.duration)}</div>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
