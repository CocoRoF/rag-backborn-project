"use client";
import clsx from "clsx";
import { useEffect, useState } from "react";

import { CalibrationPanel } from "@/components/admin/CalibrationPanel";
import { LogsPanel } from "@/components/admin/LogsPanel";
import { PluginsPanel } from "@/components/admin/PluginsPanel";
import { ProvidersPanel } from "@/components/admin/ProvidersPanel";
import { Badge, Card, Spinner, bytes } from "@/components/ui";
import { get, patch } from "@/lib/api";

const TABS = [
  { key: "overview", label: "개요" },
  { key: "providers", label: "공급자 · 모델" },
  { key: "rag", label: "임베딩 · RAG" },
  { key: "plugins", label: "플러그인" },
  { key: "users", label: "사용자" },
  { key: "logs", label: "로그" },
] as const;
type TabKey = (typeof TABS)[number]["key"];

export function AdminView() {
  const [tab, setTab] = useState<TabKey>("overview");
  return (
    <div className="flex h-full flex-col">
      <header className="flex h-12 shrink-0 items-center gap-1 border-b border-line bg-white px-4">
        {TABS.map((t) => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={clsx("rounded-lg px-3 py-1.5 text-[13px] font-medium transition-colors",
              tab === t.key ? "bg-accent-soft text-accent" : "text-[#57606a] hover:bg-muted")}>
            {t.label}
          </button>
        ))}
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto p-5">
        <div className="mx-auto max-w-4xl">
          {tab === "overview" && <Overview />}
          {tab === "providers" && <ProvidersPanel />}
          {tab === "rag" && <CalibrationPanel />}
          {tab === "plugins" && <PluginsPanel />}
          {tab === "users" && <Users />}
          {tab === "logs" && <LogsPanel />}
        </div>
      </div>
    </div>
  );
}

interface OverviewData {
  users: number; agents: number; repositories: number; files: number; bytes: number; failed_files: number;
  conversations: number; messages: number; active_turns: number;
  calls_24h: { count: number; tokens_in: number; tokens_out: number };
  jobs: Record<string, number>;
}
interface HealthData {
  storage: { backend: string; ok: boolean; detail: string };
  embedding: { ok: boolean; detail: string };
  claude_code: { version: string; credentials_present: boolean; expired: boolean; auth_mode: string; subscription?: string };
  providers: Record<string, { ok: boolean; detail: string }>;
}

function Stat({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <Card className="px-4 py-3">
      <p className="text-[12px] text-[#8b949e]">{label}</p>
      <p className="mt-0.5 text-xl font-semibold tabular-nums">{value}</p>
      {hint && <p className="text-[11.5px] text-[#c1c7cd]">{hint}</p>}
    </Card>
  );
}

function Overview() {
  const [d, setD] = useState<OverviewData | null>(null);
  const [h, setH] = useState<HealthData | null>(null);
  useEffect(() => {
    get<OverviewData>("/api/admin/overview").then(setD).catch(() => setD(null));
    get<HealthData>("/api/admin/health").then(setH).catch(() => setH(null));
  }, []);
  if (!d) return <Spinner />;
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="사용자" value={d.users} />
        <Stat label="에이전트" value={d.agents} />
        <Stat label="저장소" value={d.repositories} hint={`${d.files}개 파일 · ${bytes(d.bytes)}`} />
        <Stat label="대화" value={d.conversations} hint={`메시지 ${d.messages}건`} />
        <Stat label="24시간 모델 호출" value={d.calls_24h.count}
          hint={`입력 ${d.calls_24h.tokens_in.toLocaleString()} · 출력 ${d.calls_24h.tokens_out.toLocaleString()} 토큰`} />
        <Stat label="진행 중인 턴" value={d.active_turns} />
        <Stat label="색인 실패 파일" value={d.failed_files} />
        <Stat label="작업 큐" value={Object.entries(d.jobs).map(([k, v]) => `${k} ${v}`).join(" / ") || "비어 있음"} />
      </div>
      {h && (
        <Card className="divide-y divide-line">
          <p className="px-4 py-2.5 text-[13px] font-semibold">연결 상태</p>
          <Row label="객체 저장소" ok={h.storage.ok} detail={`${h.storage.backend} · ${h.storage.detail}`} />
          <Row label="임베딩" ok={h.embedding.ok} detail={h.embedding.detail} />
          <Row label="Claude Code CLI" ok={h.claude_code.credentials_present && !h.claude_code.expired}
            detail={`${h.claude_code.version} · ${h.claude_code.auth_mode}${h.claude_code.subscription ? ` · ${h.claude_code.subscription}` : ""}`} />
          {Object.entries(h.providers).map(([k, v]) => <Row key={k} label={k} ok={v.ok} detail={v.detail} />)}
        </Card>
      )}
    </div>
  );
}

function Row({ label, ok, detail }: { label: string; ok: boolean; detail: string }) {
  return (
    <div className="flex items-center gap-3 px-4 py-2.5">
      <span className="w-40 shrink-0 text-[13px] font-medium">{label}</span>
      <Badge tone={ok ? "ok" : "warn"}>{ok ? "정상" : "미설정"}</Badge>
      <span className="min-w-0 flex-1 truncate text-[12.5px] text-[#8b949e]">{detail}</span>
    </div>
  );
}

interface AdminUser { id: string; email: string; name: string; role: string; status: string; last_login_at: string | null }

function Users() {
  const [items, setItems] = useState<AdminUser[] | null>(null);
  const load = () => get<{ items: AdminUser[] }>("/api/admin/users").then((d) => setItems(d.items));
  useEffect(() => { load().catch(() => setItems([])); }, []);
  const update = async (u: AdminUser, body: Record<string, string>) => {
    await patch(`/api/admin/users/${u.id}`, body);
    await load();
  };
  if (!items) return <Spinner />;
  return (
    <Card className="divide-y divide-line">
      {items.map((u) => (
        <div key={u.id} className="flex items-center gap-3 px-4 py-2.5">
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[13px] font-medium">{u.name || u.email}</span>
            <span className="block truncate text-[11.5px] text-[#8b949e]">{u.email}</span>
          </span>
          <Badge tone={u.role === "admin" ? "accent" : "neutral"}>{u.role}</Badge>
          <Badge tone={u.status === "active" ? "ok" : "bad"}>{u.status}</Badge>
          <button onClick={() => update(u, { role: u.role === "admin" ? "user" : "admin" })}
            className="text-[12px] text-accent hover:underline">
            {u.role === "admin" ? "일반으로" : "관리자로"}
          </button>
          <button onClick={() => update(u, { status: u.status === "active" ? "disabled" : "active" })}
            className="text-[12px] text-[#8b949e] hover:underline">
            {u.status === "active" ? "정지" : "해제"}
          </button>
        </div>
      ))}
    </Card>
  );
}
