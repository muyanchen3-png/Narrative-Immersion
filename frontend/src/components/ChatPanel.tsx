import { useEffect, useRef, useState } from "react";

import { api } from "../lib/api";
import type { ChatMessage, ChatResponse } from "../lib/types";

interface Props {
  timelineId: string;
  playTime: number;
  onIntervention: (resp: ChatResponse) => void;
}

const SUGGESTIONS = [
  "这一段里人物的情绪是怎样的？",
  "如果接下来改成更轻松的氛围会怎样？",
  "让主角先做一件善事再继续赶路",
];

const LOCAL_USER_PREFIX = "local-user-";
const LOCAL_ASSISTANT_PREFIX = "local-assistant-";

function stripLocalAssistant(messages: ChatMessage[]): ChatMessage[] {
  return messages.filter((m) => !m.id.startsWith(LOCAL_ASSISTANT_PREFIX));
}

export default function ChatPanel({ timelineId, playTime, onIntervention }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [forceIntent, setForceIntent] = useState<"" | "qa" | "intervention">("");
  const [profile, setProfile] = useState("fast");
  const [busy, setBusy] = useState(false);
  const [clearBusy, setClearBusy] = useState(false);
  const scrollerRef = useRef<HTMLDivElement>(null);
  /** 用户是否在列表底部附近（贴底时才自动跟随新消息） */
  const stickBottomRef = useRef(true);
  const prevMsgLenRef = useRef(0);

  const updateStickFromScroll = () => {
    const el = scrollerRef.current;
    if (!el) return;
    const gap = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickBottomRef.current = gap < 72;
  };

  useEffect(() => {
    let cancelled = false;
    setMessages([]);
    prevMsgLenRef.current = 0;
    stickBottomRef.current = true;
    api.listChatMessages(timelineId).then((m) => {
      if (!cancelled) setMessages(m);
    });
    return () => {
      cancelled = true;
    };
  }, [timelineId]);

  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    el.addEventListener("scroll", updateStickFromScroll, { passive: true });
    return () => el.removeEventListener("scroll", updateStickFromScroll);
  }, [timelineId]);

  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    const prevLen = prevMsgLenRef.current;
    const len = messages.length;
    const appended = len > prevLen;
    prevMsgLenRef.current = len;

    if (!stickBottomRef.current) {
      return;
    }

    requestAnimationFrame(() => {
      el.scrollTo({
        top: el.scrollHeight,
        behavior: appended ? "smooth" : "auto",
      });
    });
  }, [messages]);

  const send = async (content: string) => {
    if (!content.trim() || busy) return;
    stickBottomRef.current = true;
    setInput("");
    const trimmed = content.trim();
    const localUserId = `${LOCAL_USER_PREFIX}${Date.now()}`;
    const localThinkingId = `${LOCAL_ASSISTANT_PREFIX}${Date.now()}`;
    const optimisticUser: ChatMessage = {
      id: localUserId,
      timeline_id: timelineId,
      role: "user",
      intent: "pending",
      play_time: playTime,
      content: trimmed,
      metadata_json: {},
      created_at: new Date().toISOString(),
    };
    const thinkingMsg: ChatMessage = {
      id: localThinkingId,
      timeline_id: timelineId,
      role: "assistant",
      intent: "system",
      play_time: playTime,
      content: "正在识别意图并准备回复…",
      metadata_json: {},
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, optimisticUser, thinkingMsg]);
    setBusy(true);
    try {
      const runChat = async (confirmIntervention: boolean): Promise<void> => {
        const resp = await api.postChat({
          timeline_id: timelineId,
          play_time: playTime,
          content: trimmed,
          force_intent: forceIntent || undefined,
          profile,
          confirm_intervention: confirmIntervention ? true : undefined,
        });
        if (resp.needs_intervention_confirm) {
          setMessages((prev) => stripLocalAssistant(prev));
          const ok = window.confirm(
            "这句话将被识别为「叙事干预」，会改写后续剧情走向。\n\n确定要执行吗？（取消则撤回本条发言）"
          );
          if (!ok) {
            setMessages((prev) => prev.filter((m) => m.id !== localUserId));
            return;
          }
          const execThinkingId = `${LOCAL_ASSISTANT_PREFIX}exec-${Date.now()}`;
          setMessages((prev) => [
            ...prev.filter((m) => m.id === localUserId),
            {
              id: execThinkingId,
              timeline_id: timelineId,
              role: "assistant",
              intent: "system",
              play_time: playTime,
              content: "正在执行叙事干预…",
              metadata_json: {},
              created_at: new Date().toISOString(),
            },
          ]);
          await runChat(true);
          return;
        }
        setMessages((prev) => {
          const base = prev.filter((m) => m.id !== localUserId && !m.id.startsWith(LOCAL_ASSISTANT_PREFIX));
          if (resp.user_message && resp.assistant_message) {
            return [...base, resp.user_message, resp.assistant_message];
          }
          return base;
        });
        if (resp.intent === "intervention") {
          onIntervention(resp);
        }
      };
      await runChat(false);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setMessages((prev) => [
        ...prev.filter((m) => m.id !== localUserId && !m.id.startsWith(LOCAL_ASSISTANT_PREFIX)),
        {
          id: `local-error-${Date.now()}`,
          timeline_id: timelineId,
          role: "assistant",
          intent: "system",
          play_time: playTime,
          content: `请求失败：${msg}`,
          metadata_json: {},
          created_at: new Date().toISOString(),
        },
      ]);
    } finally {
      setBusy(false);
    }
  };

  const clearConversation = async () => {
    if (busy || clearBusy || messages.length === 0) return;
    if (!confirm("确定清除当前时间线下的全部对话？此操作不可撤销。")) return;
    setClearBusy(true);
    try {
      await api.clearChatMessages(timelineId);
      setMessages([]);
      prevMsgLenRef.current = 0;
      stickBottomRef.current = true;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      alert(msg);
    } finally {
      setClearBusy(false);
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col rounded-2xl overflow-hidden border border-zinc-800 bg-zinc-900/50 shadow-xl shadow-black/20">
      <div className="shrink-0 border-b border-zinc-800 px-3 py-2.5 sm:px-4 text-xs space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <select
            className="bg-zinc-950 border border-zinc-700 rounded-lg px-2 py-1.5 text-zinc-200 min-w-0 max-w-full"
            value={forceIntent}
            onChange={(e) => setForceIntent(e.target.value as typeof forceIntent)}
          >
            <option value="">自动：问答 / 叙事</option>
            <option value="qa">仅问答</option>
            <option value="intervention">推动叙事</option>
          </select>
          <select
            className="bg-zinc-950 border border-zinc-700 rounded-lg px-2 py-1.5 text-zinc-200"
            value={profile}
            onChange={(e) => setProfile(e.target.value)}
          >
            <option value="fast">快速</option>
            <option value="quality">高质量</option>
            <option value="fallback">兜底</option>
          </select>
        </div>
        <div className="flex items-center justify-between gap-2 min-h-[2.25rem]">
          <button
            type="button"
            disabled={busy || clearBusy || messages.length === 0}
            onClick={() => void clearConversation()}
            className={`px-2.5 py-1.5 rounded-lg border text-sm shrink-0 transition-colors ${
              messages.length === 0
                ? "border-zinc-600 bg-zinc-900/80 text-zinc-500 cursor-not-allowed"
                : "border-amber-500/40 bg-amber-500/10 text-amber-200/95 hover:bg-amber-500/20 hover:border-amber-500/60"
            } disabled:opacity-100`}
            title={
              messages.length === 0
                ? "当前没有对话记录"
                : "清空本条时间线下的全部对话"
            }
          >
            {clearBusy ? "清除中…" : "清除对话"}
          </button>
          <span className="font-mono text-[11px] text-zinc-500 tabular-nums shrink-0">
            {playTime.toFixed(1)}s
          </span>
        </div>
      </div>
      <div
        ref={scrollerRef}
        tabIndex={0}
        className="flex-1 min-h-0 overflow-y-auto overscroll-y-contain px-4 py-3 space-y-3 touch-pan-y outline-none [scrollbar-gutter:stable] [scrollbar-width:thin] [scrollbar-color:rgba(113,113,122,0.55)_transparent]"
        onWheel={(e) => {
          const el = scrollerRef.current;
          if (!el) return;
          const { scrollTop, scrollHeight, clientHeight } = el;
          const dy = e.deltaY;
          const atTop = scrollTop <= 0;
          const atBottom = scrollTop + clientHeight >= scrollHeight - 2;
          if ((dy < 0 && !atTop) || (dy > 0 && !atBottom)) {
            e.stopPropagation();
          }
        }}
      >
        {messages.length === 0 ? (
          <p className="text-sm text-zinc-500 leading-relaxed">
            观看时可以追问剧情，也可以用一句话改写接下来的走向——叙事会在后场接续生成。
          </p>
        ) : (
          messages.map((m) => <MessageBubble key={m.id} m={m} />)
        )}
      </div>
      <div className="shrink-0 border-t border-zinc-800 p-3 space-y-2 bg-zinc-950/40">
        <div className="flex flex-wrap gap-2">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setInput(s)}
              className="text-xs px-2.5 py-1 rounded-lg border border-zinc-700 text-zinc-400 hover:text-sky-300 hover:border-sky-500/40 transition-colors"
            >
              {s}
            </button>
          ))}
        </div>
        <div className="flex items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send(input);
              }
            }}
            placeholder={busy ? "处理中…" : "输入问题或你的想法，Enter 发送"}
            rows={3}
            disabled={busy}
            className="flex-1 resize-none bg-zinc-950 border border-zinc-700 rounded-xl px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 outline-none focus:ring-1 focus:ring-sky-500/40"
          />
          <button
            type="button"
            disabled={busy || !input.trim()}
            onClick={() => send(input)}
            className="px-4 py-2 rounded-xl bg-sky-600 hover:bg-sky-500 text-white text-sm font-medium disabled:opacity-40 disabled:pointer-events-none"
          >
            发送
          </button>
        </div>
      </div>
    </div>
  );
}

function stripThinkingEcho(text: string): string {
  if (!text) return text;
  let s = text;
  s = s.replace(/<think>[\s\S]*?<\/redacted_thinking>/gi, "");
  s = s.replace(/<reasoning>[\s\S]*?<\/reasoning>/gi, "");
  s = s.replace(/<thinking>[\s\S]*?<\/thinking>/gi, "");
  s = s.replace(/\\think[\s\S]*?\\end/gi, "");
  return s.replace(/\n{3,}/g, "\n\n").trim();
}

function MessageBubble({ m }: { m: ChatMessage }) {
  const me = m.role === "user";
  const body = me ? m.content : stripThinkingEcho(m.content);
  return (
    <div className={`flex ${me ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[92%] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed whitespace-pre-wrap ${
          me
            ? "bg-sky-600/25 text-zinc-100 border border-sky-500/20"
            : "bg-zinc-950 text-zinc-200 border border-zinc-800"
        }`}
      >
        {!me && m.intent === "intervention" ? (
          <span className="block text-[10px] text-sky-500/90 mb-1 uppercase tracking-wider">叙事</span>
        ) : null}
        {!me && m.intent === "qa" ? (
          <span className="block text-[10px] text-zinc-500 mb-1 uppercase tracking-wider">解说</span>
        ) : null}
        {!me && m.intent === "system" ? (
          <span className="block text-[10px] text-zinc-500 mb-1 uppercase tracking-wider">状态</span>
        ) : null}
        <div>{body}</div>
      </div>
    </div>
  );
}
