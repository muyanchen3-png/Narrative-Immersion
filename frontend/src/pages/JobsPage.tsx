import { useEffect, useState } from "react";

import { api } from "../lib/api";
import type { GenerationJob } from "../lib/types";

/** 将 job.plan 以可读结构展示，避免把整坨 JSON 丢给用户。 */
function JobPlanView({
  plan,
  jobId,
  onDeleteShot,
}: {
  plan: Record<string, unknown> | null | undefined;
  jobId: string;
  onDeleteShot: (index: number) => void;
}) {
  if (!plan || typeof plan !== "object") {
    return <p className="text-ink-400 text-xs">无分镜计划数据</p>;
  }
  const director = plan.director as Record<string, unknown> | undefined;
  const branch = plan.branch as Record<string, unknown> | undefined;
  const shots = plan.shots as unknown[] | undefined;
  const apSched = plan.apply_schedule as Record<string, unknown> | undefined;
  const vg = plan.video_generation as Record<string, unknown> | undefined;
  const riskNotes = plan.risk_notes as unknown[] | undefined;
  const costPlan = plan.cost_plan as unknown;
  const shotFallback = plan.shot_fallback;
  const shotsCount = plan.shots_count;

  return (
    <div className="space-y-3 text-xs text-ink-200">
      {typeof shotFallback === "string" || typeof shotFallback === "number" ? (
        <p>
          <span className="text-ink-400">分镜来源：</span>
          {String(shotFallback)}
        </p>
      ) : null}
      {typeof shotsCount === "number" ? (
        <p>
          <span className="text-ink-400">镜头条数：</span> {shotsCount}
        </p>
      ) : null}

      {branch && typeof branch === "object" ? (
        <div className="rounded-lg border border-ink-800 bg-ink-950/50 p-2">
          <div className="text-ink-400 text-[10px] uppercase tracking-wide mb-1">编剧 · 分支</div>
          {typeof branch.summary === "string" && branch.summary ? (
            <p className="text-ink-100 leading-relaxed mb-2">{branch.summary}</p>
          ) : null}
          {Array.isArray(branch.outline) && branch.outline.length > 0 ? (
            <ol className="list-decimal pl-4 space-y-1 text-ink-200">
              {(branch.outline as string[]).map((line, i) => (
                <li key={i}>{line}</li>
              ))}
            </ol>
          ) : null}
        </div>
      ) : null}

      {director && typeof director === "object" && Array.isArray(director.shots_plan) && (director.shots_plan as unknown[]).length > 0 ? (
        <div className="rounded-lg border border-ink-800 bg-ink-950/50 p-2">
          <div className="text-ink-400 text-[10px] uppercase tracking-wide mb-1">导演计划</div>
          <ul className="space-y-1">
            {(director.shots_plan as Record<string, unknown>[]).map((row, i) => (
              <li key={i} className="text-ink-200">
                <span className="text-ink-500">{(row.role as string) || "镜头"}：</span>
                {String(row.summary || row.description || "—")}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {Array.isArray(shots) && shots.length > 0 ? (
        <div className="rounded-lg border border-ink-800 bg-ink-950/50 p-2">
          <div className="flex justify-between items-center mb-1">
            <span className="text-ink-400 text-[10px] uppercase tracking-wide">分镜镜头</span>
            <span className="text-ink-500 text-[10px]">未生成时可删除</span>
          </div>
          <ul className="space-y-2 max-h-52 overflow-y-auto scroll-thin">
            {shots.map((raw, i) => {
              const s = raw as Record<string, unknown>;
              const dlg = s.dialogue as { character?: string; line?: string }[] | undefined;
              return (
                <li key={i} className="border-b border-ink-900/80 pb-2 last:border-0">
                  <div className="flex justify-between gap-2 items-start">
                    <span className="font-medium text-amberx-200/90">#{i + 1}</span>
                    <div className="flex items-center gap-2">
                      <span className="text-ink-400">{typeof s.duration === "number" ? `${s.duration}s` : ""}</span>
                      <button
                        type="button"
                        title="从计划中删除此镜头"
                        onClick={() => onDeleteShot(i)}
                        className="text-ink-500 hover:text-rose-400 transition-colors text-xs px-1.5 py-0.5 rounded border border-ink-800 hover:border-rose-500/50"
                      >
                        删除
                      </button>
                    </div>
                  </div>
                  {typeof s.summary === "string" ? (
                    <p className="text-ink-100 mt-0.5">{s.summary}</p>
                  ) : null}
                  {typeof s.subject === "string" || typeof s.action === "string" ? (
                    <p className="text-ink-400 text-[11px] mt-0.5">
                      {[s.subject, s.action].filter(Boolean).join(" · ")}
                    </p>
                  ) : null}
                  {typeof s.voice_over === "string" && s.voice_over ? (
                    <p className="text-ink-300 text-[11px] mt-0.5">旁白：{s.voice_over}</p>
                  ) : null}
                  {Array.isArray(dlg) && dlg.length > 0 ? (
                    <ul className="mt-1 text-[11px] text-ink-300 space-y-0.5">
                      {dlg.map((d, j) => (
                        <li key={j}>
                          {d.character ? <span className="text-cyan-300/90">{d.character}：</span> : null}
                          {typeof d.line === "string"
                            ? d.line
                            : typeof d === "object" && d && "line" in d
                              ? String((d as { line?: unknown }).line ?? "")
                              : String(d)}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}

      {apSched && typeof apSched === "object" ? (
        <div className="rounded-lg border border-sky-900/40 bg-sky-950/20 p-2">
          <div className="text-sky-400/90 text-[10px] uppercase tracking-wide mb-1">切入编排</div>
          {typeof apSched.apply_time === "number" ? (
            <p>剧情切入约 {Number(apSched.apply_time).toFixed(1)} s</p>
          ) : null}
          {typeof apSched.rationale === "string" ? (
            <p className="text-ink-300 mt-1">{apSched.rationale}</p>
          ) : null}
          {typeof apSched.llm === "boolean" ? (
            <p className="text-ink-500 mt-1">{apSched.llm ? "由编排模型给出" : "规则估算"}</p>
          ) : null}
        </div>
      ) : null}

      {vg && typeof vg === "object" ? (
        <div className="rounded-lg border border-ink-800 bg-ink-950/50 p-2">
          <div className="text-ink-400 text-[10px] uppercase tracking-wide mb-1">视频生成</div>
          <p className="text-ink-300 text-[11px] leading-snug">
            {(typeof vg.provider === "string" ? vg.provider : "—") +
              " · " +
              (typeof vg.model === "string" ? vg.model : "—")}
            {typeof vg.fallback_clip_count === "number"
              ? ` · 兜底片段 ${vg.fallback_clip_count}`
              : ""}
            {vg.real_minimax === true ? " · MiniMax 实拍" : ""}
          </p>
        </div>
      ) : null}

      {Array.isArray(riskNotes) && riskNotes.length > 0 ? (
        <div className="text-[11px] text-amber-200/80">
          <span className="text-ink-400">风险备注：</span>
          {riskNotes.map((x, i) => (
            <span key={i}>{String(x)}{i < riskNotes.length - 1 ? "；" : ""}</span>
          ))}
        </div>
      ) : null}

      {costPlan !== undefined && costPlan !== null ? (
        <details className="text-[11px] text-ink-500">
          <summary className="cursor-pointer text-ink-400">成本明细（展开）</summary>
          <pre className="mt-1 whitespace-pre-wrap break-all bg-ink-950 p-2 rounded border border-ink-900 max-h-32 overflow-auto">
            {typeof costPlan === "object" ? JSON.stringify(costPlan, null, 2) : String(costPlan)}
          </pre>
        </details>
      ) : null}
    </div>
  );
}

function fmtTimelineSec(sec?: number | null): string {
  if (sec == null || Number.isNaN(sec)) return "—";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export default function JobsPage() {
  const [jobs, setJobs] = useState<GenerationJob[]>([]);
  const [active, setActive] = useState<GenerationJob | null>(null);

  const reload = () => api.listJobs().then(setJobs);

  const handleDeleteShot = (index: number) => {
    if (!active) return;
    if (!window.confirm(`确定要从计划中删除第 ${index + 1} 条镜头吗？（尚未生成的镜头可删除）`)) return;
    api.deleteJobShot(active.id, index).then((updated) => {
      setActive(updated);
      setJobs((prev) => prev.map((j) => (j.id === updated.id ? updated : j)));
    }).catch((err) => {
      alert("删除失败：" + (err instanceof Error ? err.message : String(err)));
    });
  };

  useEffect(() => {
    reload();
    const t = setInterval(reload, 4000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="max-w-7xl mx-auto px-6 py-8 grid grid-cols-1 lg:grid-cols-3 gap-6">
      <div className="lg:col-span-2 space-y-2">
        <h1 className="text-2xl font-semibold mb-2">干预任务流水线</h1>
        <p className="text-xs text-ink-400 mb-3">
          列表按「完成 → 开始 → 创建」时间倒序。标注「剧情切入」为成片内时间点（非服务器时钟）。
        </p>
        {jobs.length === 0 ? (
          <div className="text-ink-400">尚无任务。</div>
        ) : (
          <div className="space-y-2">
            {jobs.map((j) => (
              <button
                key={j.id}
                onClick={() => setActive(j)}
                className={`w-full text-left rounded-xl border ${
                  active?.id === j.id ? "border-amberx-400/50 bg-amberx-500/5" : "border-ink-900/60 bg-ink-900/40"
                } p-3 text-sm`}
              >
                <div className="flex items-center gap-2 flex-wrap">
                  <span
                    className={`px-2 py-0.5 rounded text-xs ${
                      j.status === "done"
                        ? "bg-emerald-500/20 text-emerald-300"
                        : j.status === "failed"
                        ? "bg-rose-500/20 text-rose-300"
                        : "bg-amberx-500/20 text-amberx-300"
                    }`}
                  >
                    {j.status}
                  </span>
                  <span className="text-ink-200">{j.profile}</span>
                  <span className="text-xs text-ink-400 ml-auto text-right">
                    <span className="block">
                      完成 {j.finished_at ? new Date(j.finished_at).toLocaleString() : "—"}
                    </span>
                    <span className="block text-ink-500">
                      剧情切入 {fmtTimelineSec(j.branch_apply_time_s ?? j.playback_position_s)}
                      {j.playback_position_s != null && j.branch_apply_time_s != null && Math.abs(j.branch_apply_time_s - j.playback_position_s) > 0.5 ? (
                        <span className="text-ink-600">（发起 {fmtTimelineSec(j.playback_position_s)}）</span>
                      ) : null}
                    </span>
                  </span>
                </div>
                <div className="text-xs text-ink-200 mt-2 grid grid-cols-4 gap-2">
                  <span>预计 {j.estimated_seconds.toFixed(1)}s</span>
                  <span>实际 {j.actual_seconds.toFixed(1)}s</span>
                  <span>预算 ￥{j.cost_estimate.toFixed(2)}</span>
                  <span>连续性 {j.continuity_score.toFixed(2)}</span>
                </div>
                <div className="text-[10px] text-ink-500 mt-1">
                  任务开始 {j.started_at ? new Date(j.started_at).toLocaleString() : "—"} · 创建{" "}
                  {new Date(j.created_at).toLocaleString()}
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
      <div className="lg:col-span-1">
        {active ? (
          <div className="rounded-xl border border-ink-900/60 bg-ink-900/40 p-4 text-sm">
            <h2 className="font-medium mb-2">任务详情</h2>
            <div className="text-xs text-ink-400 mb-3">id: {active.id}</div>
            <h3 className="text-ink-200 mt-2">流水线日志</h3>
            <ol className="space-y-1 mt-1 text-xs">
              {(active.timeline_log || []).map((s, i) => (
                <li key={i}>
                  <span className="text-amberx-400">[{s.stage}]</span>{" "}
                  <span className="text-ink-100">{String(s.message)}</span>
                </li>
              ))}
            </ol>
            <h3 className="text-ink-200 mt-3">分镜计划</h3>
            <div className="bg-ink-950 p-2 rounded border border-ink-900/60 max-h-72 overflow-y-auto scroll-thin">
              <JobPlanView
                plan={active.plan as Record<string, unknown>}
                jobId={active.id}
                onDeleteShot={handleDeleteShot}
              />
            </div>
            <h3 className="text-ink-200 mt-3">复用片段</h3>
            <ul className="text-xs space-y-1">
              {(active.reuse_segments || []).map((r: any, i: number) => (
                <li key={i}>
                  <span className="text-emerald-300">[{(r.score ?? 0).toFixed(2)}]</span>{" "}
                  {r.caption || r.shot_id || "复用片段"}
                  <span className="text-ink-400"> · {(r.reasons || []).join(", ")}</span>
                </li>
              ))}
              {(active.reuse_segments || []).length === 0 ? <li className="text-ink-400">无复用</li> : null}
            </ul>
            <h3 className="text-ink-200 mt-3">生成片段</h3>
            <ul className="text-xs space-y-1">
              {(active.generated_segments || []).map((g: any, i: number) => (
                <li key={i}>
                  <span className={g.fallback ? "text-rose-300" : "text-amberx-300"}>
                    [{g.fallback ? "兜底" : "生成"}]
                  </span>{" "}
                  {g.caption || g.prompts?.video_prompt}
                </li>
              ))}
              {(active.generated_segments || []).length === 0 ? <li className="text-ink-400">无生成</li> : null}
            </ul>
          </div>
        ) : (
          <div className="text-sm text-ink-400">点击左侧任务查看详细流水线。</div>
        )}
      </div>
    </div>
  );
}
