"use client";
import clsx from "clsx";
import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Badge, Button, Card, Empty, Spinner, when } from "@/components/ui";
import { del, get, post } from "@/lib/api";

const TABS = [
  { key: "retrieval", label: "검색" },
  { key: "calls", label: "모델 호출" },
  { key: "jobs", label: "색인 작업" },
  { key: "audit", label: "감사" },
] as const;
type TabKey = (typeof TABS)[number]["key"];

interface Row { id: string; created_at: string; [k: string]: unknown }

export function LogsPanel() {
  const [tab, setTab] = useState<TabKey>("retrieval");
  const [rows, setRows] = useState<Row[] | null>(null);

  const load = useCallback(async (t: TabKey) => {
    setRows(null);
    const d = await get<{ items: Row[] }>(`/api/admin/logs/${t}?limit=100`);
    setRows(d.items);
  }, []);

  useEffect(() => { load(tab).catch(() => setRows([])); }, [tab, load]);

  const purge = async () => {
    if (!confirm("보관 기간이 지난 로그를 지웁니다. 계속할까요?")) return;
    const r = await del<Record<string, number>>("/api/admin/logs?days=30");
    alert(Object.entries(r).map(([k, v]) => `${k}: ${v}건`).join("\n"));
    await load(tab);
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-1">
        {TABS.map((t) => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={clsx("rounded-lg px-2.5 py-1 text-[12.5px] font-medium transition-colors",
              tab === t.key ? "bg-accent-soft text-accent" : "text-[#8b949e] hover:bg-muted")}>
            {t.label}
          </button>
        ))}
        <Button size="sm" variant="ghost" className="ml-auto" onClick={() => load(tab)}>
          <RefreshCw className="size-3.5" />새로고침
        </Button>
        <Button size="sm" variant="outline" onClick={purge}>오래된 로그 정리</Button>
      </div>

      {rows === null ? <Spinner /> : !rows.length ? <Empty title="기록이 없습니다" /> : (
        <Card className="divide-y divide-line">
          {rows.map((r) => <LogRow key={r.id} tab={tab} row={r} onRetry={() => load(tab)} />)}
        </Card>
      )}
    </div>
  );
}

function LogRow({ tab, row, onRetry }: { tab: TabKey; row: Row; onRetry: () => void }) {
  const [open, setOpen] = useState(false);
  const time = <span className="w-20 shrink-0 text-[11.5px] text-[#c1c7cd]">{when(row.created_at)}</span>;

  if (tab === "retrieval") {
    const legs = (row.legs ?? {}) as Record<string, number>;
    return (
      <div className="px-4 py-2.5">
        <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-2.5 text-left">
          {time}
          <span className="min-w-0 flex-1 truncate text-[13px]">{String(row.query)}</span>
          <span className="shrink-0 text-[11.5px] text-[#8b949e]">
            후보 {String(row.candidates)} → {String(row.returned)}
          </span>
          {Object.entries(legs).filter(([, n]) => n > 0).map(([k, n]) => <Badge key={k}>{k} {n}</Badge>)}
          {Boolean(row.reranked) && <Badge tone="ok">rerank</Badge>}
          <span className="shrink-0 text-[11.5px] text-[#c1c7cd]">{String(row.latency_ms)}ms</span>
        </button>
        {open && (
          <div className="mt-2 space-y-1 rounded-lg bg-muted p-2.5">
            {((row.passages ?? []) as { path: string; heading: string; score: number; legs: string[] }[]).map((p, i) => (
              <p key={i} className="flex gap-2 text-[11.5px]">
                <span className="font-semibold text-accent">{i + 1}</span>
                <span className="min-w-0 flex-1 truncate">{p.path}{p.heading ? ` · ${p.heading}` : ""}</span>
                <span className="text-[#8b949e]">{p.legs?.join("+")}</span>
                <span className="text-[#c1c7cd]">{p.score}</span>
              </p>
            ))}
          </div>
        )}
      </div>
    );
  }

  if (tab === "calls") {
    return (
      <div className="flex items-center gap-2.5 px-4 py-2.5">
        {time}
        <Badge tone={row.status === "ok" ? "ok" : "bad"}>{String(row.status)}</Badge>
        <span className="w-28 shrink-0 truncate text-[12px] text-[#8b949e]">{String(row.provider)}</span>
        <span className="min-w-0 flex-1 truncate text-[12.5px]">{String(row.model)}</span>
        <span className="shrink-0 text-[11.5px] text-[#8b949e]">
          ↓{String(row.tokens_in)} ↑{String(row.tokens_out)} · 도구 {String(row.tool_calls)}
        </span>
        <span className="shrink-0 text-[11.5px] text-[#c1c7cd]">{String(row.latency_ms)}ms</span>
        {Boolean(row.error) && <span className="max-w-60 truncate text-[11.5px] text-red-600">{String(row.error)}</span>}
      </div>
    );
  }

  if (tab === "jobs") {
    const tone = row.status === "done" ? "ok" : row.status === "dead" ? "bad" : row.status === "running" ? "accent" : "neutral";
    return (
      <div className="flex items-center gap-2.5 px-4 py-2.5">
        {time}
        <Badge tone={tone as "ok" | "bad" | "accent" | "neutral"}>{String(row.status)}</Badge>
        <span className="w-28 shrink-0 text-[12.5px]">{String(row.kind)}</span>
        <span className="min-w-0 flex-1 truncate text-[11.5px] text-[#8b949e]">
          {row.last_error ? String(row.last_error) : JSON.stringify(row.result ?? row.payload)}
        </span>
        <span className="shrink-0 text-[11.5px] text-[#c1c7cd]">시도 {String(row.attempts)}</span>
        {(row.status === "dead" || row.status === "queued") && (
          <button onClick={async () => { await post(`/api/admin/logs/jobs/${row.id}/retry`); onRetry(); }}
            className="shrink-0 text-[11.5px] text-accent hover:underline">재시도</button>
        )}
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2.5 px-4 py-2.5">
      {time}
      <span className="w-44 shrink-0 truncate text-[12.5px] font-medium">{String(row.action)}</span>
      <span className="min-w-0 flex-1 truncate text-[11.5px] text-[#8b949e]">
        {row.target_type ? `${row.target_type} ${String(row.target_id).slice(0, 8)}` : ""} {JSON.stringify(row.meta ?? {})}
      </span>
      <span className="shrink-0 text-[11.5px] text-[#c1c7cd]">{String(row.ip ?? "")}</span>
    </div>
  );
}
