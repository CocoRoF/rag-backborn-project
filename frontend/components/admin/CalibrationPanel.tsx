"use client";
import { useEffect, useState } from "react";

import { Badge, Button, Card, Field, Input, Select, Spinner } from "@/components/ui";
import { ApiError, get, post, put } from "@/lib/api";

type Values = Record<string, unknown>;

const EMBEDDING_MODELS: Record<string, { id: string; label: string; dim: number }[]> = {
  openai: [
    { id: "text-embedding-3-small", label: "text-embedding-3-small (권장)", dim: 1536 },
    { id: "text-embedding-3-large", label: "text-embedding-3-large", dim: 3072 },
  ],
  gemini: [{ id: "text-embedding-004", label: "text-embedding-004", dim: 768 }],
  voyage: [
    { id: "voyage-3", label: "voyage-3", dim: 1024 },
    { id: "voyage-3-lite", label: "voyage-3-lite", dim: 512 },
  ],
  hash: [{ id: "hash", label: "해시 (오프라인, 의미검색 아님)", dim: 1536 }],
};

const WEIGHTS: { key: string; label: string; hint: string }[] = [
  { key: "rag.weight_vector", label: "벡터 (조각)", hint: "질문에 직접 답하는 문장" },
  { key: "rag.weight_section", label: "섹션 요약", hint: "질문이 가리키는 '장' — 계층 검색 축" },
  { key: "rag.weight_lexical", label: "형태소 (tsvector)", hint: "단어가 그대로 등장할 때" },
  { key: "rag.weight_trigram", label: "부분 일치", hint: "한국어 조사 결합을 넘어서는 축" },
  { key: "rag.weight_path", label: "파일 경로", hint: "'그 매뉴얼에' 처럼 파일을 지목할 때" },
];

/** Retrieval is a set of trade-offs, not a constant. Which leg matters depends on the corpus,
 *  so the numbers are exposed with what each one actually does. */
export function CalibrationPanel() {
  const [values, setValues] = useState<Values | null>(null);
  const [draft, setDraft] = useState<Values>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [probe, setProbe] = useState<{ ok: boolean; detail: string } | null>(null);

  const load = () => get<{ values: Values }>("/api/admin/settings").then((d) => { setValues(d.values); setDraft({}); });
  useEffect(() => { load().catch(() => setValues({})); }, []);

  const v = (key: string) => (key in draft ? draft[key] : values?.[key]);
  const set = (key: string, value: unknown) => setDraft((d) => ({ ...d, [key]: value }));

  const save = async () => {
    setBusy(true); setError("");
    try { await put("/api/admin/settings", { values: draft }); await load(); }
    catch (e) { setError(e instanceof ApiError ? e.message : "저장에 실패했습니다"); }
    finally { setBusy(false); }
  };

  if (!values) return <Spinner />;
  const provider = String(v("embedding.provider") ?? "openai");
  const dirty = Object.keys(draft).length > 0;

  return (
    <div className="space-y-5 pb-16">
      <Card className="space-y-4 p-5">
        <div className="flex items-center gap-2">
          <h3 className="text-[14px] font-semibold">임베딩</h3>
          <Button size="sm" variant="outline" className="ml-auto"
            onClick={async () => setProbe(await post("/api/admin/providers/embedding/test"))}>연결 확인</Button>
        </div>
        {probe && <p className="text-[12px]"><Badge tone={probe.ok ? "ok" : "bad"}>{probe.ok ? "정상" : "실패"}</Badge>
          <span className="ml-1.5 text-[#8b949e]">{probe.detail}</span></p>}
        <div className="grid grid-cols-3 gap-3">
          <Field label="공급자">
            <Select value={provider} onChange={(e) => {
              const p = e.target.value;
              const first = EMBEDDING_MODELS[p][0];
              set("embedding.provider", p); set("embedding.model", first.id); set("embedding.dim", first.dim);
            }}>
              {Object.keys(EMBEDDING_MODELS).map((p) => <option key={p} value={p}>{p}</option>)}
            </Select>
          </Field>
          <Field label="모델">
            <Select value={String(v("embedding.model") ?? "")} onChange={(e) => {
              const m = EMBEDDING_MODELS[provider].find((x) => x.id === e.target.value);
              set("embedding.model", e.target.value);
              if (m) set("embedding.dim", m.dim);
            }}>
              {(EMBEDDING_MODELS[provider] ?? []).map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
            </Select>
          </Field>
          <Field label="차원" hint="1536 열에 맞춰 패딩/절단됩니다.">
            <Input type="number" value={Number(v("embedding.dim") ?? 1536)}
              onChange={(e) => set("embedding.dim", Number(e.target.value))} />
          </Field>
        </div>
        <p className="rounded-lg bg-accent-soft px-3 py-2 text-[12px] leading-relaxed text-accent">
          공급자나 모델을 바꾸면 <b>기존 벡터와 좌표계가 달라집니다</b>. 바꾼 뒤에는 각 저장소에서 [전체 재색인]을 실행하세요.
        </p>
      </Card>

      <Card className="space-y-4 p-5">
        <h3 className="text-[14px] font-semibold">검색 축 가중치</h3>
        <p className="-mt-2 text-[12.5px] text-[#8b949e]">
          다섯 축의 결과를 RRF(순위 역수 합)로 합칩니다. 0 으로 두면 그 축을 끕니다.
        </p>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
          {WEIGHTS.map((w) => (
            <Field key={w.key} label={w.label} hint={w.hint}>
              <Input type="number" step={0.1} min={0} max={5} value={Number(v(w.key) ?? 0)}
                onChange={(e) => set(w.key, Number(e.target.value))} />
            </Field>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Field label="top-k" hint="최종 근거 수">
            <Input type="number" value={Number(v("rag.top_k") ?? 8)} onChange={(e) => set("rag.top_k", Number(e.target.value))} />
          </Field>
          <Field label="축별 후보" hint="각 축이 가져올 개수">
            <Input type="number" value={Number(v("rag.candidates_per_leg") ?? 24)}
              onChange={(e) => set("rag.candidates_per_leg", Number(e.target.value))} />
          </Field>
          <Field label="이웃 확장" hint="앞뒤 N조각을 붙여 문맥 복원">
            <Input type="number" min={0} max={3} value={Number(v("rag.neighbor_window") ?? 1)}
              onChange={(e) => set("rag.neighbor_window", Number(e.target.value))} />
          </Field>
          <Field label="문서당 최대" hint="한 문서가 결과를 독점하지 않도록">
            <Input type="number" min={1} max={10} value={Number(v("rag.max_per_document") ?? 3)}
              onChange={(e) => set("rag.max_per_document", Number(e.target.value))} />
          </Field>
          <Field label="RRF k" hint="클수록 순위 차이가 완만해집니다">
            <Input type="number" value={Number(v("rag.rrf_k") ?? 60)} onChange={(e) => set("rag.rrf_k", Number(e.target.value))} />
          </Field>
          <Field label="벡터 최소 유사도" hint="이 아래는 후보에서 제외">
            <Input type="number" step={0.01} value={Number(v("rag.min_vector_score") ?? 0.15)}
              onChange={(e) => set("rag.min_vector_score", Number(e.target.value))} />
          </Field>
          <Field label="문맥 토큰 예산" hint="모델에 넣을 발췌 총량">
            <Input type="number" step={500} value={Number(v("rag.context_token_budget") ?? 6000)}
              onChange={(e) => set("rag.context_token_budget", Number(e.target.value))} />
          </Field>
          <Field label="도구 호출 한도" hint="에이전트 검색 최대 왕복">
            <Input type="number" min={1} max={12} value={Number(v("chat.max_tool_rounds") ?? 6)}
              onChange={(e) => set("chat.max_tool_rounds", Number(e.target.value))} />
          </Field>
        </div>
      </Card>

      <Card className="space-y-4 p-5">
        <h3 className="text-[14px] font-semibold">색인 · 재순위</h3>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Field label="조각 토큰" hint="목표 크기">
            <Input type="number" step={50} value={Number(v("rag.chunk_tokens") ?? 700)}
              onChange={(e) => set("rag.chunk_tokens", Number(e.target.value))} />
          </Field>
          <Field label="조각 최대" hint="한 조각 상한">
            <Input type="number" step={50} value={Number(v("rag.chunk_max_tokens") ?? 1000)}
              onChange={(e) => set("rag.chunk_max_tokens", Number(e.target.value))} />
          </Field>
          <Field label="겹침" hint="긴 문단 분할 시">
            <Input type="number" step={10} value={Number(v("rag.chunk_overlap") ?? 100)}
              onChange={(e) => set("rag.chunk_overlap", Number(e.target.value))} />
          </Field>
          <Field label="섹션 요약 상한" hint="문서당 요약할 섹션 수">
            <Input type="number" value={Number(v("rag.section_summary_max_sections") ?? 60)}
              onChange={(e) => set("rag.section_summary_max_sections", Number(e.target.value))} />
          </Field>
        </div>
        <Toggle label="섹션 요약 생성" value={Boolean(v("rag.section_summary_enabled"))}
          hint="색인할 때 섹션마다 요약을 만들어 임베딩합니다. 계층 검색 품질이 크게 올라가지만 모델 호출이 늘어납니다."
          onChange={(x) => set("rag.section_summary_enabled", x)} />
        <Toggle label="재순위(rerank) 사용" value={Boolean(v("rag.rerank_enabled"))}
          hint="상위 후보를 교차 인코더로 다시 정렬합니다. Voyage 키가 필요하거나, llm 선택 시 요약 모델을 사용합니다."
          onChange={(x) => set("rag.rerank_enabled", x)} />
        {Boolean(v("rag.rerank_enabled")) && (
          <div className="grid grid-cols-3 gap-3">
            <Field label="재순위 공급자">
              <Select value={String(v("rag.rerank_provider") ?? "voyage")} onChange={(e) => set("rag.rerank_provider", e.target.value)}>
                <option value="voyage">voyage</option>
                <option value="llm">llm (요약 모델 사용)</option>
              </Select>
            </Field>
            <Field label="재순위 모델">
              <Input value={String(v("rag.rerank_model") ?? "rerank-2")} onChange={(e) => set("rag.rerank_model", e.target.value)} />
            </Field>
            <Field label="재순위 후보 수">
              <Input type="number" value={Number(v("rag.rerank_candidates") ?? 30)}
                onChange={(e) => set("rag.rerank_candidates", Number(e.target.value))} />
            </Field>
          </div>
        )}
        <div className="grid grid-cols-2 gap-3">
          <Field label="요약 모델 공급자">
            <Select value={String(v("rag.section_summary_provider") ?? "claude_code")}
              onChange={(e) => set("rag.section_summary_provider", e.target.value)}>
              {["claude_code", "anthropic", "openai", "gemini"].map((p) => <option key={p} value={p}>{p}</option>)}
            </Select>
          </Field>
          <Field label="요약 모델" hint="저렴하고 빠른 모델을 권장합니다.">
            <Input value={String(v("rag.section_summary_model") ?? "")}
              onChange={(e) => set("rag.section_summary_model", e.target.value)} />
          </Field>
        </div>
      </Card>

      <Card className="space-y-4 p-5">
        <h3 className="text-[14px] font-semibold">기타</h3>
        <div className="grid grid-cols-3 gap-3">
          <Field label="기본 검색 방식">
            <Select value={String(v("chat.default_retrieval_mode") ?? "agentic")}
              onChange={(e) => set("chat.default_retrieval_mode", e.target.value)}>
              <option value="agentic">에이전트 검색</option>
              <option value="auto">자동 검색</option>
              <option value="off">검색 안 함</option>
            </Select>
          </Field>
          <Field label="대화 히스토리 길이">
            <Input type="number" value={Number(v("chat.history_messages") ?? 12)}
              onChange={(e) => set("chat.history_messages", Number(e.target.value))} />
          </Field>
          <Field label="로그 보관 일수">
            <Input type="number" value={Number(v("log.retention_days") ?? 30)}
              onChange={(e) => set("log.retention_days", Number(e.target.value))} />
          </Field>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="서비스 이름">
            <Input value={String(v("branding.service_name") ?? "")} onChange={(e) => set("branding.service_name", e.target.value)} />
          </Field>
          <Field label="가입 정책">
            <Select value={String(v("signup.mode") ?? "open")} onChange={(e) => set("signup.mode", e.target.value)}>
              <option value="open">공개 — 누구나 가입</option>
              <option value="closed">닫힘 — 관리자만 계정 생성</option>
            </Select>
          </Field>
        </div>
      </Card>

      {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-[13px] text-red-600">{error}</p>}
      {dirty && (
        <div className="sticky bottom-4 flex justify-end">
          <div className="flex items-center gap-2 rounded-xl border border-line bg-white px-3 py-2 shadow-lg">
            <span className="text-[12.5px] text-[#8b949e]">{Object.keys(draft).length}개 변경</span>
            <Button variant="outline" size="sm" onClick={() => setDraft({})}>되돌리기</Button>
            <Button size="sm" onClick={save} busy={busy}>저장</Button>
          </div>
        </div>
      )}
    </div>
  );
}

function Toggle({ label, hint, value, onChange }:
  { label: string; hint: string; value: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex cursor-pointer items-start gap-2.5">
      <input type="checkbox" checked={value} onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 size-4 accent-accent" />
      <span>
        <span className="block text-[13px] font-medium">{label}</span>
        <span className="block text-[12px] text-[#8b949e]">{hint}</span>
      </span>
    </label>
  );
}
