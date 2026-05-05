import { useEffect, useMemo, useState } from "react";

import { api } from "../lib/api";
import type { ModelConfig, SafetyPolicy } from "../lib/types";

const PROFILES = ["fast", "quality", "fallback"] as const;

const KIND_LABELS: Record<string, string> = {
  llm: "语言模型",
  vlm: "多模态理解",
  image: "图像生成",
  video: "视频生成",
  tts: "配音合成",
  asr: "音轨识别 ASR",
};

const MODEL_SECTIONS = [
  {
    kind: "llm",
    title: "语言模型",
    desc: "对话、意图分类、剧情编排、问答与安全审核；放映厅对话 profile 与此处 fast/quality/fallback 对应。",
  },
  {
    kind: "vlm",
    title: "多模态理解",
    desc: "上传切分时镜头字段 analyze_shot（OpenAI 兼容 chat，视觉模型 ID）。",
  },
  {
    kind: "image",
    title: "图像生成",
    desc: "预留：提示词工程中的生图管线；密钥与模型 ID 可先在此保存。",
  },
  {
    kind: "video",
    title: "视频生成",
    desc: "干预流程中生成镜头片段；当前仍以 FFmpeg 占位为主，配置供后续接入 Runway/Luma 等。",
  },
  {
    kind: "tts",
    title: "配音合成",
    desc: "对白与旁白 TTS；与 OpenAI Audio / ElevenLabs 等兼容配置。",
  },
  {
    kind: "asr",
    title: "音轨识别 ASR",
    desc: "镜头对白转写（音轨 ASR）；与 OpenAI / MiniMax 兼容 transcriptions。",
  },
] as const;

export default function ConfigPage() {
  const [tab, setTab] = useState<"models" | "safety">("models");

  return (
    <div className="max-w-6xl mx-auto px-4 sm:px-6 py-8 space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center gap-3">
        <div>
          <p className="text-xs uppercase tracking-wider text-sky-500/90 mb-1">叙境 · 后台</p>
          <h1 className="text-2xl font-semibold text-zinc-50">模型与服务密钥</h1>
          <p className="text-sm text-zinc-500 mt-1">
            数据库中的条目可编辑；服务端 .env 里已配置的链路会以「来自环境变量」只读卡片一并列出，修改请编辑 .env 后重启。
          </p>
        </div>
        <div className="sm:ml-auto flex items-center gap-1 text-sm">
          {(
            [
              ["models", "模型"],
              ["safety", "安全策略"],
            ] as const
          ).map(([v, l]) => (
            <button
              key={v}
              type="button"
              onClick={() => setTab(v)}
              className={`px-3 py-1.5 rounded-lg ${
                tab === v ? "bg-sky-600 text-white" : "bg-zinc-800 text-zinc-300 hover:bg-zinc-700"
              }`}
            >
              {l}
            </button>
          ))}
        </div>
      </div>

      {tab === "models" ? <ModelsPanel /> : <SafetyPanel />}
    </div>
  );
}

function ModelsPanel() {
  const [list, setList] = useState<ModelConfig[]>([]);
  const [sectionKind, setSectionKind] = useState<string>("llm");
  const [modalOpen, setModalOpen] = useState(false);
  const [modalMode, setModalMode] = useState<"create" | "edit">("create");
  const [editing, setEditing] = useState<ModelConfig | null>(null);

  const reload = () => api.listModels().then(setList);
  useEffect(() => {
    reload();
  }, []);

  const sectionList = useMemo(() => {
    const rows = list.filter((m) => m.kind === sectionKind);
    return [...rows].sort((a, b) => {
      const ae = a.source === "environment" ? 1 : 0;
      const be = b.source === "environment" ? 1 : 0;
      if (ae !== be) return ae - be;
      return (b.priority ?? 0) - (a.priority ?? 0);
    });
  }, [list, sectionKind]);

  const activeSection = MODEL_SECTIONS.find((s) => s.kind === sectionKind) ?? MODEL_SECTIONS[0];

  const openCreate = () => {
    setModalMode("create");
    setEditing(null);
    setModalOpen(true);
  };

  const openEdit = (m: ModelConfig) => {
    setModalMode("edit");
    setEditing(m);
    setModalOpen(true);
  };

  return (
    <div className="space-y-5 text-sm">
      <div className="flex flex-wrap gap-2">
        {MODEL_SECTIONS.map((s) => (
          <button
            key={s.kind}
            type="button"
            onClick={() => setSectionKind(s.kind)}
            className={`px-3 py-2 rounded-xl text-sm border transition-colors ${
              sectionKind === s.kind
                ? "bg-sky-600/25 border-sky-500/50 text-sky-200"
                : "bg-zinc-900/80 border-zinc-700 text-zinc-400 hover:border-zinc-500"
            }`}
          >
            {s.title}
          </button>
        ))}
      </div>

      <section className="rounded-2xl border border-zinc-800/90 bg-zinc-900/30 overflow-hidden">
        <div className="px-4 py-3 border-b border-zinc-800 bg-zinc-950/60 flex flex-wrap items-center gap-3">
          <div className="flex-1 min-w-0">
            <h2 className="text-base font-medium text-zinc-100">{activeSection.title}</h2>
            <p className="text-xs text-zinc-500 mt-1">{activeSection.desc}</p>
          </div>
          <button
            type="button"
            onClick={openCreate}
            className="shrink-0 px-3 py-2 rounded-xl bg-sky-600 hover:bg-sky-500 text-white text-sm font-medium"
          >
            添加配置
          </button>
        </div>
        <div className="p-4 lg:p-5">
          <p className="text-xs text-zinc-500 mb-3">本类已配置 {sectionList.length} 条</p>
          <div className="space-y-3">
            {sectionList.length === 0 ? (
              <p className="text-sm text-zinc-500 py-6 text-center rounded-xl border border-dashed border-zinc-700">
                该分类下暂无条目，点击「添加配置」。
              </p>
            ) : (
              sectionList.map((m) => (
                <ModelConfigCard key={m.id} m={m} onReload={reload} onEdit={() => openEdit(m)} />
              ))
            )}
          </div>
        </div>
      </section>

      {modalOpen ? (
        <ModelConfigModal
          mode={modalMode}
          sectionKind={sectionKind}
          initial={editing}
          onClose={() => setModalOpen(false)}
          onSaved={() => {
            setModalOpen(false);
            reload();
          }}
        />
      ) : null}
    </div>
  );
}

function ModelConfigCard({
  m,
  onReload,
  onEdit,
}: {
  m: ModelConfig;
  onReload: () => void;
  onEdit: () => void;
}) {
  const ro = m.read_only === true || m.source === "environment" || m.id.startsWith("env:");
  return (
    <div
      className={`rounded-2xl border p-4 space-y-2 ${
        ro ? "border-cyan-500/25 bg-zinc-950/60" : "border-zinc-800 bg-zinc-950/80"
      }`}
    >
      <div className="flex flex-wrap items-start gap-2">
        <span className="px-2 py-0.5 rounded-md bg-violet-500/15 text-violet-300 text-xs">
          {KIND_LABELS[m.kind] ?? m.kind}
        </span>
        <span className="px-2 py-0.5 rounded-md bg-emerald-500/15 text-emerald-300 text-xs">{m.profile}</span>
        {ro ? (
          <span className="px-2 py-0.5 rounded-md bg-cyan-500/20 text-cyan-200 text-xs">来自 .env</span>
        ) : null}
        {m.is_default ? (
          <span className="px-2 py-0.5 rounded-md bg-amber-500/15 text-amber-300 text-xs">默认</span>
        ) : null}
        {!ro ? (
          <span className="px-2 py-0.5 rounded-md bg-zinc-700/40 text-zinc-400 text-xs">
            优先级 {m.priority ?? 0}
          </span>
        ) : null}
        <span className="ml-auto flex items-center gap-2">
          {ro ? (
            <span className="text-[11px] text-zinc-500">只读 · 改 .env 后重启</span>
          ) : (
            <>
              <button
                type="button"
                onClick={onEdit}
                className="text-xs px-2 py-1 rounded-lg bg-sky-600/25 text-sky-300 border border-sky-500/40 hover:bg-sky-600/35"
              >
                编辑
              </button>
              <button
                type="button"
                onClick={async () => {
                  await api.updateModel(m.id, {
                    kind: m.kind,
                    profile: m.profile,
                    name: m.name,
                    provider: m.provider,
                    model: m.model,
                    base_url: m.base_url ?? undefined,
                    api_key_alias: m.api_key_alias ?? undefined,
                    params: m.params || {},
                    is_default: m.is_default,
                    enabled: !m.enabled,
                    priority: m.priority ?? 0,
                  });
                  onReload();
                }}
                className={`text-xs px-2 py-1 rounded-lg ${
                  m.enabled ? "bg-emerald-500/20 text-emerald-300" : "bg-zinc-800 text-zinc-500"
                }`}
              >
                {m.enabled ? "启用" : "停用"}
              </button>
              <button
                type="button"
                onClick={async () => {
                  await api.deleteModel(m.id);
                  onReload();
                }}
                className="text-xs text-rose-400 hover:text-rose-300"
              >
                删除
              </button>
            </>
          )}
        </span>
      </div>
      <div className="font-medium text-zinc-100">{m.name}</div>
      <div className="text-xs text-zinc-500">
        {m.provider} · {m.model}
        {m.base_url ? ` · ${m.base_url}` : ""}
      </div>
    </div>
  );
}

function ModelConfigModal({
  mode,
  sectionKind,
  initial,
  onClose,
  onSaved,
}: {
  mode: "create" | "edit";
  sectionKind: string;
  initial: ModelConfig | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [profile, setProfile] = useState(initial?.profile || "fast");
  const [name, setName] = useState(initial?.name || "");
  const [provider, setProvider] = useState(initial?.provider || "openai");
  const [model, setModel] = useState(initial?.model || "");
  const [baseUrl, setBaseUrl] = useState(initial?.base_url || "");
  const [apiKeyAlias, setApiKeyAlias] = useState(initial?.api_key_alias || "");
  const [apiKey, setApiKey] = useState("");
  const [isDefault, setIsDefault] = useState(!!initial?.is_default);
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [priority, setPriority] = useState(initial?.priority ?? 0);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (initial) {
      setProfile(initial.profile);
      setName(initial.name);
      setProvider(initial.provider);
      setModel(initial.model);
      setBaseUrl(initial.base_url || "");
      setApiKeyAlias(initial.api_key_alias || "");
      setIsDefault(!!initial.is_default);
      setEnabled(initial.enabled);
      setPriority(initial.priority ?? 0);
    } else {
      setProfile("fast");
      setName("");
      setProvider("openai");
      setModel("");
      setBaseUrl("");
      setApiKeyAlias("");
      setApiKey("");
      setIsDefault(false);
      setEnabled(true);
      setPriority(0);
    }
    setApiKey("");
  }, [initial, mode, sectionKind]);

  const submit = async () => {
    if (!name.trim() || !model.trim()) return;
    if (mode === "create" && !apiKey.trim()) {
      alert("请先填写 API Key；未保存密钥的配置不会出现在列表中。");
      return;
    }
    setBusy(true);
    try {
      const params: Record<string, unknown> = { ...(initial?.params || {}) };
      delete params.api_key;
      if (apiKey.trim()) params.api_key = apiKey.trim();
      const pri = Number.parseInt(String(priority), 10);
      const payload = {
        kind: sectionKind,
        profile,
        name: name.trim(),
        provider: provider.trim(),
        model: model.trim(),
        base_url: baseUrl.trim() || undefined,
        api_key_alias: apiKeyAlias.trim() || undefined,
        params,
        is_default: isDefault,
        enabled,
        priority: Number.isFinite(pri) ? pri : 0,
      };
      if (mode === "create") {
        await api.saveModel(payload);
      } else if (initial) {
        await api.updateModel(initial.id, payload);
      }
      onSaved();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-2xl border border-zinc-700 bg-zinc-950 shadow-xl p-5 space-y-3"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-2">
          <h3 className="font-medium text-zinc-100">{mode === "create" ? "添加模型配置" : "编辑模型配置"}</h3>
          <button type="button" onClick={onClose} className="text-zinc-500 hover:text-zinc-300 text-sm">
            关闭
          </button>
        </div>
        <p className="text-xs text-zinc-500">
          分类：{KIND_LABELS[sectionKind] ?? sectionKind}。密钥仅存储于本机数据库，不会在列表中回显内容。
        </p>
        <Field label="模式">
          <select
            value={profile}
            onChange={(e) => setProfile(e.target.value)}
            className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-2 py-2 text-zinc-100"
          >
            {PROFILES.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </Field>
        <Field label="优先级（数字越大越优先调用）">
          <input
            type="number"
            step={1}
            value={priority}
            onChange={(e) => setPriority(Number.parseInt(e.target.value, 10) || 0)}
            className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-2 py-2 text-zinc-100"
          />
        </Field>
        <Field label="显示名称">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-2 py-2 text-zinc-100"
          />
        </Field>
        <Field label="提供商">
          <input
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-2 py-2 text-zinc-100"
          />
        </Field>
        <Field label="模型 ID">
          <input
            value={model}
            onChange={(e) => setModel(e.target.value)}
            className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-2 py-2 text-zinc-100"
          />
        </Field>
        <Field label="Base URL（可选）">
          <input
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-2 py-2 text-zinc-100"
            placeholder="https://api.openai.com/v1"
          />
        </Field>
        <Field label="密钥别名（可选）">
          <input
            value={apiKeyAlias}
            onChange={(e) => setApiKeyAlias(e.target.value)}
            className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-2 py-2 text-zinc-100"
          />
        </Field>
        <Field label={mode === "edit" && initial?.has_api_key ? "API Key（留空保留原密钥）" : "API Key（可选）"}>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-2 py-2 text-zinc-100"
            placeholder="sk-…"
            autoComplete="off"
          />
        </Field>
        <label className="flex items-center gap-2 text-sm text-zinc-300">
          <input type="checkbox" checked={isDefault} onChange={(e) => setIsDefault(e.target.checked)} />
          作为该分类该模式下的默认
        </label>
        <label className="flex items-center gap-2 text-sm text-zinc-300">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          启用
        </label>
        <button
          type="button"
          disabled={busy}
          onClick={() => void submit()}
          className="w-full px-3 py-2 rounded-xl bg-sky-600 hover:bg-sky-500 disabled:opacity-50 text-white text-sm font-medium"
        >
          {busy ? "保存中…" : "保存"}
        </button>
      </div>
    </div>
  );
}

function SafetyPanel() {
  const [list, setList] = useState<SafetyPolicy[]>([]);
  const [draft, setDraft] = useState<Partial<SafetyPolicy>>({
    label: "",
    category: "forbid",
    keywords: [],
    description: "",
    enabled: true,
  });
  const [keywordsInput, setKeywordsInput] = useState("");

  const reload = () => api.listSafety().then(setList);
  useEffect(() => {
    reload();
  }, []);

  const submit = async () => {
    if (!draft.label) return;
    await api.saveSafety({
      label: draft.label!,
      category: draft.category as any,
      keywords: keywordsInput.split(/[,，\s]+/).filter(Boolean),
      description: draft.description || "",
      rewrite_template: draft.rewrite_template || undefined,
      enabled: draft.enabled ?? true,
    });
    setDraft({ label: "", category: "forbid", keywords: [], description: "", enabled: true });
    setKeywordsInput("");
    reload();
  };

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-sm">
      <div className="md:col-span-2 rounded-xl border border-ink-900/60 bg-ink-900/40 p-3 space-y-2">
        <h2 className="font-medium mb-1">安全策略（{list.length}）</h2>
        {list.map((p) => (
          <div key={p.id} className="rounded border border-ink-900/60 bg-ink-950 p-2 flex items-start gap-2">
            <span
              className={`px-2 py-0.5 rounded text-xs ${
                p.category === "forbid"
                  ? "bg-rose-500/20 text-rose-300"
                  : p.category === "rewrite"
                  ? "bg-amberx-500/20 text-amberx-300"
                  : "bg-cyan-500/20 text-cyan-300"
              }`}
            >
              {p.category}
            </span>
            <div className="flex-1">
              <div className="font-medium">{p.label}</div>
              <div className="text-xs text-ink-400">{p.description}</div>
              <div className="text-xs text-ink-200 mt-1">关键词：{(p.keywords || []).join(" / ") || "—"}</div>
              {p.rewrite_template ? (
                <div className="text-xs text-ink-200 mt-1">改写模板：{p.rewrite_template}</div>
              ) : null}
            </div>
            <button
              onClick={async () => {
                await api.updateSafety(p.id, { ...p, enabled: !p.enabled });
                reload();
              }}
              className={`text-xs px-2 py-0.5 rounded ${
                p.enabled ? "bg-emerald-500/20 text-emerald-300" : "bg-ink-900 text-ink-400"
              }`}
            >
              {p.enabled ? "启用" : "停用"}
            </button>
            <button
              onClick={async () => {
                await api.deleteSafety(p.id);
                reload();
              }}
              className="text-xs text-rose-300"
            >
              删除
            </button>
          </div>
        ))}
      </div>
      <div className="rounded-xl border border-ink-900/60 bg-ink-900/40 p-3 space-y-2">
        <h2 className="font-medium">新增策略</h2>
        <Field label="标签">
          <input
            value={draft.label || ""}
            onChange={(e) => setDraft({ ...draft, label: e.target.value })}
            className="w-full bg-ink-950 border border-ink-900/60 rounded px-2 py-1.5"
          />
        </Field>
        <Field label="分类">
          <select
            value={draft.category}
            onChange={(e) => setDraft({ ...draft, category: e.target.value as any })}
            className="w-full bg-ink-950 border border-ink-900/60 rounded px-2 py-1.5"
          >
            <option value="forbid">禁止 (forbid)</option>
            <option value="restrict">限制 (restrict)</option>
            <option value="rewrite">改写 (rewrite)</option>
          </select>
        </Field>
        <Field label="关键词（逗号 / 空格分隔）">
          <input
            value={keywordsInput}
            onChange={(e) => setKeywordsInput(e.target.value)}
            className="w-full bg-ink-950 border border-ink-900/60 rounded px-2 py-1.5"
          />
        </Field>
        <Field label="描述">
          <textarea
            value={draft.description || ""}
            onChange={(e) => setDraft({ ...draft, description: e.target.value })}
            rows={2}
            className="w-full bg-ink-950 border border-ink-900/60 rounded px-2 py-1.5"
          />
        </Field>
        <Field label="改写提示词（可选）">
          <textarea
            value={draft.rewrite_template || ""}
            onChange={(e) => setDraft({ ...draft, rewrite_template: e.target.value })}
            rows={2}
            className="w-full bg-ink-950 border border-ink-900/60 rounded px-2 py-1.5"
          />
        </Field>
        <button
          onClick={submit}
          className="px-3 py-1.5 rounded bg-amberx-500 text-ink-950 text-sm font-medium"
        >
          保存
        </button>
      </div>
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
