import { useEffect, useState } from "react";

import { api } from "../lib/api";
import type { StoryState } from "../lib/types";

interface Props {
  timelineId: string;
  refreshKey?: number;
}

export default function StoryStateBar({ timelineId, refreshKey }: Props) {
  const [state, setState] = useState<StoryState | null>(null);

  useEffect(() => {
    api
      .getStoryState(timelineId)
      .then(setState)
      .catch(() => setState(null));
  }, [timelineId, refreshKey]);

  if (!state) {
    return (
      <div className="rounded-xl border border-ink-900/60 bg-ink-900/40 p-4 text-sm text-ink-400">
        剧情状态生成中...
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-ink-900/60 bg-ink-900/40 p-4 text-sm space-y-3">
      <div className="flex items-center gap-2 text-ink-200">
        <span className="px-2 py-0.5 rounded bg-amberx-500/20 text-amberx-400 text-xs">剧情状态</span>
        <span className="text-xs text-ink-400">@ {state.time_point.toFixed(1)}s</span>
      </div>
      {state.summary ? <p className="text-ink-100 whitespace-pre-wrap">{state.summary}</p> : null}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
        <Field label="当前事件">{state.current_event || "—"}</Field>
        <Field label="地点 / 时间">
          {String(state.location_time?.location || "—")} · {String(state.location_time?.time || "")}
        </Field>
        <Field label="角色目标">
          <ul className="space-y-0.5">
            {Object.entries(state.characters_state || {}).map(([k, v]: any) => (
              <li key={k}>
                <span className="text-amberx-400">{k}</span>：目标={v?.goal ?? "—"}，情绪={v?.emotion ?? "—"}
              </li>
            ))}
            {Object.keys(state.characters_state || {}).length === 0 ? <li>—</li> : null}
          </ul>
        </Field>
        <Field label="世界规则">
          {Object.entries(state.world_rules || {})
            .map(([k, v]) => `${k}=${String(v)}`)
            .join(" · ") || "—"}
        </Field>
        <Field label="未解悬念">
          {(state.open_threads || []).join(" / ") || "—"}
        </Field>
        <Field label="不可破坏的约束">
          {(state.constraints || []).join(" / ") || "—"}
        </Field>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-ink-400 mb-0.5">{label}</div>
      <div className="text-ink-100">{children}</div>
    </div>
  );
}
