"use client";
import clsx from "clsx";
import { AlertCircle, ChevronDown, FileText, Search, Wrench } from "lucide-react";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { Badge } from "@/components/ui";
import type { ChatMessage, Citation, ToolEvent } from "@/lib/types";

const TOOL_LABEL: Record<string, string> = {
  rag_search: "저장소 검색", rag_browse: "폴더 탐색", rag_outline: "문서 목차", rag_read: "문서 읽기",
};

function ToolTrail({ events }: { events: ToolEvent[] }) {
  const [open, setOpen] = useState(false);
  // Pair start/end by order of arrival: the bridge emits them strictly in sequence.
  const rows = events.filter((e) => e.phase === "end");
  const running = events.filter((e) => e.phase === "start").length - rows.length;
  if (!events.length) return null;
  return (
    <div className="mb-2">
      <button onClick={() => setOpen(!open)}
        className="inline-flex items-center gap-1.5 rounded-full border border-line bg-white px-2.5 py-1 text-[12px] text-[#57606a] hover:bg-muted">
        <Wrench className="size-3" />
        {running > 0 ? <span className="pulse-dot">검색 중…</span> : `도구 ${rows.length}회`}
        <ChevronDown className={clsx("size-3 transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="mt-1.5 space-y-1 rounded-lg border border-line bg-white p-2">
          {rows.map((e, i) => (
            <div key={i} className="flex gap-2 text-[12px]">
              <span className={clsx("shrink-0 font-medium", e.is_error ? "text-red-600" : "text-accent")}>
                {TOOL_LABEL[e.name] ?? e.name}
              </span>
              <span className="min-w-0 flex-1 truncate text-[#8b949e]">{e.summary}</span>
              {e.duration_ms !== undefined && <span className="shrink-0 text-[#c1c7cd]">{e.duration_ms}ms</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Citations({ items }: { items: Citation[] }) {
  const [openId, setOpenId] = useState<number | null>(null);
  if (!items.length) return null;
  return (
    <div className="mt-3 space-y-1">
      <p className="flex items-center gap-1.5 text-[12px] font-medium text-[#8b949e]">
        <FileText className="size-3" />근거 {items.length}건
      </p>
      {items.map((c, i) => (
        <div key={i} className="overflow-hidden rounded-lg border border-line bg-white">
          <button onClick={() => setOpenId(openId === i ? null : i)}
            className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left hover:bg-muted">
            <span className="grid size-5 shrink-0 place-items-center rounded bg-accent-soft text-[11px] font-semibold text-accent">
              {i + 1}
            </span>
            <span className="min-w-0 flex-1 truncate text-[12.5px]">
              <span className="font-medium">{c.name}</span>
              {c.heading && <span className="text-[#8b949e]"> · {c.heading}</span>}
              {c.page && <span className="text-[#8b949e]"> · p.{c.page}</span>}
            </span>
            <span className="shrink-0 text-[11px] text-[#c1c7cd]">{c.legs.join("+")}</span>
          </button>
          {openId === i && (
            <p className="max-h-56 overflow-y-auto whitespace-pre-wrap border-t border-line bg-muted px-3 py-2 text-[12.5px] leading-relaxed text-[#4b5563]">
              {c.text}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

export function MessageRow({ m }: { m: ChatMessage }) {
  if (m.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-ink px-3.5 py-2.5 text-[14px] leading-relaxed text-white">
          {m.content}
        </div>
      </div>
    );
  }
  return (
    <div className="max-w-[92%]">
      <ToolTrail events={m.tool_events} />
      {m.error && (
        <p className="mb-2 flex items-start gap-1.5 rounded-lg bg-red-50 px-3 py-2 text-[13px] text-red-600">
          <AlertCircle className="mt-0.5 size-3.5 shrink-0" />{m.error}
        </p>
      )}
      {m.content ? (
        <div className="prose-chat text-[14px]">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
        </div>
      ) : m.pending && !m.error ? (
        <p className="flex items-center gap-1.5 text-[13px] text-[#8b949e]">
          <Search className="size-3.5" /><span className="pulse-dot">생각하는 중…</span>
        </p>
      ) : null}
      <Citations items={m.citations} />
      {!m.pending && m.latency_ms ? (
        <p className="mt-2"><Badge>{(m.latency_ms / 1000).toFixed(1)}초</Badge></p>
      ) : null}
    </div>
  );
}
