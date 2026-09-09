"use client";
import { useEffect, useState } from "react";

import { Badge, Button, Card, Field, Input, Select, Spinner, Textarea } from "@/components/ui";
import { ApiError, del, get, patch, post, put } from "@/lib/api";

type SettingsMap = Record<string, unknown>;
interface Secret { has_value: boolean; masked: string }

const KEYS: { key: string; label: string; hint: string; provider: string }[] = [
  { key: "providers.anthropic.api_key", label: "Anthropic API 키", hint: "Claude 모델 직접 호출 · console.anthropic.com", provider: "anthropic" },
  { key: "providers.openai.api_key", label: "OpenAI API 키", hint: "GPT 모델 + 기본 임베딩 · platform.openai.com", provider: "openai" },
  { key: "providers.google.api_key", label: "Google AI 키", hint: "Gemini 모델 + 임베딩 · aistudio.google.com", provider: "google" },
  { key: "providers.voyage.api_key", label: "Voyage 키", hint: "임베딩 및 재순위(rerank) 전용 · voyageai.com", provider: "voyage" },
];

interface ModelRow {
  id: string; provider: string; model_id: string; display_name: string; cli_alias: string | null;
  context_window: number; enabled: boolean; is_default: boolean;
}

interface ClaudeStatus {
  version: string; binary: string; auth_mode: string; credentials_present: boolean; expired: boolean;
  session_expires_at: string | null; subscription?: string; login_running?: boolean;
}

export function ProvidersPanel() {
  const [values, setValues] = useState<SettingsMap | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [probes, setProbes] = useState<Record<string, { ok: boolean; detail: string }>>({});
  const [models, setModels] = useState<ModelRow[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const loadSettings = () => get<{ values: SettingsMap }>("/api/admin/settings?prefix=providers.")
    .then((d) => setValues(d.values));
  const loadModels = () => get<{ items: ModelRow[] }>("/api/admin/models").then((d) => setModels(d.items));

  useEffect(() => { loadSettings().catch(() => setValues({})); loadModels().catch(() => setModels([])); }, []);

  const save = async () => {
    setBusy(true); setError("");
    try {
      const payload = Object.fromEntries(Object.entries(drafts).filter(([, v]) => v !== ""));
      if (Object.keys(payload).length) await put("/api/admin/settings", { values: payload });
      setDrafts({});
      await loadSettings();
    } catch (e) { setError(e instanceof ApiError ? e.message : "저장에 실패했습니다"); }
    finally { setBusy(false); }
  };

  const probe = async (provider: string) => {
    setProbes((p) => ({ ...p, [provider]: { ok: false, detail: "확인 중…" } }));
    const r = await post<{ ok: boolean; detail: string }>(`/api/admin/providers/${provider}/test`);
    setProbes((p) => ({ ...p, [provider]: r }));
  };

  if (!values) return <Spinner />;

  return (
    <div className="space-y-5">
      <Card className="space-y-4 p-5">
        <div>
          <h3 className="text-[14px] font-semibold">API 키</h3>
          <p className="mt-0.5 text-[12.5px] text-[#8b949e]">
            공개 API로 접근 가능한 공급자만 지원합니다. 저장된 키는 암호화되어 다시 표시되지 않습니다.
          </p>
        </div>
        {KEYS.map((k) => {
          const cur = values[k.key] as Secret | undefined;
          const p = probes[k.provider];
          return (
            <Field key={k.key} label={k.label} hint={k.hint}>
              <div className="flex gap-2">
                <Input type="password" placeholder={cur?.has_value ? `저장됨 (${cur.masked})` : "미설정"}
                  value={drafts[k.key] ?? ""} onChange={(e) => setDrafts({ ...drafts, [k.key]: e.target.value })} />
                <Button variant="outline" onClick={() => probe(k.provider)}>연결 확인</Button>
              </div>
              {p && <p className="mt-1 flex items-center gap-1.5 text-[12px]">
                <Badge tone={p.ok ? "ok" : "bad"}>{p.ok ? "정상" : "실패"}</Badge>
                <span className="text-[#8b949e]">{p.detail}</span>
              </p>}
            </Field>
          );
        })}
        {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-[13px] text-red-600">{error}</p>}
        <div className="flex justify-end"><Button onClick={save} busy={busy} disabled={!Object.keys(drafts).length}>저장</Button></div>
      </Card>

      <ClaudeCodeCard onProbe={() => probe("claude_code")} probe={probes["claude_code"]} />

      <Card className="divide-y divide-line">
        <div className="flex items-center gap-2 px-4 py-3">
          <h3 className="text-[14px] font-semibold">모델 카탈로그</h3>
          <p className="text-[12px] text-[#8b949e]">에이전트가 고를 수 있는 목록입니다.</p>
          <Button size="sm" variant="outline" className="ml-auto"
            onClick={async () => { await post("/api/admin/models/seed"); await loadModels(); }}>최신 목록 반영</Button>
        </div>
        {(models ?? []).map((m) => (
          <div key={m.id} className="flex items-center gap-3 px-4 py-2">
            <span className="w-24 shrink-0 text-[12px] text-[#8b949e]">{m.provider}</span>
            <span className="min-w-0 flex-1 truncate text-[13px]">{m.display_name}</span>
            {m.cli_alias && <Badge tone="warn">alias {m.cli_alias}</Badge>}
            {m.is_default && <Badge tone="accent">기본</Badge>}
            <button onClick={async () => { await patch(`/api/admin/models/${m.id}`, { enabled: !m.enabled }); await loadModels(); }}
              className="text-[12px] text-[#8b949e] hover:underline">{m.enabled ? "사용 중" : "사용 안 함"}</button>
            {!m.is_default && (
              <button onClick={async () => { await patch(`/api/admin/models/${m.id}`, { is_default: true }); await loadModels(); }}
                className="text-[12px] text-accent hover:underline">기본으로</button>
            )}
          </div>
        ))}
      </Card>
    </div>
  );
}

/** Claude Code is authenticated by logging the container in, not by pasting a key: the CLI
 *  refreshes its own credential, and a file copied from elsewhere dies the moment the
 *  original refreshes. */
function ClaudeCodeCard({ onProbe, probe }: { onProbe: () => void; probe?: { ok: boolean; detail: string } }) {
  const [st, setSt] = useState<ClaudeStatus | null>(null);
  const [login, setLogin] = useState<{ running: boolean; lines: { seq: number; text: string }[] } | null>(null);
  const [code, setCode] = useState("");
  const [mode, setMode] = useState("oauth");
  const [creds, setCreds] = useState("");

  const loadStatus = () => get<ClaudeStatus>("/api/admin/claude/status").then((s) => { setSt(s); setMode(s.auth_mode); });
  useEffect(() => { loadStatus().catch(() => setSt(null)); }, []);
  useEffect(() => {
    if (!login?.running) return;
    const t = setTimeout(() => get<typeof login>("/api/admin/claude/login").then(setLogin).catch(() => {}), 1500);
    return () => clearTimeout(t);
  }, [login]);

  return (
    <Card className="space-y-4 p-5">
      <div className="flex items-center gap-2">
        <h3 className="text-[14px] font-semibold">Claude Code CLI</h3>
        {st && <Badge tone={st.credentials_present && !st.expired ? "ok" : "warn"}>
          {st.credentials_present ? (st.expired ? "만료됨" : "로그인됨") : "미로그인"}
        </Badge>}
        {st && <span className="text-[12px] text-[#8b949e]">{st.version}</span>}
        <Button size="sm" variant="outline" className="ml-auto" onClick={onProbe}>연결 확인</Button>
      </div>
      {probe && <p className="text-[12px]"><Badge tone={probe.ok ? "ok" : "bad"}>{probe.ok ? "정상" : "실패"}</Badge>
        <span className="ml-1.5 text-[#8b949e]">{probe.detail}</span></p>}

      <Field label="인증 방식" hint="구독 로그인(oauth)이 기본입니다. api_key 는 위의 Anthropic 키를 사용합니다.">
        <div className="flex gap-2">
          <Select value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="oauth">oauth — 구독 디바이스 로그인</option>
            <option value="api_key">api_key — Anthropic API 키</option>
            <option value="setup_token">setup_token — 발급 토큰</option>
          </Select>
          <Button variant="outline" onClick={async () => {
            await put("/api/admin/settings", { values: { "providers.claude_code.auth_mode": mode } });
            await loadStatus();
          }}>적용</Button>
        </div>
      </Field>

      <div className="space-y-2">
        <div className="flex gap-2">
          <Button variant="outline" onClick={async () => setLogin(await post("/api/admin/claude/login", { console: false }))}>
            디바이스 로그인 시작
          </Button>
          {login?.running && (
            <Button variant="danger" onClick={async () => setLogin(await del("/api/admin/claude/login"))}>취소</Button>
          )}
        </div>
        {login && (
          <>
            <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-lg bg-ink px-3 py-2 text-[11.5px] leading-relaxed text-[#e6edf3]">
              {login.lines.map((l) => l.text).join("\n") || "시작하는 중…"}
            </pre>
            {login.running && (
              <div className="flex gap-2">
                <Input value={code} placeholder="브라우저에서 받은 코드를 붙여넣으세요"
                  onChange={(e) => setCode(e.target.value)} />
                <Button onClick={async () => {
                  setLogin(await post("/api/admin/claude/login/input", { text: code }));
                  setCode("");
                  setTimeout(() => loadStatus().catch(() => {}), 2500);
                }}>전송</Button>
              </div>
            )}
          </>
        )}
      </div>

      <details className="text-[12.5px]">
        <summary className="cursor-pointer text-[#8b949e]">credentials.json 직접 넣기</summary>
        <div className="mt-2 space-y-2">
          <Textarea rows={3} value={creds} placeholder='{"claudeAiOauth": {...}}' onChange={(e) => setCreds(e.target.value)} />
          <Button size="sm" variant="outline" onClick={async () => {
            await post("/api/admin/claude/credentials", { credentials_json: creds });
            setCreds(""); await loadStatus();
          }}>가져오기</Button>
          <p className="text-[11.5px] text-[#c1c7cd]">
            다른 기기에서 복사한 자격은 원본이 갱신되는 순간 무효가 됩니다. 이 서버에서 직접 디바이스 로그인하는 편이 안전합니다.
          </p>
        </div>
      </details>
    </Card>
  );
}
