import { useEffect, useState } from "react";

import { api } from "../lib/api";
import type { GenerationJob } from "../lib/types";

interface Props {
  timelineId: string;
  refreshKey?: number;
  onSwitchTimeline?: (newTimelineId: string, applyTime: number) => void;
}

export default function InterventionFeed({ timelineId, refreshKey, onSwitchTimeline }: Props) {
  const [jobs, setJobs] = useState<GenerationJob[]>([]);

  useEffect(() => {
    api
      .listJobs(timelineId)
      .then(setJobs)
      .catch(() => setJobs([]));
  }, [timelineId, refreshKey]);

  if (!jobs.length) {
    return (
      <div className="rounded-xl border border-ink-900/60 bg-ink-900/40 p-4 text-sm text-ink-400">
        暂无干预任务记录。
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {jobs.map((job) => (
        <div key={job.id} className="rounded-xl border border-ink-900/60 bg-ink-900/40 p-4 text-sm">
          <div className="flex items-center gap-2 mb-2">
            <StatusBadge status={job.status} />
            <span className="text-ink-200">{job.profile} 模式</span>
            <span className="ml-auto text-xs text-ink-400">{job.id.slice(0, 8)}</span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
            <Stat label="预计耗时" value={`${job.estimated_seconds.toFixed(1)}s`} />
            <Stat label="实际耗时" value={`${job.actual_seconds.toFixed(1)}s`} />
            <Stat label="预算成本" value={`￥${job.cost_estimate.toFixed(2)}`} />
            <Stat label="连续性评分" value={job.continuity_score.toFixed(2)} />
            <Stat label="复用片段" value={String(job.reuse_segments?.length || 0)} />
            <Stat label="生成片段" value={String(job.generated_segments?.length || 0)} />
            <Stat label="兜底使用" value={job.fallback_used ? "是" : "否"} />
            <Stat label="安全评分" value={job.safety_score.toFixed(2)} />
          </div>
          <details className="mt-2 text-xs text-ink-200">
            <summary className="cursor-pointer text-ink-400">查看流水线日志</summary>
            <ol className="mt-2 space-y-1">
              {job.timeline_log?.map((step, i) => (
                <li key={i}>
                  <span className="text-amberx-400">[{step.stage}]</span>{" "}
                  <span className="text-ink-100">{String(step.message)}</span>
                </li>
              ))}
            </ol>
          </details>
          {job.new_timeline_id && onSwitchTimeline ? (
            <button
              onClick={() => onSwitchTimeline(job.new_timeline_id!, Number(job.plan?.apply_time ?? 0))}
              className="mt-3 px-3 py-1 rounded bg-amberx-500 text-ink-950 text-xs font-medium"
            >
              切换到该分支 ({(job.new_timeline_id || "").slice(0, 8)})
            </button>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    queued: "bg-slate-500/20 text-slate-200",
    running: "bg-amberx-500/20 text-amberx-300",
    done: "bg-emerald-500/20 text-emerald-300",
    failed: "bg-rose-500/20 text-rose-300",
  };
  return (
    <span className={`px-2 py-0.5 rounded text-xs ${map[status] || "bg-ink-900/60 text-ink-200"}`}>
      {status}
    </span>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded bg-ink-950 px-2 py-1.5 border border-ink-900/60">
      <div className="text-ink-400">{label}</div>
      <div className="text-ink-100 font-mono">{value}</div>
    </div>
  );
}
