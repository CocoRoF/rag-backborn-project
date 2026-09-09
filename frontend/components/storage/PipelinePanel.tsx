"use client";
import clsx from "clsx";
import { ChevronDown, Play, Plus, Settings2, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { ConfigForm } from "@/components/storage/ConfigForm";
import { Badge, Button, Card, Empty, Input, Spinner, when } from "@/components/ui";
import { ApiError, del, get, patch, post } from "@/lib/api";
import { KIND_ORDER, KIND_TONE, type Binding, type Collection, type PluginRun, type PluginSpec, type Scorecard } from "@/lib/plugins";
import type { StorageNode } from "@/lib/types";

const STATUS_TONE: Record<string, "ok" | "warn" | "bad" | "accent" | "neutral"> = {
  done: "ok", running: "accent", queued: "neutral", failed: "bad",
};

/** [파이프라인] — 이 저장소에 붙은 플러그인 단계와 실행 이력.
 *
 *  단계는 다섯 종류(수집 → 구조화 → 평가 → 매칭 → 산출물)이고, 순서는 실행 순서가 아니라
 *  읽는 순서다. 실행은 단계별 수동 — 자동 스케줄은 백본이 정할 문제가 아니다. */
export function PipelinePanel({ repoId, files }: { repoId: string; files: StorageNode[] }) {
  const [specs, setSpecs] = useState<PluginSpec[] | null>(null);
  const [bindings, setBindings] = useState<Binding[]>([]);
  const [runs, setRuns] = useState<PluginRun[]>([]);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [scorecards, setScorecards] = useState<Scorecard[]>([]);
  const [editing, setEditing] = useState<{ binding: Binding | null; spec: PluginSpec } | null>(null);
  const [picking, setPicking] = useState(false);
  const [error, setError] = useState("");

  const loadPipeline = useCallback(async () => {
    const d = await get<{ items: Binding[]; runs: PluginRun[] }>(`/api/repositories/${repoId}/pipeline`);
    setBindings(d.items);
    setRuns(d.runs);
    return d;
  }, [repoId]);

  useEffect(() => {
    get<{ items: PluginSpec[] }>("/api/plugins").then((d) => setSpecs(d.items)).catch(() => setSpecs([]));
    get<{ items: Collection[]; scorecards: Scorecard[] }>(`/api/repositories/${repoId}/collections`)
      .then((d) => { setCollections(d.items); setScorecards(d.scorecards); }).catch(() => {});
    loadPipeline().catch(() => {});
  }, [repoId, loadPipeline]);

  // A run is a background job; the row only becomes useful when it lands.
  useEffect(() => {
    if (!runs.some((r) => r.status === "queued" || r.status === "running")) return;
    const t = setTimeout(() => { loadPipeline().catch(() => {}); }, 3000);
    return () => clearTimeout(t);
  }, [runs, loadPipeline]);

  const runNow = async (b: Binding) => {
    setError("");
    try { await post(`/api/repositories/${repoId}/pipeline/${b.id}/run`); await loadPipeline(); }
    catch (e) { setError(e instanceof ApiError ? e.message : "실행에 실패했습니다"); }
  };

  const remove = async (b: Binding) => {
    if (!confirm(`'${b.name}' 단계를 삭제할까요? 실행 이력도 함께 사라집니다.`)) return;
    await del(`/api/repositories/${repoId}/pipeline/${b.id}`);
    await loadPipeline();
  };

  if (specs === null) return <Spinner />;

  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-5">
      <div className="mx-auto max-w-3xl space-y-4">
        <Card className="flex items-start gap-3 px-4 py-3">
          <div className="min-w-0 flex-1">
            <p className="text-[13px] font-semibold">파이프라인</p>
            <p className="mt-0.5 text-[12.5px] leading-relaxed text-[#8b949e]">
              RAG 저장소 위에 붙이는 업무 단계입니다. <b>수집 → 구조화 → 평가 → 매칭 → 산출물</b> 순서로
              쌓으면 문서가 레코드가 되고, 점수와 매칭 후보를 거쳐 Evidence Card 로 다시 저장소에 들어옵니다.
            </p>
          </div>
          <Button size="sm" onClick={() => setPicking(true)}><Plus className="size-3.5" />단계 추가</Button>
        </Card>

        {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-[13px] text-red-600">{error}</p>}

        {!bindings.length ? (
          <Empty title="아직 단계가 없습니다"
            hint="[단계 추가]에서 플러그인을 골라 이 저장소에 붙이세요. 각 플러그인은 필요한 설정만 물어봅니다." />
        ) : (
          <div className="space-y-2">
            {bindings.map((b) => {
              const last = runs.find((r) => r.binding_id === b.id);
              const spec = specs.find((s) => s.id === b.plugin_id);
              const busy = last?.status === "queued" || last?.status === "running";
              return (
                <Card key={b.id} className="px-4 py-3">
                  <div className="flex items-center gap-2.5">
                    <Badge tone={KIND_TONE[b.kind]}>{b.kind_label}</Badge>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13.5px] font-medium">{b.name}</span>
                      <span className="block truncate text-[11.5px] text-[#8b949e]">
                        {b.plugin_name}
                        {b.last_run_at && ` · 마지막 실행 ${when(b.last_run_at)}`}
                      </span>
                    </span>
                    {last && <Badge tone={STATUS_TONE[last.status] ?? "neutral"}>
                      {busy ? <span className="pulse-dot">실행 중</span> : last.status}
                    </Badge>}
                    <Button size="sm" variant="outline" busy={busy} onClick={() => runNow(b)}>
                      <Play className="size-3.5" />실행
                    </Button>
                    <button onClick={() => spec && setEditing({ binding: b, spec })}
                      className="rounded p-1.5 text-[#8b949e] hover:bg-muted"><Settings2 className="size-3.5" /></button>
                    <button onClick={() => remove(b)}
                      className="rounded p-1.5 text-[#c1c7cd] hover:text-red-500"><Trash2 className="size-3.5" /></button>
                  </div>
                  {last && (last.summary || last.error) && (
                    <p className={clsx("mt-2 rounded-lg px-2.5 py-1.5 text-[12px]",
                      last.error ? "bg-red-50 text-red-600" : "bg-muted text-[#57606a]")}>
                      {last.error || last.summary}
                    </p>
                  )}
                </Card>
              );
            })}
          </div>
        )}

        <RunHistory runs={runs} />
      </div>

      {picking && (
        <PluginPicker specs={specs} onClose={() => setPicking(false)}
          onPick={(spec) => { setPicking(false); setEditing({ binding: null, spec }); }} />
      )}
      {editing && (
        <BindingDialog repoId={repoId} spec={editing.spec} binding={editing.binding}
          collections={collections} scorecards={scorecards} files={files}
          onClose={() => setEditing(null)}
          onSaved={async () => { setEditing(null); await loadPipeline(); }} />
      )}
    </div>
  );
}

function PluginPicker({ specs, onClose, onPick }:
  { specs: PluginSpec[]; onClose: () => void; onPick: (s: PluginSpec) => void }) {
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/30 p-4" onClick={onClose}>
      <Card className="flex max-h-[86dvh] w-full max-w-2xl flex-col overflow-hidden">
        <div onClick={(e) => e.stopPropagation()} className="flex min-h-0 flex-col">
          <div className="flex shrink-0 items-center justify-between border-b border-line px-5 py-3.5">
            <h2 className="text-[15px] font-semibold">플러그인 선택</h2>
            <button onClick={onClose} className="rounded p-1 text-[#8b949e] hover:bg-muted"><X className="size-4" /></button>
          </div>
          <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
            {KIND_ORDER.map((kind) => {
              const group = specs.filter((s) => s.kind === kind);
              if (!group.length) return null;
              return (
                <div key={kind}>
                  <p className="mb-1.5 flex items-center gap-2 text-[12px] font-semibold uppercase tracking-wide text-[#8b949e]">
                    <Badge tone={KIND_TONE[kind]}>{group[0].kind_label}</Badge>
                  </p>
                  <div className="space-y-1.5">
                    {group.map((s) => (
                      <button key={s.id} onClick={() => onPick(s)}
                        className="block w-full rounded-lg border border-line px-3 py-2.5 text-left transition-colors hover:bg-muted">
                        <span className="flex items-center gap-2">
                          <span className="text-[13.5px] font-medium">{s.name}</span>
                          {s.needs_llm && <Badge tone="warn">모델 필요</Badge>}
                          {s.needs_embedding && <Badge>임베딩 사용</Badge>}
                        </span>
                        <span className="mt-0.5 block text-[12.5px] leading-relaxed text-[#8b949e]">{s.description}</span>
                        {s.produces && <span className="mt-1 block text-[11.5px] text-[#c1c7cd]">→ {s.produces}</span>}
                      </button>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </Card>
    </div>
  );
}

function BindingDialog({ repoId, spec, binding, collections, scorecards, files, onClose, onSaved }: {
  repoId: string; spec: PluginSpec; binding: Binding | null;
  collections: Collection[]; scorecards: Scorecard[]; files: StorageNode[];
  onClose: () => void; onSaved: () => void;
}) {
  const [name, setName] = useState(binding?.name ?? spec.name);
  const [config, setConfig] = useState<Record<string, unknown>>(binding?.config ?? { ...spec.defaults });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const save = async () => {
    setBusy(true); setError("");
    try {
      if (binding) await patch(`/api/repositories/${repoId}/pipeline/${binding.id}`, { name, config, enabled: true });
      else await post(`/api/repositories/${repoId}/pipeline`, { plugin_id: spec.id, name, config });
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "저장에 실패했습니다");
    } finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/30 p-4" onClick={onClose}>
      <Card className="flex max-h-[88dvh] w-full max-w-lg flex-col overflow-hidden">
        <div onClick={(e) => e.stopPropagation()} className="flex min-h-0 flex-col">
          <div className="shrink-0 border-b border-line px-5 py-3.5">
            <div className="flex items-center gap-2">
              <Badge tone={KIND_TONE[spec.kind]}>{spec.kind_label}</Badge>
              <h2 className="text-[15px] font-semibold">{spec.name}</h2>
              <button onClick={onClose} className="ml-auto rounded p-1 text-[#8b949e] hover:bg-muted">
                <X className="size-4" />
              </button>
            </div>
            <p className="mt-1 text-[12.5px] leading-relaxed text-[#8b949e]">{spec.description}</p>
          </div>
          <div className="min-h-0 flex-1 space-y-3.5 overflow-y-auto px-5 py-4">
            <label className="block space-y-1.5">
              <span className="text-[13px] font-medium text-[#374151]">단계 이름</span>
              <Input value={name} onChange={(e) => setName(e.target.value)} />
            </label>
            <ConfigForm fields={spec.fields} value={config} onChange={setConfig}
              collections={collections} scorecards={scorecards} files={files} />
            {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-[13px] text-red-600">{error}</p>}
          </div>
          <div className="flex shrink-0 justify-end gap-2 border-t border-line px-5 py-3">
            <Button variant="outline" onClick={onClose}>취소</Button>
            <Button onClick={save} busy={busy}>저장</Button>
          </div>
        </div>
      </Card>
    </div>
  );
}

function RunHistory({ runs }: { runs: PluginRun[] }) {
  const [openId, setOpenId] = useState<string | null>(null);
  if (!runs.length) return null;
  return (
    <Card className="divide-y divide-line">
      <p className="px-4 py-2.5 text-[13px] font-semibold">실행 이력</p>
      {runs.slice(0, 20).map((r) => (
        <div key={r.id}>
          <button onClick={() => setOpenId(openId === r.id ? null : r.id)}
            className="flex w-full items-center gap-2.5 px-4 py-2 text-left hover:bg-muted">
            <span className="w-16 shrink-0 text-[11.5px] text-[#c1c7cd]">{when(r.created_at)}</span>
            <Badge tone={STATUS_TONE[r.status] ?? "neutral"}>{r.status}</Badge>
            <span className="w-32 shrink-0 truncate text-[12px] text-[#8b949e]">{r.plugin_id}</span>
            <span className="min-w-0 flex-1 truncate text-[12.5px]">{r.error || r.summary}</span>
            <ChevronDown className={clsx("size-3.5 shrink-0 text-[#c1c7cd] transition-transform",
              openId === r.id && "rotate-180")} />
          </button>
          {openId === r.id && (
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap bg-ink px-4 py-3 text-[11.5px] leading-relaxed text-[#e6edf3]">
              {r.log || "(로그 없음)"}
            </pre>
          )}
        </div>
      ))}
    </Card>
  );
}
