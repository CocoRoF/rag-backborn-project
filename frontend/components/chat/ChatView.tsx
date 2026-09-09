"use client";
import clsx from "clsx";
import { MessageSquarePlus, Plus, Send, Settings, Square, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { AgentDialog } from "@/components/chat/AgentDialog";
import { MessageRow } from "@/components/chat/Message";
import { Badge, Button, Empty, Spinner, Textarea, when } from "@/components/ui";
import { ApiError, del, get, post, stream } from "@/lib/api";
import type { Agent, ChatMessage, Citation, Conversation, ToolEvent } from "@/lib/types";

const MODE_LABEL: Record<string, string> = { agentic: "에이전트 검색", auto: "자동 검색", off: "검색 안 함" };

export function ChatView() {
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [agentId, setAgentId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [convId, setConvId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [dialog, setDialog] = useState<{ open: boolean; agent: Agent | null }>({ open: false, agent: null });
  const [error, setError] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const agent = agents?.find((a) => a.id === agentId) ?? null;

  useEffect(() => {
    get<{ items: Agent[] }>("/api/agents").then((d) => {
      setAgents(d.items);
      if (d.items.length) setAgentId((cur) => cur ?? d.items[0].id);
    }).catch(() => setAgents([]));
  }, []);

  const loadConversations = useCallback(async (id: string) => {
    const d = await get<{ items: Conversation[] }>(`/api/agents/${id}/conversations`);
    setConversations(d.items);
    return d.items;
  }, []);

  useEffect(() => {
    if (!agentId) return;
    setMessages([]); setConvId(null);
    loadConversations(agentId).catch(() => setConversations([]));
  }, [agentId, loadConversations]);

  const openConversation = async (id: string) => {
    if (!agentId) return;
    setConvId(id);
    const d = await get<{ messages: ChatMessage[] }>(`/api/agents/${agentId}/conversations/${id}`);
    setMessages(d.messages);
  };

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  const send = async () => {
    const text = input.trim();
    if (!text || !agentId || sending) return;
    setInput(""); setError(""); setSending(true);
    setMessages((m) => [...m, { id: `u_${Date.now()}`, role: "user", content: text, citations: [], tool_events: [] },
                             { id: `a_${Date.now()}`, role: "assistant", content: "", citations: [], tool_events: [], pending: true }]);
    const controller = new AbortController();
    abortRef.current = controller;
    // One assistant bubble is mutated in place as events arrive; replacing the array each
    // time would remount the markdown renderer on every token.
    const patchLast = (fn: (m: ChatMessage) => ChatMessage) =>
      setMessages((all) => all.map((m, i) => (i === all.length - 1 ? fn(m) : m)));
    try {
      await stream("/api/chat/turn", { agent_id: agentId, conversation_id: convId, message: text }, (ev) => {
        const t = ev.type as string;
        if (t === "text") patchLast((m) => ({ ...m, content: m.content + (ev.text as string) }));
        else if (t === "tool") patchLast((m) => ({ ...m, tool_events: [...m.tool_events, ev as unknown as ToolEvent] }));
        else if (t === "citations") patchLast((m) => ({ ...m, citations: ev.items as Citation[] }));
        else if (t === "error") patchLast((m) => ({ ...m, error: ev.message as string, pending: false }));
        else if (t === "done") patchLast((m) => ({ ...m, id: ev.message_id as string, pending: false,
                                                   latency_ms: ev.latency_ms as number }));
      }, controller.signal);
      if (agentId) {
        const items = await loadConversations(agentId);
        if (!convId && items.length) setConvId(items[0].id);
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        const msg = e instanceof ApiError ? e.message : "응답을 받지 못했습니다";
        setError(msg);
        patchLast((m) => ({ ...m, error: msg, pending: false }));
      }
    } finally {
      setSending(false); abortRef.current = null;
      patchLast((m) => ({ ...m, pending: false }));
    }
  };

  const removeConversation = async (id: string) => {
    if (!agentId) return;
    await del(`/api/agents/${agentId}/conversations/${id}`);
    setConversations((c) => c.filter((x) => x.id !== id));
    if (convId === id) { setConvId(null); setMessages([]); }
  };

  const removeAgent = async (a: Agent) => {
    if (!confirm(`'${a.name}' 에이전트를 삭제할까요? 대화 기록도 함께 사라집니다.`)) return;
    await del(`/api/agents/${a.id}`);
    setAgents((list) => (list ?? []).filter((x) => x.id !== a.id));
    if (agentId === a.id) setAgentId(null);
  };

  if (agents === null) return <Spinner />;

  return (
    <div className="flex h-full">
      <aside className="flex w-64 shrink-0 flex-col border-r border-line bg-white">
        <div className="flex items-center justify-between px-3 py-2.5">
          <span className="text-[12px] font-semibold uppercase tracking-wide text-[#8b949e]">에이전트</span>
          <Button size="sm" variant="ghost" onClick={() => setDialog({ open: true, agent: null })}>
            <Plus className="size-3.5" />새 에이전트
          </Button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
          {agents.map((a) => (
            <button key={a.id} onClick={() => setAgentId(a.id)}
              className={clsx("group mb-0.5 flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left transition-colors",
                agentId === a.id ? "bg-accent-soft" : "hover:bg-muted")}>
              <span className="text-base">{a.emoji}</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] font-medium">{a.name}</span>
                <span className="block truncate text-[11px] text-[#8b949e]">
                  {a.repository_ids.length ? `저장소 ${a.repository_ids.length}개` : "저장소 미연결"}
                </span>
              </span>
              <span onClick={(e) => { e.stopPropagation(); removeAgent(a); }}
                className="hidden rounded p-1 text-[#c1c7cd] hover:text-red-500 group-hover:block">
                <Trash2 className="size-3.5" />
              </span>
            </button>
          ))}
          {!agents.length && (
            <p className="px-2 py-6 text-center text-[12.5px] leading-relaxed text-[#8b949e]">
              에이전트가 없습니다.<br />[새 에이전트]로 모델과 저장소를 고르세요.
            </p>
          )}
        </div>
        {agent && (
          <div className="border-t border-line px-2 py-2">
            <div className="mb-1 flex items-center justify-between px-1">
              <span className="text-[12px] font-semibold uppercase tracking-wide text-[#8b949e]">대화</span>
              <button onClick={() => { setConvId(null); setMessages([]); }}
                className="rounded p-1 text-[#8b949e] hover:bg-muted" title="새 대화">
                <MessageSquarePlus className="size-3.5" />
              </button>
            </div>
            <div className="max-h-52 overflow-y-auto">
              {conversations.map((c) => (
                <div key={c.id} className={clsx("group flex items-center gap-1 rounded-lg px-2 py-1.5",
                  convId === c.id ? "bg-muted" : "hover:bg-muted")}>
                  <button onClick={() => openConversation(c.id)} className="min-w-0 flex-1 text-left">
                    <span className="block truncate text-[12.5px]">{c.title}</span>
                    <span className="text-[11px] text-[#c1c7cd]">{when(c.updated_at)}</span>
                  </button>
                  <button onClick={() => removeConversation(c.id)}
                    className="hidden rounded p-1 text-[#c1c7cd] hover:text-red-500 group-hover:block">
                    <Trash2 className="size-3" />
                  </button>
                </div>
              ))}
              {!conversations.length && <p className="px-2 py-3 text-[12px] text-[#c1c7cd]">아직 대화가 없습니다.</p>}
            </div>
          </div>
        )}
      </aside>

      <section className="flex min-w-0 flex-1 flex-col">
        {!agent ? (
          <Empty title="에이전트를 만들어 시작하세요"
            hint="모델과 RAG 저장소를 고르면 그 저장소만 근거로 답하는 챗봇이 만들어집니다."
            action={<Button className="mt-2" onClick={() => setDialog({ open: true, agent: null })}>
              <Plus className="size-4" />새 에이전트</Button>} />
        ) : (
          <>
            <header className="flex h-12 shrink-0 items-center gap-2 border-b border-line bg-white px-4">
              <span>{agent.emoji}</span>
              <span className="text-[14px] font-medium">{agent.name}</span>
              <Badge tone="accent">{MODE_LABEL[agent.retrieval_mode]}</Badge>
              <Badge>{agent.model}</Badge>
              {!agent.repository_ids.length && agent.retrieval_mode !== "off" && (
                <Badge tone="warn">저장소 미연결</Badge>
              )}
              <button onClick={() => setDialog({ open: true, agent })}
                className="ml-auto rounded-lg p-1.5 text-[#8b949e] hover:bg-muted" title="설정">
                <Settings className="size-4" />
              </button>
            </header>

            <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
              <div className="mx-auto max-w-3xl space-y-5">
                {!messages.length && (
                  <Empty title={`${agent.name}에게 물어보세요`}
                    hint={agent.repository_ids.length
                      ? "연결된 저장소에서 근거를 찾아 답합니다. 답변의 [1] 표시를 누르면 원문이 열립니다."
                      : "이 에이전트에는 저장소가 연결되어 있지 않습니다. 설정에서 연결해 주세요."} />
                )}
                {messages.map((m) => <MessageRow key={m.id} m={m} />)}
                <div ref={bottomRef} />
              </div>
            </div>

            <div className="shrink-0 border-t border-line bg-white px-6 py-3">
              <div className="mx-auto flex max-w-3xl items-end gap-2">
                <Textarea rows={1} value={input} placeholder="질문을 입력하세요 (Shift+Enter 줄바꿈)"
                  className="max-h-40 min-h-[42px]"
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
                {sending ? (
                  <Button variant="outline" onClick={() => abortRef.current?.abort()}><Square className="size-3.5" />중지</Button>
                ) : (
                  <Button onClick={send} disabled={!input.trim()}><Send className="size-4" /></Button>
                )}
              </div>
              {error && <p className="mx-auto mt-2 max-w-3xl text-[12.5px] text-red-600">{error}</p>}
            </div>
          </>
        )}
      </section>

      {dialog.open && (
        <AgentDialog agent={dialog.agent} onClose={() => setDialog({ open: false, agent: null })}
          onSaved={(a) => {
            setAgents((list) => {
              const next = (list ?? []).filter((x) => x.id !== a.id);
              return dialog.agent ? (list ?? []).map((x) => (x.id === a.id ? a : x)) : [a, ...next];
            });
            setAgentId(a.id);
            setDialog({ open: false, agent: null });
          }} />
      )}
    </div>
  );
}
