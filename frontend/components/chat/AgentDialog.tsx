"use client";
import { X } from "lucide-react";
import { useEffect, useState } from "react";

import { Button, Card, Field, Input, Select, Textarea } from "@/components/ui";
import { ApiError, get, patch, post } from "@/lib/api";
import type { Agent, ModelOption } from "@/lib/types";

interface Options {
  models: ModelOption[];
  default: { provider: string; model_id: string } | null;
  repositories: { id: string; name: string; file_count: number; chunk_count: number; visibility: string }[];
  default_retrieval_mode: string;
}

const MODES: { value: Agent["retrieval_mode"]; label: string; hint: string }[] = [
  { value: "agentic", label: "에이전트 검색", hint: "모델이 스스로 검색어를 바꿔가며 여러 번 찾습니다. 기본값." },
  { value: "auto", label: "자동 검색", hint: "질문으로 한 번 검색해 근거를 붙여 답합니다. 빠릅니다." },
  { value: "off", label: "검색 안 함", hint: "저장소를 쓰지 않는 일반 대화." },
];

export function AgentDialog({ agent, onClose, onSaved }:
  { agent: Agent | null; onClose: () => void; onSaved: (a: Agent) => void }) {
  const [opts, setOpts] = useState<Options | null>(null);
  const [form, setForm] = useState({
    name: agent?.name ?? "",
    description: agent?.description ?? "",
    system_prompt: agent?.system_prompt ?? "",
    provider: agent?.provider ?? "",
    model: agent?.model ?? "",
    temperature: agent?.temperature ?? 0.2,
    max_tokens: agent?.max_tokens ?? 4096,
    retrieval_mode: agent?.retrieval_mode ?? "agentic",
    top_k: agent?.top_k ?? 8,
    repository_ids: agent?.repository_ids ?? [],
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    get<Options>("/api/agents/options").then((o) => {
      setOpts(o);
      setForm((f) => f.model ? f : {
        ...f,
        provider: o.default?.provider ?? o.models[0]?.provider ?? "",
        model: o.default?.model_id ?? o.models[0]?.model_id ?? "",
        retrieval_mode: (o.default_retrieval_mode as Agent["retrieval_mode"]) || "agentic",
      });
    }).catch((e) => setError(e instanceof ApiError ? e.message : "옵션을 불러오지 못했습니다"));
  }, []);

  const toggleRepo = (id: string) =>
    setForm((f) => ({ ...f, repository_ids: f.repository_ids.includes(id)
      ? f.repository_ids.filter((r) => r !== id) : [...f.repository_ids, id] }));

  const save = async () => {
    setBusy(true); setError("");
    try {
      const saved = agent
        ? await patch<Agent>(`/api/agents/${agent.id}`, form)
        : await post<Agent>("/api/agents", form);
      onSaved(saved);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "저장에 실패했습니다");
    } finally { setBusy(false); }
  };

  const mode = MODES.find((m) => m.value === form.retrieval_mode);

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/30 p-4" onClick={onClose}>
      <Card className="flex max-h-[88dvh] w-full max-w-lg flex-col overflow-hidden" >
        <div onClick={(e) => e.stopPropagation()} className="flex min-h-0 flex-col">
          <div className="flex shrink-0 items-center justify-between border-b border-line px-5 py-3.5">
            <h2 className="text-[15px] font-semibold">{agent ? "에이전트 설정" : "새 에이전트"}</h2>
            <button onClick={onClose} className="rounded-lg p-1 text-[#8b949e] hover:bg-muted"><X className="size-4" /></button>
          </div>

          <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
            <Field label="이름">
              <Input value={form.name} placeholder="예: 사내 규정 도우미"
                onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </Field>

            <Field label="모델" hint="공개 API로 접근 가능한 모델만 노출됩니다.">
              <Select value={`${form.provider}::${form.model}`}
                onChange={(e) => { const [p, m] = e.target.value.split("::"); setForm({ ...form, provider: p, model: m }); }}>
                {(opts?.models ?? []).map((m) => (
                  <option key={`${m.provider}::${m.model_id}`} value={`${m.provider}::${m.model_id}`}>
                    {m.display_name}{m.is_default ? " · 기본" : ""}
                  </option>
                ))}
              </Select>
            </Field>

            <Field label="저장소" hint="선택한 저장소만 이 에이전트가 검색합니다.">
              <div className="max-h-44 space-y-1 overflow-y-auto rounded-lg border border-line p-1.5">
                {(opts?.repositories ?? []).length === 0 && (
                  <p className="px-2 py-3 text-[13px] text-[#8b949e]">저장소가 없습니다. [RAG 저장소]에서 먼저 만들어 주세요.</p>
                )}
                {(opts?.repositories ?? []).map((r) => (
                  <label key={r.id} className="flex cursor-pointer items-center gap-2.5 rounded-md px-2 py-1.5 hover:bg-muted">
                    <input type="checkbox" checked={form.repository_ids.includes(r.id)} onChange={() => toggleRepo(r.id)}
                      className="size-4 accent-accent" />
                    <span className="flex-1 text-[13px] font-medium">{r.name}</span>
                    <span className="text-[11px] text-[#8b949e]">{r.file_count}개 파일 · {r.chunk_count}조각</span>
                  </label>
                ))}
              </div>
            </Field>

            <Field label="검색 방식" hint={mode?.hint}>
              <Select value={form.retrieval_mode}
                onChange={(e) => setForm({ ...form, retrieval_mode: e.target.value as Agent["retrieval_mode"] })}>
                {MODES.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
              </Select>
            </Field>

            <Field label="지침 (선택)" hint="말투, 역할, 답변 형식 같은 이 에이전트만의 규칙.">
              <Textarea rows={3} value={form.system_prompt} placeholder="예: 사내 규정 담당자로서 조항 번호를 함께 안내한다."
                onChange={(e) => setForm({ ...form, system_prompt: e.target.value })} />
            </Field>

            <div className="grid grid-cols-3 gap-3">
              <Field label="근거 수 (top-k)">
                <Input type="number" min={1} max={20} value={form.top_k}
                  onChange={(e) => setForm({ ...form, top_k: Number(e.target.value) })} />
              </Field>
              <Field label="temperature">
                <Input type="number" min={0} max={2} step={0.1} value={form.temperature}
                  onChange={(e) => setForm({ ...form, temperature: Number(e.target.value) })} />
              </Field>
              <Field label="최대 토큰">
                <Input type="number" min={256} max={64000} step={256} value={form.max_tokens}
                  onChange={(e) => setForm({ ...form, max_tokens: Number(e.target.value) })} />
              </Field>
            </div>

            {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-[13px] text-red-600">{error}</p>}
          </div>

          <div className="flex shrink-0 justify-end gap-2 border-t border-line px-5 py-3">
            <Button variant="outline" onClick={onClose}>취소</Button>
            <Button onClick={save} busy={busy} disabled={!form.name.trim() || !form.model}>저장</Button>
          </div>
        </div>
      </Card>
    </div>
  );
}
