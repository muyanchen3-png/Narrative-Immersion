import { useEffect, useMemo, useState } from "react";

import BranchTimelineEditDialog from "../components/BranchTimelineEditDialog";
import { api, fileUrl, formatDuration } from "../lib/api";
import type { Shot, Timeline, Video, VideoCharacter } from "../lib/types";

const GRANULARITIES = ["", "1s", "5s", "10s", "scene", "story"];
const SOURCES = ["", "origin", "reused", "generated", "fallback"];

const CAST_PAGE_SIZES = [6, 9, 12, 18] as const;
const SHOTS_PAGE_SIZES = [9, 12, 18, 24, 36] as const;

type LibraryTab = "cast" | "shots" | "timelines";

function LibraryTabButton({
  label,
  count,
  active,
  onClick,
}: {
  label: string;
  count: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={`px-4 py-2.5 rounded-lg text-sm font-medium transition-colors border ${
        active
          ? "bg-ink-800/90 text-ink-100 border-ink-600/70 shadow-sm"
          : "text-ink-400 border-transparent hover:text-ink-200 hover:bg-ink-900/70"
      }`}
    >
      {label}
      <span className="ml-1.5 text-xs font-normal tabular-nums text-ink-500">· {count}</span>
    </button>
  );
}

function paginateSlice<T>(items: T[], page: number, pageSize: number): { slice: T[]; totalPages: number; pageSafe: number } {
  const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
  const pageSafe = Math.min(Math.max(1, page), totalPages);
  const start = (pageSafe - 1) * pageSize;
  return { slice: items.slice(start, start + pageSize), totalPages, pageSafe };
}

function PaginationFooter(props: {
  page: number;
  totalPages: number;
  total: number;
  pageSize: number;
  pageSizes: readonly number[];
  onPageChange: (p: number) => void;
  onPageSizeChange: (n: number) => void;
  itemLabel?: string;
}) {
  const {
    page,
    totalPages,
    total,
    pageSize,
    pageSizes,
    onPageChange,
    onPageSizeChange,
    itemLabel = "条",
  } = props;
  if (total === 0) return null;
  const startIdx = (page - 1) * pageSize + 1;
  const endIdx = Math.min(total, page * pageSize);
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 mt-4 pt-3 border-t border-ink-900/50 text-xs text-ink-400">
      <span>
        共 {total} {itemLabel}，本页 {startIdx}–{endIdx} · 第 {page} / {totalPages} 页
      </span>
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1.5">
          <span className="text-ink-500">每页</span>
          <select
            value={pageSize}
            onChange={(e) => onPageSizeChange(Number(e.target.value))}
            className="bg-ink-950 border border-ink-900/60 rounded px-2 py-1 text-ink-200"
          >
            {pageSizes.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
          className="px-2.5 py-1 rounded border border-ink-800/80 text-ink-200 hover:bg-ink-900/80 disabled:opacity-35 disabled:pointer-events-none"
        >
          上一页
        </button>
        <button
          type="button"
          disabled={page >= totalPages}
          onClick={() => onPageChange(page + 1)}
          className="px-2.5 py-1 rounded border border-ink-800/80 text-ink-200 hover:bg-ink-900/80 disabled:opacity-35 disabled:pointer-events-none"
        >
          下一页
        </button>
      </div>
    </div>
  );
}

/** 后端字段 enrichment_status；用于判断是否已跑完富化流水线 */
const ENRICHMENT_UI: Record<
  string,
  { label: string; hint: string; tone: "muted" | "warn" | "ok" | "run" }
> = {
  pending: {
    label: "未富化",
    hint: "尚未「富化」：无三视图与智能体人物摘要；参照帧由证据镜头自动截取，进入本页或「从镜头汇总」后会补全。",
    tone: "muted",
  },
  analyzing: {
    label: "处理中",
    hint: "正在拉镜头、跑智能体或生图，请稍候。",
    tone: "run",
  },
  partial: {
    label: "部分完成",
    hint: "例如：人物摘要已有但三视图为占位图，或参照镜头已变需重新富化。",
    tone: "warn",
  },
  visual_ready: {
    label: "已生成",
    hint: "流水线已跑完：磁盘上已有三视图文件（若为 MiniMax/OpenAI 失败则可能仍是占位图）。",
    tone: "ok",
  },
  failed: {
    label: "失败",
    hint: "上次富化出错（如抽帧失败），可单条重试。",
    tone: "warn",
  },
};

function enrichmentToneClass(tone: (typeof ENRICHMENT_UI)[string]["tone"]): string {
  switch (tone) {
    case "ok":
      return "border-emerald-500/40 bg-emerald-500/15 text-emerald-200";
    case "warn":
      return "border-amber-500/40 bg-amber-500/10 text-amber-200";
    case "run":
      return "border-sky-500/40 bg-sky-500/10 text-sky-200";
    default:
      return "border-ink-700/80 bg-ink-950/80 text-ink-400";
  }
}

function characterAssetChecks(c: VideoCharacter) {
  const hasRef = Boolean(c.reference_image_path?.trim());
  const tv = c.three_views as Record<string, unknown> | undefined;
  const sheetVal = tv?.sheet;
  const hasSheet = typeof sheetVal === "string" && sheetVal.length > 0;
  const prof = c.agent_profile as Record<string, unknown> | undefined;
  const hasSummary =
    typeof prof?.identity_summary === "string" && (prof.identity_summary as string).trim().length > 0;
  const sheetSource = typeof tv?.source === "string" ? tv.source : undefined;
  return { hasRef, hasSheet, hasSummary, sheetSource };
}

export default function AssetLibraryPage() {
  const [videos, setVideos] = useState<Video[]>([]);
  const [videoId, setVideoId] = useState<string>("");
  /** 默认「全部」：避免仅筛 scene 时与实际上传的粒度不一致导致列表为空（角色库无粒度时会优先用 scene→story→10s…）。 */
  const [granularity, setGranularity] = useState<string>("");
  const [source, setSource] = useState<string>("");
  const [keyword, setKeyword] = useState("");
  const [shots, setShots] = useState<Shot[]>([]);
  const [timelines, setTimelines] = useState<Timeline[]>([]);
  const [activeShot, setActiveShot] = useState<Shot | null>(null);
  const [regenerateBusy, setRegenerateBusy] = useState(false);
  const [asrBulkBusy, setAsrBulkBusy] = useState(false);
  const [deleteVideoBusy, setDeleteVideoBusy] = useState(false);
  const [cast, setCast] = useState<VideoCharacter[]>([]);
  const [extractBusy, setExtractBusy] = useState(false);
  const [enrichAllBusy, setEnrichAllBusy] = useState(false);
  /** 正在单独富化中的角色 id（可多任务并行） */
  const [enrichBusyIds, setEnrichBusyIds] = useState<string[]>([]);
  const [castPage, setCastPage] = useState(1);
  const [castPageSize, setCastPageSize] = useState(9);
  const [shotsPage, setShotsPage] = useState(1);
  const [shotsPageSize, setShotsPageSize] = useState(12);
  const [libraryTab, setLibraryTab] = useState<LibraryTab>("shots");
  const [deleteTimelineBusyId, setDeleteTimelineBusyId] = useState<string | null>(null);
  const [orchestrateTimelineId, setOrchestrateTimelineId] = useState<string | null>(null);

  useEffect(() => {
    api.listVideos().then((vs) => {
      setVideos(vs);
      if (vs.length && !videoId) setVideoId(vs[0].id);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const reloadAssetData = () => {
    if (!videoId) return;
    api
      .listAssetShots({
        video_id: videoId,
        granularity: granularity || undefined,
        source: source || undefined,
        keyword: keyword || undefined,
      })
      .then(setShots);
    api.listAssetTimelines(videoId).then(setTimelines);
    api.listVideoCharacters(videoId).then(setCast).catch(() => setCast([]));
  };

  useEffect(() => {
    reloadAssetData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [videoId, granularity, source, keyword]);

  useEffect(() => {
    setCastPage(1);
    setShotsPage(1);
  }, [videoId]);

  useEffect(() => {
    setShotsPage(1);
  }, [granularity, source, keyword]);

  const castPageData = useMemo(
    () => paginateSlice(cast, castPage, castPageSize),
    [cast, castPage, castPageSize]
  );
  const shotsPageData = useMemo(
    () => paginateSlice(shots, shotsPage, shotsPageSize),
    [shots, shotsPage, shotsPageSize]
  );

  useEffect(() => {
    if (castPageData.pageSafe !== castPage) {
      setCastPage(castPageData.pageSafe);
    }
  }, [castPageData.pageSafe, castPage]);

  useEffect(() => {
    if (shotsPageData.pageSafe !== shotsPage) {
      setShotsPage(shotsPageData.pageSafe);
    }
  }, [shotsPageData.pageSafe, shotsPage]);

  const currentVideo = useMemo(() => videos.find((v) => v.id === videoId) ?? null, [videos, videoId]);

  const castEnrichSummary = useMemo(() => {
    const total = cast.length;
    const ready = cast.filter((c) => (c.enrichment_status || "pending") === "visual_ready").length;
    const hasPic = cast.filter((c) => {
      const { hasSheet } = characterAssetChecks(c);
      return hasSheet;
    }).length;
    return { total, ready, hasPic };
  }, [cast]);

  const handleEnrichAllCharacters = async () => {
    if (!videoId || enrichAllBusy) return;
    if (
      !confirm(
        "将基于每个角色关联镜头的字幕/摘要运行人物身份分析，并抽取参照帧、生成三视图（调用 LLM 与生图，可能较久）。是否继续？"
      )
    ) {
      return;
    }
    setEnrichAllBusy(true);
    try {
      const r = await api.enrichAllVideoCharacters(videoId, { onlyPending: true });
      setCast(await api.listVideoCharacters(videoId));
      const fail = r.items.filter((i) => !i.ok);
      if (fail.length) {
        alert(
          `完成：成功 ${r.items.length - fail.length}，失败 ${fail.length}。首条错误：${fail[0].detail || "未知"}`
        );
      }
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      setEnrichAllBusy(false);
    }
  };

  const handleEnrichOne = async (characterId: string) => {
    if (!videoId || enrichAllBusy) return;
    if (enrichBusyIds.includes(characterId)) return;
    setEnrichBusyIds((prev) => [...prev, characterId]);
    try {
      await api.enrichVideoCharacter(videoId, characterId);
      setCast(await api.listVideoCharacters(videoId));
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      setEnrichBusyIds((prev) => prev.filter((id) => id !== characterId));
    }
  };

  const handleExtractCharacters = async () => {
    if (!videoId || extractBusy) return;
    setExtractBusy(true);
    try {
      const r = await api.extractVideoCharacters(videoId, granularity || undefined);
      setCast(await api.listVideoCharacters(videoId));
      if (r.count === 0) {
        alert("未汇总到角色：请先完成镜头内容分析（镜头的「人物」字段需非空）。");
      }
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      setExtractBusy(false);
    }
  };

  const handleDeleteCharacter = async (characterId: string) => {
    if (!videoId) return;
    if (!confirm("删除后不可恢复，确定要删除这个角色吗？")) return;
    try {
      await api.deleteCharacter(videoId, characterId);
      setCast((prev) => prev.filter((c) => c.id !== characterId));
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    }
  };

  const [editCharId, setEditCharId] = useState<string | null>(null);
  const [sheetUploadBusyId, setSheetUploadBusyId] = useState<string | null>(null);

  const handleRegenerateAllShots = async () => {
    if (!videoId || regenerateBusy) return;
    if (
      !confirm(
        "将保留成片与当前切分，仅对所有已有镜头重新生成摘要、人物、地点等分析字段（调用模型，可能耗时较长）。是否继续？"
      )
    ) {
      return;
    }
    setRegenerateBusy(true);
    try {
      const r = await api.reanalyzeVideoShots(videoId, granularity || undefined);
      await api.listVideos().then(setVideos);
      reloadAssetData();
      if (r.shots_updated === 0) {
        alert("当前视频下没有镜头记录，请先完成上传切分。");
      }
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      setRegenerateBusy(false);
    }
  };

  const handleDeleteEntireVideo = async () => {
    if (!videoId || deleteVideoBusy) return;
    const v = videos.find((x) => x.id === videoId);
    const label = v?.title?.trim() || "该成片";
    if (
      !confirm(
        `确定删除成片「${label}」的全部数据？\n\n将移除：时间线与对话、全部镜头记录、成片角色库、音色档案等；并删除服务器上的成片文件、切片目录及该成片角色的生成图目录。\n\n此操作不可恢复。`
      )
    ) {
      return;
    }
    setDeleteVideoBusy(true);
    try {
      await api.deleteVideo(videoId);
      const next = await api.listVideos();
      setVideos(next);
      setVideoId((prev) => {
        if (next.length === 0) return "";
        if (next.some((x) => x.id === prev)) return prev;
        return next[0].id;
      });
      setActiveShot(null);
      setShots([]);
      setTimelines([]);
      setCast([]);
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      setDeleteVideoBusy(false);
    }
  };

  const handleDeleteTimelineBranch = async (t: Timeline) => {
    if (!t.parent_id || deleteTimelineBusyId) return;
    if (
      !confirm(
        `删除叙事分支「${t.label}」？\n\n将移除该时间线下的片段、对话、剧情状态及关联的干预生成记录；不可恢复。主线不受影响。`
      )
    ) {
      return;
    }
    setDeleteTimelineBusyId(t.id);
    try {
      await api.deleteTimeline(t.id);
      await api.listAssetTimelines(videoId).then(setTimelines);
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      setDeleteTimelineBusyId(null);
    }
  };

  const handleTranscribeAllAsr = async () => {
    if (!videoId || asrBulkBusy) return;
    if (
      !confirm(
        "对当前筛选下的全部镜头从音轨识别对白（不上传新文件，调用 ASR；镜头多时可能较久）。需在服务端配置 HERMES_ASR_PROVIDER 等。是否继续？"
      )
    ) {
      return;
    }
    setAsrBulkBusy(true);
    try {
      const r = await api.transcribeShotsAsrBulk(videoId, granularity || undefined);
      reloadAssetData();
      alert(`已处理 ${r.shots_processed} 条镜头，其中 ${r.shots_with_transcript} 条得到非空识别文本。`);
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      setAsrBulkBusy(false);
    }
  };

  return (
    <div className="max-w-7xl mx-auto px-6 py-8 space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">媒资库</h1>
        <p className="text-sm text-ink-400 mt-1">
          先选成片，再用上方筛选项；下方分栏切换「成片角色库 / 镜头 / 时间线」，每次只专注一类内容。
        </p>
      </header>

      <div className="rounded-xl border border-ink-900/60 bg-ink-900/40 p-4 grid grid-cols-1 md:grid-cols-4 gap-3 text-sm">
        <Field label="视频">
          <select
            value={videoId}
            onChange={(e) => setVideoId(e.target.value)}
            className="w-full min-w-0 bg-ink-950 border border-ink-900/60 rounded px-2 py-1.5"
          >
            {videos.map((v) => (
              <option key={v.id} value={v.id}>
                {v.title}
              </option>
            ))}
          </select>
        </Field>
        <Field label="切分粒度">
          <select
            value={granularity}
            onChange={(e) => setGranularity(e.target.value)}
            className="w-full bg-ink-950 border border-ink-900/60 rounded px-2 py-1.5"
          >
            {GRANULARITIES.map((g) => (
              <option key={g} value={g}>
                {g || "全部"}
              </option>
            ))}
          </select>
        </Field>
        <Field label="来源">
          <select
            value={source}
            onChange={(e) => setSource(e.target.value)}
            className="w-full bg-ink-950 border border-ink-900/60 rounded px-2 py-1.5"
          >
            {SOURCES.map((s) => (
              <option key={s} value={s}>
                {s || "全部"}
              </option>
            ))}
          </select>
        </Field>
        <Field label="关键词">
          <input
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            placeholder="人物 / 地点 / 摘要"
            className="w-full bg-ink-950 border border-ink-900/60 rounded px-2 py-1.5"
          />
        </Field>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-red-900/35 bg-red-950/20 px-4 py-3 text-sm">
        <p className="text-ink-400 max-w-xl">
          <span className="text-red-300/90 font-medium">危险操作：</span>
          删除当前所选成片的全部媒资数据（含成片文件与生成产物），不可撤销。
        </p>
        <button
          type="button"
          disabled={!videoId || deleteVideoBusy || videos.length === 0}
          onClick={() => void handleDeleteEntireVideo()}
          className="shrink-0 px-4 py-2 rounded-lg border border-red-500/45 bg-red-500/15 text-red-200 text-sm hover:bg-red-500/25 disabled:opacity-40"
        >
          {deleteVideoBusy ? "删除中…" : "删除该成片及全部内容"}
        </button>
      </div>

      <nav
        className="flex flex-wrap gap-1 p-1.5 rounded-xl border border-ink-900/60 bg-ink-950/40"
        role="tablist"
        aria-label="媒资分栏"
      >
        <LibraryTabButton
          label="成片角色库"
          count={cast.length}
          active={libraryTab === "cast"}
          onClick={() => setLibraryTab("cast")}
        />
        <LibraryTabButton
          label="镜头"
          count={shots.length}
          active={libraryTab === "shots"}
          onClick={() => setLibraryTab("shots")}
        />
        <LibraryTabButton
          label="时间线"
          count={timelines.length}
          active={libraryTab === "timelines"}
          onClick={() => setLibraryTab("timelines")}
        />
      </nav>

      {libraryTab === "cast" ? (
        <p className="text-xs text-ink-500 mt-2 mb-0">
          当前「切分粒度」会参与「从镜头汇总角色」使用的镜头集合。
        </p>
      ) : libraryTab === "shots" ? (
        <p className="text-xs text-ink-500 mt-2 mb-0">
          粒度 / 来源 / 关键词仅影响下列镜头列表与「重新生成镜头分析」范围。
        </p>
      ) : (
        <p className="text-xs text-ink-500 mt-2 mb-0">该片下的时间线版本（主线与分支）。</p>
      )}

      {libraryTab === "cast" && (
      <section>
        <div className="flex flex-wrap items-center gap-3 mb-2">
          <h2 className="text-lg font-medium">
            成片角色库（{castEnrichSummary.total}）
            {castEnrichSummary.total > 0 ? (
              <span className="text-xs font-normal text-ink-500 ml-2">
                已标记「已生成」{castEnrichSummary.ready} / {castEnrichSummary.total} · 有三视图文件{" "}
                {castEnrichSummary.hasPic} / {castEnrichSummary.total}
              </span>
            ) : null}
          </h2>
          <button
            type="button"
            disabled={!videoId || extractBusy}
            onClick={() => void handleExtractCharacters()}
            title="根据当前筛选粒度下的镜头，汇总 characters 字段并写入本条成片专属角色表"
            className="px-3 py-1.5 rounded-lg border border-emerald-500/40 bg-emerald-500/10 text-emerald-200 text-xs hover:bg-emerald-500/20 disabled:opacity-40"
          >
            {extractBusy ? "提取中…" : "从镜头汇总角色"}
          </button>
          <button
            type="button"
            disabled={!videoId || enrichAllBusy || cast.length === 0}
            onClick={() => void handleEnrichAllCharacters()}
            title="根据镜头字幕/摘要分析人物身份，抽帧参照图并生成三视图设定"
            className="px-3 py-1.5 rounded-lg border border-sky-500/40 bg-sky-500/10 text-sky-200 text-xs hover:bg-sky-500/20 disabled:opacity-40"
          >
            {enrichAllBusy ? "富化中…" : "批量富化角色"}
          </button>
        </div>
        <p className="text-xs text-ink-500 mb-3 space-y-1">
          <span className="block">
            角色名单<strong>只来自</strong>各镜头分析里的 <code className="text-ink-300">characters</code>{" "}
            字段（即「该镜头画面里出现谁」），不会单开剧情推断列。若发现混入了仅台词提到、未出镜的人，请对镜头
            「重新生成分析」后再点「从镜头汇总角色」。
          </span>
          <span className="block text-ink-400">
            <strong className="text-ink-300 font-medium">如何看出有没有生成：</strong>
            每张卡片上的<strong>彩色标签</strong>对应后端状态「已生成 / 未富化 / 部分完成」；下面的<strong>
              产出检查
            </strong>
            三项（参照帧 · 三视图 · 身份摘要）打勾表示接口已写入路径或摘要。三视图右侧小字「API」表示来自 MiniMax/OpenAI；
            「占位」表示走了本地占位图（真实 API 未成功或未配置）。
          </span>
        </p>
        {cast.length === 0 ? (
          <p className="text-sm text-ink-500 rounded-xl border border-ink-900/60 bg-ink-900/30 px-4 py-6">
            暂无角色条目。请先完成上传与镜头分析，或点击「从镜头汇总角色」。
          </p>
        ) : (
          <>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {castPageData.slice.map((c) => {
              const st = c.enrichment_status || "pending";
              const meta = ENRICHMENT_UI[st] ?? {
                label: st,
                hint: "未知状态码，可查看接口返回的 enrichment_status。",
                tone: "muted" as const,
              };
              const { hasRef, hasSheet, hasSummary, sheetSource } = characterAssetChecks(c);
              return (
              <div
                key={c.id}
                className="rounded-xl border border-ink-900/60 bg-ink-900/40 p-3 text-sm"
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <div className="font-medium text-ink-100">{c.display_name}</div>
                    {c.user_notes?.trim() ? (
                      <p className="text-[11px] text-ink-400 mt-1 line-clamp-2" title={c.user_notes}>
                        备注：{c.user_notes}
                      </p>
                    ) : null}
                  </div>
                  <div className="flex flex-wrap gap-1">
                    <button
                      type="button"
                      disabled={enrichBusyIds.includes(c.id) || enrichAllBusy}
                      onClick={() => void handleEnrichOne(c.id)}
                      className="shrink-0 px-2 py-0.5 rounded border border-sky-500/35 text-sky-200 text-[11px] hover:bg-sky-500/15 disabled:opacity-40"
                    >
                      {enrichBusyIds.includes(c.id) ? "富化中…" : "富化"}
                    </button>
                    <button
                      type="button"
                      onClick={() => setEditCharId(c.id)}
                      className="shrink-0 px-2 py-0.5 rounded border border-emerald-500/35 text-emerald-200 text-[11px] hover:bg-emerald-500/15"
                    >
                      编辑
                    </button>
                    <button
                      type="button"
                      onClick={() => handleDeleteCharacter(c.id)}
                      title="删除该角色（不可恢复）"
                      className="shrink-0 px-2 py-0.5 rounded border border-red-500/35 text-red-300 text-[11px] hover:bg-red-500/15"
                    >
                      删除
                    </button>
                  </div>
                </div>
                {c.aliases.length > 0 ? (
                  <div className="text-xs text-ink-500 mt-1">别名：{c.aliases.join("、")}</div>
                ) : null}
                <div className="flex flex-wrap items-center gap-2 mt-2">
                  <span
                    title={meta.hint}
                    className={`text-[11px] px-2 py-0.5 rounded border ${enrichmentToneClass(meta.tone)}`}
                  >
                    {meta.label}
                  </span>
                  <span className="text-[10px] text-ink-500" title={meta.hint}>
                    （{st}）
                  </span>
                </div>
                <div className="text-[11px] text-ink-400 mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5">
                  <span title="镜头抽取的参照图是否已写入">
                    参照帧 {hasRef ? "✓" : "—"}
                  </span>
                  <span title="三视图 PNG 是否已写入（含占位图）">
                    三视图 {hasSheet ? "✓" : "—"}
                  </span>
                  <span title="人物身份智能体是否写出摘要">
                    身份摘要 {hasSummary ? "✓" : "—"}
                  </span>
                  {hasSheet ? (
                    <span className="text-ink-500">
                      三视图来源：
                      {sheetSource === "api" ? "API" : sheetSource === "placeholder" ? "占位" : sheetSource || "—"}
                    </span>
                  ) : null}
                </div>
                <div className="grid grid-cols-2 gap-2 mt-2">
                  {c.reference_image_path ? (
                    <a
                      href={fileUrl(c.reference_image_path)}
                      target="_blank"
                      rel="noreferrer"
                      className="block aspect-video rounded border border-ink-800/80 overflow-hidden bg-ink-950"
                    >
                      <img
                        src={fileUrl(c.reference_image_path)}
                        alt="参照帧"
                        className="w-full h-full object-cover"
                      />
                    </a>
                  ) : (
                    <div className="aspect-video rounded border border-dashed border-ink-800 flex items-center justify-center text-[11px] text-ink-600">
                      无参照帧
                    </div>
                  )}
                  {typeof c.three_views?.sheet === "string" && c.three_views.sheet ? (
                    <a
                      href={fileUrl(c.three_views.sheet)}
                      target="_blank"
                      rel="noreferrer"
                      className="block aspect-video rounded border border-ink-800/80 overflow-hidden bg-ink-950"
                    >
                      <img
                        src={fileUrl(c.three_views.sheet)}
                        alt="三视图"
                        className="w-full h-full object-cover"
                      />
                    </a>
                  ) : (
                    <div className="aspect-video rounded border border-dashed border-ink-800 flex items-center justify-center text-[11px] text-ink-600">
                      无三视图
                    </div>
                  )}
                </div>
                {typeof c.agent_profile?.identity_summary === "string" &&
                c.agent_profile.identity_summary ? (
                  <p className="text-xs text-ink-300 mt-2 leading-snug line-clamp-4">
                    {c.agent_profile.identity_summary}
                  </p>
                ) : null}
                {typeof c.agent_profile?._appearance_evidence_raw === "string" &&
                c.agent_profile._appearance_evidence_raw ? (
                  <details className="mt-2 rounded border border-ink-800/80 p-2 text-[10px] text-ink-500 leading-relaxed">
                    <summary className="cursor-pointer text-ink-400 font-medium">镜头外观证据（查看从镜头提取了哪些内容）</summary>
                    <pre className="mt-1.5 whitespace-pre-wrap break-all font-mono text-ink-500">{c.agent_profile._appearance_evidence_raw}</pre>
                  </details>
                ) : null}
                <div className="text-xs text-ink-400 mt-2 space-y-0.5">
                  <div>出现约 {c.mention_count} 次（镜头条目中）</div>
                  <div>
                    时间轴约 {c.first_seen_s.toFixed(1)}s — {c.last_seen_s.toFixed(1)}s
                  </div>
                  <div className="text-ink-500">证据镜头数：{c.source_shot_ids.length}</div>
                </div>
              </div>
            );
            })}
          </div>
          <PaginationFooter
            page={castPageData.pageSafe}
            totalPages={castPageData.totalPages}
            total={cast.length}
            pageSize={castPageSize}
            pageSizes={CAST_PAGE_SIZES}
            onPageChange={setCastPage}
            onPageSizeChange={(n) => {
              setCastPageSize(n);
              setCastPage(1);
            }}
            itemLabel="个角色"
          />
          </>
        )}
      </section>
      )}

      {libraryTab === "timelines" && (
      <section>
        <h2 className="text-lg font-medium mb-2">时间线版本（{timelines.length}）</h2>
        {timelines.length === 0 ? (
          <p className="text-sm text-ink-500 rounded-xl border border-ink-900/60 bg-ink-900/30 px-4 py-6">
            暂无时间线记录。
          </p>
        ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {timelines.map((t) => (
            <div key={t.id} className="rounded-xl border border-ink-900/60 bg-ink-900/40 p-3 text-sm">
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="font-medium">{t.label}</span>
                  <span className="text-xs text-ink-400">{t.status}</span>
                </div>
                {t.parent_id ? (
                  <button
                    type="button"
                    title="删除该分支时间线（不可恢复）"
                    disabled={deleteTimelineBusyId === t.id}
                    onClick={() => void handleDeleteTimelineBranch(t)}
                    className="shrink-0 px-2 py-0.5 rounded border border-red-500/35 text-red-300 text-[11px] hover:bg-red-500/15 disabled:opacity-40"
                  >
                    {deleteTimelineBusyId === t.id ? "删除中…" : "删除分支"}
                  </button>
                ) : (
                  <span className="text-[10px] text-ink-500 shrink-0">主线不可删</span>
                )}
              </div>
              <div className="text-xs text-ink-400 mt-1">id: {t.id.slice(0, 8)}</div>
              {t.parent_id ? (
                <div className="text-xs text-ink-400">由 {t.parent_id.slice(0, 8)} fork</div>
              ) : null}
              {t.branch_reason ? (
                <div className="text-xs text-ink-200 mt-1">原因：{t.branch_reason}</div>
              ) : null}
              {t.apply_time !== null && t.apply_time !== undefined ? (
                <div className="text-xs text-ink-200">分叉点：{Number(t.apply_time).toFixed(1)}s</div>
              ) : null}
              {t.parent_id ? (
                <button
                  type="button"
                  onClick={() => setOrchestrateTimelineId(t.id)}
                  className="mt-2 w-full px-2 py-1.5 rounded-lg border border-sky-500/40 text-sky-200 text-xs hover:bg-sky-500/10"
                >
                  编辑编排（切入时刻 / 片段顺序）
                </button>
              ) : null}
            </div>
          ))}
        </div>
        )}
      </section>
      )}

      {libraryTab === "shots" && (
      <section>
        <div className="flex flex-wrap items-center justify-between gap-3 mb-2">
          <h2 className="text-lg font-medium">镜头与生成片段（{shots.length}）</h2>
          <div className="flex flex-wrap gap-2 justify-end">
            <button
              type="button"
              disabled={!videoId || asrBulkBusy || shots.length === 0}
              onClick={() => void handleTranscribeAllAsr()}
              title="仅从镜头 mp4 抽音轨并转写对白，合并到 dialogue（不重新跑画面理解）"
              className="shrink-0 px-3 py-1.5 rounded-lg border border-cyan-500/40 bg-cyan-500/10 text-cyan-200 text-xs hover:bg-cyan-500/20 disabled:opacity-40"
            >
              {asrBulkBusy ? "识别中…" : "批量音轨识别对白"}
            </button>
            <button
              type="button"
              disabled={!videoId || regenerateBusy}
              onClick={handleRegenerateAllShots}
              title="不切分视频，对该成片下全部镜头重新调用分析模型（受上方粒度等筛选影响）"
              className="shrink-0 px-3 py-1.5 rounded-lg border border-amber-500/40 bg-amber-500/10 text-amber-200 text-xs hover:bg-amber-500/20 disabled:opacity-40"
            >
              {regenerateBusy ? "生成中…" : "重新生成镜头分析"}
            </button>
          </div>
        </div>
        {shots.length === 0 ? (
          <div className="text-sm text-ink-500 rounded-xl border border-ink-900/60 bg-ink-900/30 px-4 py-6 space-y-2">
            <p>当前筛选下暂无镜头。可调整粒度 / 来源 / 关键词，或确认该片已上传并完成切分。</p>
            {granularity ? (
              <p className="text-ink-400">
                提示：若上传页未勾选「场景」粒度，则不会有{" "}
                <code className="text-ink-300">scene</code>{" "}
                镜头；请把「切分粒度」改为「全部」查看其它粒度（如 5s、story）。
              </p>
            ) : null}
          </div>
        ) : (
          <>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {shotsPageData.slice.map((s) => (
            <button
              key={s.id}
              onClick={() => setActiveShot(s)}
              className="text-left rounded-xl border border-ink-900/60 bg-ink-900/40 hover:border-amberx-400/40 overflow-hidden"
            >
              <div className="aspect-video bg-black">
                {s.thumbnail_path ? (
                  <img src={fileUrl(s.thumbnail_path)} alt="thumb" className="w-full h-full object-cover" />
                ) : null}
              </div>
              <div className="p-3 text-sm">
                <div className="flex items-center gap-2 text-xs">
                  <Tag>{s.granularity}</Tag>
                  <Tag>{s.source}</Tag>
                  <span className="ml-auto text-ink-400">{formatDuration(s.duration)}</span>
                </div>
                <div
                  className={`mt-1 line-clamp-2 ${s.summary?.trim() ? "text-ink-100" : "text-ink-500"}`}
                >
                  {s.summary?.trim() || "字幕失败"}
                </div>
                <div className="mt-1 text-xs text-ink-400">
                  {(s.characters || []).join(" / ")} {s.location ? `· ${s.location}` : ""}
                </div>
              </div>
            </button>
          ))}
        </div>
        <PaginationFooter
          page={shotsPageData.pageSafe}
          totalPages={shotsPageData.totalPages}
          total={shots.length}
          pageSize={shotsPageSize}
          pageSizes={SHOTS_PAGE_SIZES}
          onPageChange={setShotsPage}
          onPageSizeChange={(n) => {
            setShotsPageSize(n);
            setShotsPage(1);
          }}
          itemLabel="条镜头"
        />
          </>
        )}
      </section>
      )}

      {activeShot ? (
        <ShotDetail
          shot={activeShot}
          onClose={() => setActiveShot(null)}
          onShotUpdated={(s) => {
            setActiveShot(s);
            reloadAssetData();
          }}
          onDeleted={() => {
            setActiveShot(null);
            reloadAssetData();
          }}
        />
      ) : null}

      {editCharId ? (() => {
        const char = cast.find((c) => c.id === editCharId);
        return char ? (
          <CharacterEditDialog
            character={char}
            videoId={videoId}
            onClose={() => setEditCharId(null)}
            onUpdated={(ch) =>
              setCast((prev) => prev.map((x) => (x.id === ch.id ? { ...x, ...ch } : x)))
            }
            onSheetUpload={async (characterId, file) => {
              setSheetUploadBusyId(characterId);
              try {
                const updated = await api.uploadCharacterTurnaroundSheet(videoId, characterId, file);
                setCast((prev) => prev.map((c) => (c.id === characterId ? { ...c, ...updated } as typeof c : c)));
              } catch (e) {
                alert(e instanceof Error ? e.message : String(e));
              } finally {
                setSheetUploadBusyId(null);
              }
            }}
            onDelete={handleDeleteCharacter}
            sheetUploadBusyId={sheetUploadBusyId}
          />
        ) : null;
      })(      ) : null}

      {orchestrateTimelineId && videoId ? (
        <BranchTimelineEditDialog
          open
          timelineId={orchestrateTimelineId}
          videoDuration={currentVideo?.duration ?? 0}
          initialApply={(() => {
            const ot = timelines.find((x) => x.id === orchestrateTimelineId);
            return typeof ot?.apply_time === "number" ? ot.apply_time : 0;
          })()}
          onClose={() => setOrchestrateTimelineId(null)}
          onSaved={async () => {
            const tls = await api.listAssetTimelines(videoId);
            setTimelines(tls);
          }}
        />
      ) : null}
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

function Tag({ children }: { children: React.ReactNode }) {
  return (
    <span className="px-2 py-0.5 rounded bg-ink-950 border border-ink-900/60 text-ink-200 text-xs">
      {children}
    </span>
  );
}

function ShotDetail({
  shot,
  onClose,
  onShotUpdated,
  onDeleted,
}: {
  shot: Shot;
  onClose: () => void;
  onShotUpdated: (s: Shot) => void;
  onDeleted?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [asrBusy, setAsrBusy] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);

  const handleTranscribeAsr = async () => {
    if (asrBusy) return;
    setAsrBusy(true);
    try {
      const updated = await api.transcribeShotAsr(shot.video_id, shot.id);
      onShotUpdated(updated);
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      setAsrBusy(false);
    }
  };

  const handleRegenerate = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const updated = await api.reanalyzeShot(shot.id);
      onShotUpdated(updated);
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleDeleteShot = async () => {
    if (deleteBusy) return;
    if (
      !confirm(
        "确定删除该镜头？将删除切片与缩略图等文件；时间线上引用该镜头的片段会解除关联；角色库「证据镜头」列表中也会移除该 id。此操作不可恢复。"
      )
    ) {
      return;
    }
    setDeleteBusy(true);
    try {
      await api.deleteShot(shot.video_id, shot.id);
      onDeleted?.();
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      setDeleteBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-6" onClick={onClose}>
      <div
        className="bg-ink-950 border border-ink-900/60 rounded-xl max-w-3xl w-full max-h-[90vh] flex flex-col overflow-hidden shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="aspect-video bg-black shrink-0">
          <video src={fileUrl(shot.file_path)} controls className="w-full h-full" />
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto scroll-thin p-4 text-sm space-y-2">
          <div className="flex items-center gap-2 flex-wrap">
            <Tag>{shot.granularity}</Tag>
            <Tag>{shot.source}</Tag>
            <span className="ml-auto text-ink-400">
              {shot.start_time.toFixed(1)}s - {shot.end_time.toFixed(1)}s
            </span>
          </div>
          <Detail label="摘要" value={shot.summary?.trim() || "字幕失败"} />
          <Detail label="人物" value={(shot.characters || []).join(" / ") || "—"} />
          <Detail label="地点" value={shot.location || "—"} />
          <Detail label="动作" value={(shot.actions || []).join(" / ") || "—"} />
          <Detail label="对白" value={shot.dialogue || "—"} />
          <Detail label="情绪" value={shot.emotion || "—"} />
          <Detail label="道具" value={(shot.objects || []).join(" / ") || "—"} />
          <Detail label="标签" value={(shot.tags || []).join(" / ") || "—"} />
          <Detail label="连续性锚点" value={JSON.stringify(shot.continuity_anchors || {})} />
        </div>
        <div className="shrink-0 p-3 border-t border-ink-900/60 flex flex-wrap items-center justify-end gap-2 bg-ink-950">
          <button
            type="button"
            disabled={deleteBusy || asrBusy || busy}
            onClick={() => void handleDeleteShot()}
            title="删除该镜头记录及切片文件（不可恢复）"
            className="px-3 py-1.5 rounded border border-rose-500/40 bg-rose-500/10 text-rose-200 text-sm hover:bg-rose-500/20 disabled:opacity-40 mr-auto"
          >
            {deleteBusy ? "删除中…" : "删除镜头"}
          </button>
          <button
            type="button"
            disabled={asrBusy}
            onClick={() => void handleTranscribeAsr()}
            title="从该镜头音轨转写对白并合并到「对白」字段（需配置服务端 ASR）"
            className="px-3 py-1.5 rounded border border-cyan-500/40 bg-cyan-500/10 text-cyan-200 text-sm hover:bg-cyan-500/20 disabled:opacity-40"
          >
            {asrBusy ? "识别中…" : "音轨识别对白"}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={handleRegenerate}
            title="重新调用分析模型，刷新摘要等结构化字段"
            className="px-3 py-1.5 rounded border border-amber-500/40 bg-amber-500/10 text-amber-200 text-sm hover:bg-amber-500/20 disabled:opacity-40"
          >
            {busy ? "生成中…" : "重新生成"}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 rounded bg-ink-900 text-ink-100 text-sm"
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── 角色编辑弹窗 ───────────────────────────────────────────────────────

interface CharacterEditDialogProps {
  character: VideoCharacter;
  videoId: string;
  onClose: () => void;
  onUpdated?: (ch: VideoCharacter) => void;
  /** 仅替换三视图 PNG/JPG，不改变镜头参照帧 */
  onSheetUpload: (characterId: string, file: File) => void;
  onDelete: (characterId: string) => void;
  sheetUploadBusyId: string | null;
}

function CharacterEditDialog({
  character: c,
  videoId,
  onClose,
  onUpdated,
  onSheetUpload,
  onDelete,
  sheetUploadBusyId,
}: CharacterEditDialogProps) {
  const prof = c.agent_profile as Record<string, unknown> | undefined;
  const sheetPath =
    typeof c.three_views?.sheet === "string" ? (c.three_views.sheet as string) : "";
  const [notesDraft, setNotesDraft] = useState(c.user_notes ?? "");
  const [notesBusy, setNotesBusy] = useState(false);
  useEffect(() => {
    setNotesDraft(c.user_notes ?? "");
  }, [c.id, c.user_notes]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div
        className="w-full max-w-3xl max-h-[85dvh] flex flex-col rounded-2xl border border-ink-700 bg-ink-950 shadow-2xl shadow-black/50 overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between gap-3 px-5 py-4 border-b border-ink-800 shrink-0">
          <div className="flex items-center gap-3">
            <span className="text-base font-semibold text-ink-100">编辑角色</span>
            <span className="text-sm text-ink-400">{c.display_name}</span>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-2 rounded-lg text-ink-500 hover:text-ink-200 hover:bg-ink-800"
            aria-label="关闭"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Body */}
        <div className="overflow-y-auto p-5 flex flex-col lg:flex-row gap-6">
          {/* 左侧：角色信息 */}
          <div className="lg:w-80 shrink-0 space-y-3">
            <h3 className="text-xs font-medium text-ink-400 uppercase tracking-wider">我的备注</h3>
            <div className="rounded-xl border border-ink-800 bg-ink-900/60 p-3 space-y-2">
              <textarea
                value={notesDraft}
                onChange={(e) => setNotesDraft(e.target.value)}
                rows={4}
                placeholder="记录与该剧、该角色相关的备忘（仅本地库，不影响分析）"
                className="w-full bg-ink-950 border border-ink-800 rounded-lg px-3 py-2 text-sm text-ink-100 placeholder:text-ink-600"
              />
              <button
                type="button"
                disabled={notesBusy}
                onClick={async () => {
                  setNotesBusy(true);
                  try {
                    const updated = await api.patchVideoCharacter(videoId, c.id, {
                      user_notes: notesDraft.trim() || null,
                    });
                    onUpdated?.(updated);
                  } catch (e) {
                    alert(e instanceof Error ? e.message : String(e));
                  } finally {
                    setNotesBusy(false);
                  }
                }}
                className="w-full px-3 py-1.5 rounded-lg border border-emerald-500/40 text-emerald-200 text-xs hover:bg-emerald-500/10 disabled:opacity-40"
              >
                {notesBusy ? "保存中…" : "保存备注"}
              </button>
            </div>
            <h3 className="text-xs font-medium text-ink-400 uppercase tracking-wider">角色信息</h3>
            <div className="rounded-xl border border-ink-800 bg-ink-900/60 p-4 space-y-2 text-sm">
              {prof?.identity_summary ? (
                <Detail label="身份摘要" value={String(prof.identity_summary)} />
              ) : (
                <p className="text-xs text-ink-500 italic">暂无身份摘要（可点击「富化」生成）</p>
              )}
              {typeof prof?._evidence_alignment_note === "string" &&
              (prof._evidence_alignment_note as string).trim() ? (
                <p className="text-[11px] text-amber-200/90 rounded border border-amber-500/30 bg-amber-500/10 p-2 leading-snug">
                  {String(prof._evidence_alignment_note)}
                  {typeof prof?._evidence_shots_total_in_library === "number" &&
                  typeof prof?._evidence_shots_used_for_enrichment === "number" ? (
                    <span className="block mt-1 text-ink-500">
                      角色库关联镜头 {Number(prof._evidence_shots_total_in_library)} 条；本次富化实际采用{" "}
                      {Number(prof._evidence_shots_used_for_enrichment)} 条（已按画风主线对齐）。
                    </span>
                  ) : null}
                </p>
              ) : null}
              {typeof prof?.reference_frame_alignment === "string" &&
              (prof.reference_frame_alignment as string).trim() ? (
                <details className="mt-2 rounded border border-ink-800/80 p-2 text-[11px] text-ink-400 leading-relaxed">
                  <summary className="cursor-pointer text-ink-300 font-medium">
                    身份与参照图对齐依据（富化后以参照镜头为准）
                  </summary>
                  <pre className="mt-1.5 whitespace-pre-wrap break-all font-mono text-ink-500">
                    {String(prof.reference_frame_alignment)}
                  </pre>
                </details>
              ) : null}
              {prof?.evidence_summary ? (
                <Detail label="证据说明" value={String(prof.evidence_summary)} />
              ) : null}
              {prof?.who_is ? <Detail label="who_is" value={String(prof.who_is)} /> : null}
              {prof?.role_in_story ? <Detail label="角色定位" value={String(prof.role_in_story)} /> : null}
              {prof?.visual_style_for_gen ? (
                <Detail label="视觉风格" value={String(prof.visual_style_for_gen)} />
              ) : null}
              {c.description ? <Detail label="描述" value={c.description} /> : null}
              {c.aliases.length > 0 ? <Detail label="别名" value={c.aliases.join("、")} /> : null}
              <Detail label="出现次数" value={`${c.mention_count} 次`} />
              <Detail
                label="时间轴"
                value={`${c.first_seen_s.toFixed(1)}s — ${c.last_seen_s.toFixed(1)}s`}
              />
              <Detail label="来源镜头" value={`${c.source_shot_ids.length} 个`} />
            </div>
          </div>

          {/* 右侧：镜头参照帧（只读） + 三视图（可替换） */}
          <div className="flex-1 space-y-5 min-w-0">
            <div>
              <h3 className="text-xs font-medium text-ink-400 uppercase tracking-wider mb-2">
                镜头参照帧
              </h3>
              <p className="text-[11px] text-ink-500 mb-2">
                来自同一镜头（与身份摘要对齐）；仅展示。更新请先重新「富化」或调整镜头分析。
              </p>
              {c.reference_image_path ? (
                <div className="rounded-xl border border-ink-800 overflow-hidden">
                  <img
                    src={fileUrl(c.reference_image_path)}
                    alt="镜头参照帧"
                    className="w-full max-h-48 object-contain bg-ink-950"
                  />
                </div>
              ) : (
                <div className="rounded-xl border border-dashed border-ink-800 flex items-center justify-center h-32 text-ink-600 text-xs">
                  暂无参照帧
                </div>
              )}
            </div>

            <div>
              <div className="flex items-center justify-between gap-2 mb-2">
                <h3 className="text-xs font-medium text-ink-400 uppercase tracking-wider">三视图</h3>
                <label
                  className={`px-3 py-1 rounded-lg border border-emerald-500/40 text-emerald-200 text-xs cursor-pointer hover:bg-emerald-500/15 shrink-0 ${sheetUploadBusyId === c.id ? "opacity-50 pointer-events-none" : ""}`}
                >
                  {sheetUploadBusyId === c.id ? "上传中…" : "替换三视图"}
                  <input
                    id={`sheet-upload-${c.id}`}
                    type="file"
                    accept="image/jpeg,image/png,image/webp"
                    className="hidden"
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) onSheetUpload(c.id, file);
                      e.target.value = "";
                    }}
                  />
                </label>
              </div>
              <p className="text-[11px] text-ink-500 mb-2">仅此处可上传自定义三视图，覆盖生图结果。</p>
              {sheetPath ? (
                <div className="rounded-xl border border-ink-800 overflow-hidden">
                  <img
                    src={fileUrl(sheetPath)}
                    alt="三视图"
                    className="w-full max-h-64 object-contain bg-ink-950"
                  />
                </div>
              ) : (
                <div className="rounded-xl border border-dashed border-ink-800 flex items-center justify-center h-40 text-ink-600 text-xs">
                  暂无三视图（可先「富化」生成，或在此上传）
                </div>
              )}
            </div>

            {/* 底部操作 */}
            <div className="pt-3 border-t border-ink-800 flex justify-end">
              <button
                type="button"
                onClick={() => {
                  onDelete(c.id);
                  onClose();
                }}
                className="px-4 py-1.5 rounded-lg border border-red-500/40 text-red-300 text-xs hover:bg-red-500/15"
              >
                删除此角色
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span className="text-ink-400 text-xs">{label}：</span>
      <span className="text-ink-100">{value}</span>
    </div>
  );
}
