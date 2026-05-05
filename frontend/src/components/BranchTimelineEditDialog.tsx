import { useEffect, useMemo, useState } from "react";

import { api, formatDuration } from "../lib/api";
import type { TimelineManifest, TimelineSegment } from "../lib/types";

type Props = {
  open: boolean;
  timelineId: string;
  videoDuration: number;
  initialApply: number;
  onClose: () => void;
  onSaved: () => void | Promise<void>;
};

export default function BranchTimelineEditDialog({
  open,
  timelineId,
  videoDuration,
  initialApply,
  onClose,
  onSaved,
}: Props) {
  const [busy, setBusy] = useState(false);
  const [applyInput, setApplyInput] = useState("");
  const [segments, setSegments] = useState<TimelineSegment[]>([]);
  const [orderedIds, setOrderedIds] = useState<string[]>([]);
  const [dragId, setDragId] = useState<string | null>(null);
  const [baselineApply, setBaselineApply] = useState(initialApply);

  useEffect(() => {
    if (!open || !timelineId) return;
    setBaselineApply(initialApply);
    setApplyInput(String(Number(initialApply).toFixed(2)));
    let cancelled = false;
    api.getManifest(timelineId).then((m: TimelineManifest) => {
      if (cancelled) return;
      const segs = [...m.segments].sort((a, b) => a.index - b.index);
      setSegments(segs);
      setOrderedIds(segs.map((s) => s.id));
    });
    return () => {
      cancelled = true;
    };
  }, [open, timelineId, initialApply]);

  const idToSeg = useMemo(() => Object.fromEntries(segments.map((s) => [s.id, s])), [segments]);

  const baselineIds = useMemo(
    () => [...segments].sort((a, b) => a.index - b.index).map((s) => s.id),
    [segments]
  );

  const orderChanged =
    baselineIds.length > 0 &&
    orderedIds.length === baselineIds.length &&
    orderedIds.some((id, i) => id !== baselineIds[i]);

  const moveItem = (fromIndex: number, toIndex: number) => {
    setOrderedIds((prev) => {
      const next = [...prev];
      const [removed] = next.splice(fromIndex, 1);
      next.splice(toIndex, 0, removed);
      return next;
    });
  };

  const handleDropOn = (targetId: string) => {
    if (!dragId || dragId === targetId) return;
    const from = orderedIds.indexOf(dragId);
    const to = orderedIds.indexOf(targetId);
    if (from < 0 || to < 0) return;
    moveItem(from, to);
    setDragId(null);
  };

  const reloadManifestLocal = async () => {
    const m = await api.getManifest(timelineId);
    const segs = [...m.segments].sort((a, b) => a.index - b.index);
    setSegments(segs);
    setOrderedIds(segs.map((s) => s.id));
  };

  const canDeleteSource = (src: string) =>
    src === "generated" || src === "fallback" || src === "reused";

  const handleDeleteSegment = async (segmentId: string) => {
    if (
      !confirm(
        "确定移除此片段？将从该分支播放序列与关联生成任务中去掉此段（主线剪入段不可删，请改切入时刻）。"
      )
    ) {
      return;
    }
    setBusy(true);
    try {
      await api.deleteBranchTimelineSegment(timelineId, segmentId);
      await reloadManifestLocal();
      await onSaved();
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleSave = async () => {
    const sec = parseFloat(applyInput);
    const applyOk = Number.isFinite(sec);
    const applyChanged = applyOk && Math.abs(sec - baselineApply) > 0.001;

    if (!applyChanged && !orderChanged) {
      onClose();
      return;
    }

    setBusy(true);
    try {
      if (applyChanged) {
        await api.setBranchApplyTime(timelineId, sec);
        await reloadManifestLocal();
        setBaselineApply(sec);
        alert(
          "切入时刻已更新，片段顺序已按生成任务重置。若需自定义播放顺序，请在本窗口内再次拖动后保存。"
        );
      } else if (orderChanged) {
        await api.reorderTimelineSegments(timelineId, orderedIds);
      }
      await onSaved();
      onClose();
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center p-4 bg-black/65 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg rounded-2xl border border-ink-800 bg-ink-950 shadow-xl flex flex-col max-h-[min(90vh,720px)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-4 py-3 border-b border-ink-800 flex items-center justify-between gap-2 shrink-0">
          <div>
            <h3 className="font-medium text-ink-100 text-sm">分支时间线编排</h3>
            <p className="text-[11px] text-ink-500 mt-0.5">
              拖动排序；生成/复用/兜底段可删除。变更切入时刻会按干预任务重装片段（顺序会重置）。
            </p>
          </div>
          <button type="button" onClick={onClose} className="text-ink-500 hover:text-ink-300 text-xs shrink-0">
            关闭
          </button>
        </div>

        <div className="px-4 py-3 space-y-3 overflow-y-auto flex-1 min-h-0">
          <label className="block text-xs text-ink-400">
            叙事切入时刻（秒）
            <div className="flex flex-wrap items-center gap-2 mt-1">
              <input
                type="number"
                step={0.1}
                value={applyInput}
                onChange={(e) => setApplyInput(e.target.value)}
                className="w-32 bg-ink-900 border border-ink-700 rounded-lg px-2 py-1.5 text-ink-100"
              />
              <span className="text-ink-500">
                正片 0 — {videoDuration > 0 ? videoDuration.toFixed(1) : "—"}
              </span>
            </div>
          </label>

          <div>
            <div className="text-[11px] text-ink-500 mb-1.5">片段顺序（拖拽排序）</div>
            <ul className="rounded-xl border border-ink-800/90 divide-y divide-ink-800/80 overflow-hidden">
              {orderedIds.map((id) => {
                const s = idToSeg[id];
                if (!s) return null;
                const deletable = canDeleteSource(s.source);
                return (
                  <li
                    key={id}
                    draggable
                    onDragStart={() => setDragId(id)}
                    onDragOver={(e) => e.preventDefault()}
                    onDrop={() => handleDropOn(id)}
                    className="flex items-start gap-2 px-3 py-2 bg-ink-900/40 hover:bg-ink-900/70 cursor-grab active:cursor-grabbing"
                  >
                    <span className="text-ink-600 select-none pt-0.5">⋮⋮</span>
                    <div className="flex-1 min-w-0 text-xs">
                      <div className="flex items-center gap-2 text-ink-400">
                        <span className="text-[10px] uppercase">{s.source}</span>
                        <span className="tabular-nums">{formatDuration(s.duration)}</span>
                      </div>
                      <div className="text-ink-200 line-clamp-2 mt-0.5">
                        {s.caption?.trim() || s.note?.trim() || s.file_path.split("/").pop() || "片段"}
                      </div>
                    </div>
                    {deletable ? (
                      <button
                        type="button"
                        title="从分支中移除此片段"
                        disabled={busy}
                        onClick={(e) => {
                          e.stopPropagation();
                          void handleDeleteSegment(id);
                        }}
                        className="shrink-0 text-[11px] px-2 py-0.5 rounded border border-rose-500/40 text-rose-300 hover:bg-rose-500/15 disabled:opacity-40"
                      >
                        删除
                      </button>
                    ) : (
                      <span className="shrink-0 text-[10px] text-ink-600 pt-0.5" title="主线剪入段请调整切入时刻">
                        —
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
            {orderedIds.length === 0 ? (
              <p className="text-xs text-ink-500 py-4 text-center">暂无片段数据</p>
            ) : null}
          </div>
        </div>

        <div className="px-4 py-3 border-t border-ink-800 flex justify-end gap-2 shrink-0">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 rounded-lg border border-ink-700 text-ink-300 text-sm hover:bg-ink-900"
          >
            取消
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => void handleSave()}
            className="px-3 py-1.5 rounded-lg bg-sky-600 hover:bg-sky-500 disabled:opacity-50 text-white text-sm"
          >
            {busy ? "保存中…" : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}
