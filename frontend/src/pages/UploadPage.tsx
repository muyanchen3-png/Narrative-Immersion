import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../lib/api";

const ALL_GRANULARITIES = ["1s", "5s", "10s", "scene", "story"];

export default function UploadPage() {
  const navigate = useNavigate();
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [granularities, setGranularities] = useState<string[]>(["5s", "scene", "story"]);
  const [sceneThreshold, setSceneThreshold] = useState(0.4);
  const [sampleFps, setSampleFps] = useState(1.0);
  const [profile, setProfile] = useState<"fast" | "quality" | "fallback">("fast");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploadPct, setUploadPct] = useState(0);
  const [uploadDone, setUploadDone] = useState(false);
  const [shotCurrent, setShotCurrent] = useState(0);
  const [shotTotal, setShotTotal] = useState(0);
  const [ingestPhase, setIngestPhase] = useState("");
  const pollTimerRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (pollTimerRef.current != null) {
        window.clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, []);

  const toggle = (g: string) => {
    setGranularities((prev) =>
      prev.includes(g) ? prev.filter((x) => x !== g) : [...prev, g]
    );
  };

  const onSubmit = async () => {
    if (!file) {
      setError("请选择视频文件");
      return;
    }
    setBusy(true);
    setError(null);
    setUploadPct(0);
    setUploadDone(false);
    setShotCurrent(0);
    setShotTotal(0);
    setIngestPhase("");
    const fd = new FormData();
    fd.append("file", file);
    fd.append("title", title || file.name);
    fd.append("description", description);
    fd.append(
      "config",
      JSON.stringify({
        granularities,
        scene_threshold: sceneThreshold,
        sample_fps: sampleFps,
        profile,
      })
    );
    try {
      const v = await api.uploadVideo(fd, (loaded, total) => {
        setUploadPct(Math.round((loaded / total) * 100));
      });
      setUploadDone(true);

      await new Promise<void>((resolve, reject) => {
        const tick = async () => {
          try {
            const p = await api.getIngestProgress(v.id);
            setShotTotal(p.total);
            setShotCurrent(p.current);
            setIngestPhase(p.phase);
            if (p.phase === "error") {
              if (pollTimerRef.current != null) {
                window.clearInterval(pollTimerRef.current);
                pollTimerRef.current = null;
              }
              reject(new Error(p.error || "成片处理失败"));
              return;
            }
            if (p.phase === "done") {
              if (pollTimerRef.current != null) {
                window.clearInterval(pollTimerRef.current);
                pollTimerRef.current = null;
              }
              resolve();
            }
          } catch (e) {
            if (pollTimerRef.current != null) {
              window.clearInterval(pollTimerRef.current);
              pollTimerRef.current = null;
            }
            reject(e);
          }
        };
        void tick();
        pollTimerRef.current = window.setInterval(() => void tick(), 400);
      });

      navigate(`/watch/${v.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (pollTimerRef.current != null) {
        window.clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
      setBusy(false);
      setUploadPct(0);
      setUploadDone(false);
    }
  };

  const barPct = !uploadDone
    ? uploadPct
    : ingestPhase === "finalizing"
      ? 100
      : shotTotal > 0
        ? Math.round((shotCurrent / shotTotal) * 100)
        : 0;

  const statusLabel = !uploadDone
    ? `正在上传 ${uploadPct}%`
    : ingestPhase === "finalizing"
      ? "收尾：生成时间线、剧情状态与角色库…"
      : ingestPhase === "analyzing_shots"
        ? `镜头内容分析 ${shotCurrent} / ${shotTotal}`
        : ingestPhase === "done"
          ? "已完成"
          : ingestPhase === "pending"
            ? "排队处理中…"
            : shotTotal > 0
              ? `镜头内容分析 ${shotCurrent} / ${shotTotal}`
              : "准备切分…";

  return (
    <div className="max-w-3xl mx-auto px-6 py-8 space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">上传视频与切分配置</h1>
        <p className="text-sm text-ink-400 mt-1">
          上传后系统会自动转码、切分多种粒度的镜头、抽取剧情字段，并生成主线时间线和初始剧情状态。
        </p>
      </header>

      <div className="rounded-xl border border-ink-900/60 bg-ink-900/40 p-4 space-y-3 text-sm">
        <Field label="视频文件 (MP4/MOV)">
          <input
            type="file"
            accept="video/*"
            onChange={(e) => setFile(e.target.files?.[0] || null)}
            className="w-full bg-ink-950 border border-ink-900/60 rounded px-2 py-1.5"
          />
          {file ? (
            <div className="text-xs text-ink-400 mt-1">{file.name} · {(file.size / 1024 / 1024).toFixed(1)} MB</div>
          ) : null}
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="标题">
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="为该视频起一个名字"
              className="w-full bg-ink-950 border border-ink-900/60 rounded px-2 py-1.5"
            />
          </Field>
          <Field label="生成模式">
            <select
              value={profile}
              onChange={(e) => setProfile(e.target.value as any)}
              className="w-full bg-ink-950 border border-ink-900/60 rounded px-2 py-1.5"
            >
              <option value="fast">fast：低延时低成本</option>
              <option value="quality">quality：稳定高质</option>
              <option value="fallback">fallback：兜底占位</option>
            </select>
          </Field>
        </div>
        <Field label="描述（可选）">
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={2}
            className="w-full bg-ink-950 border border-ink-900/60 rounded px-2 py-1.5"
          />
        </Field>
      </div>

      <div className="rounded-xl border border-ink-900/60 bg-ink-900/40 p-4 space-y-3 text-sm">
        <h2 className="font-medium">切分颗粒度</h2>
        <p className="text-xs text-ink-400">
          系统会同时按选中的多种粒度切分。短粒度用于精确替换，中粒度用于生成参考，长粒度用于剧情理解。
        </p>
        <div className="flex flex-wrap gap-2">
          {ALL_GRANULARITIES.map((g) => (
            <button
              key={g}
              onClick={() => toggle(g)}
              className={`px-3 py-1 rounded-full border ${
                granularities.includes(g)
                  ? "bg-amberx-500 text-ink-950 border-amberx-500"
                  : "bg-ink-950 text-ink-200 border-ink-900/60"
              }`}
            >
              {g}
            </button>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="场景切分阈值">
            <input
              type="number"
              step={0.05}
              min={0.1}
              max={1.0}
              value={sceneThreshold}
              onChange={(e) => setSceneThreshold(Number(e.target.value))}
              className="w-full bg-ink-950 border border-ink-900/60 rounded px-2 py-1.5"
            />
          </Field>
          <Field label="多模态采样 fps">
            <input
              type="number"
              step={0.5}
              min={0.5}
              max={4}
              value={sampleFps}
              onChange={(e) => setSampleFps(Number(e.target.value))}
              className="w-full bg-ink-950 border border-ink-900/60 rounded px-2 py-1.5"
            />
          </Field>
        </div>
      </div>

      {error ? <div className="text-rose-300 text-sm">{error}</div> : null}

      {busy ? (
        <div
          className="rounded-xl border border-ink-900/60 bg-ink-950/50 p-4 space-y-2"
          role="status"
          aria-live="polite"
          aria-busy="true"
        >
          <div className="flex justify-between gap-3 text-xs text-ink-400">
            <span className="min-w-0">{statusLabel}</span>
            <span className="tabular-nums shrink-0 text-ink-300">{Math.min(100, barPct)}%</span>
          </div>
          <div className="h-2.5 rounded-full bg-ink-900 border border-ink-800/80 overflow-hidden">
            <div
              className="h-full rounded-full bg-gradient-to-r from-amberx-600 to-amberx-400 transition-[width] duration-300 ease-out"
              style={{ width: `${Math.min(100, barPct)}%` }}
            />
          </div>
        </div>
      ) : null}

      <button
        disabled={busy}
        onClick={onSubmit}
        className="px-5 py-2 rounded-lg bg-amberx-500 text-ink-950 font-medium disabled:opacity-50"
      >
        {busy ? "处理中…" : "上传并开始处理"}
      </button>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="text-xs text-ink-400 mb-1">{label}</div>
      {children}
    </label>
  );
}
