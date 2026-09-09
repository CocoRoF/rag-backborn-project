"use client";
import clsx from "clsx";
import { ChevronRight, FileText, Folder, FolderPlus, Plus, RefreshCw, Search, Trash2, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { NodeDetail } from "@/components/storage/NodeDetail";
import { PipelinePanel } from "@/components/storage/PipelinePanel";
import { RecordsPanel } from "@/components/storage/RecordsPanel";
import { SearchPanel } from "@/components/storage/SearchPanel";
import { Badge, Button, Card, Empty, Field, Input, Select, Spinner, Textarea, bytes, when } from "@/components/ui";
import { ApiError, api, del, get, post } from "@/lib/api";
import type { Repository, StorageNode } from "@/lib/types";

const STATUS: Record<string, { tone: "ok" | "warn" | "bad" | "neutral"; label: string }> = {
  ready: { tone: "ok", label: "색인 완료" },
  processing: { tone: "warn", label: "색인 중" },
  pending: { tone: "neutral", label: "대기" },
  failed: { tone: "bad", label: "실패" },
};

type Tab = "files" | "search" | "pipeline" | "records";
const TABS: { key: Tab; label: string }[] = [
  { key: "files", label: "파일" },
  { key: "search", label: "검색 테스트" },
  { key: "pipeline", label: "파이프라인" },
  { key: "records", label: "레코드" },
];

interface NodesResponse {
  repository: Repository;
  parent: StorageNode | null;
  breadcrumbs: { id: string; name: string; kind: string }[];
  items: StorageNode[];
}

export function StorageView() {
  const [repos, setRepos] = useState<Repository[] | null>(null);
  const [embeddingReady, setEmbeddingReady] = useState(true);
  const [repoId, setRepoId] = useState<string | null>(null);
  const [tree, setTree] = useState<NodesResponse | null>(null);
  const [parentId, setParentId] = useState<string | null>(null);
  const [selected, setSelected] = useState<StorageNode | null>(null);
  const [tab, setTab] = useState<Tab>("files");
  const [newRepo, setNewRepo] = useState(false);
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);

  const loadRepos = useCallback(async () => {
    const d = await get<{ items: Repository[]; embedding_ready: boolean }>("/api/repositories");
    setRepos(d.items);
    setEmbeddingReady(d.embedding_ready);
    setRepoId((cur) => cur ?? d.items[0]?.id ?? null);
  }, []);

  const loadTree = useCallback(async (rid: string, pid: string | null) => {
    const q = pid ? `?parent_id=${pid}` : "";
    setTree(await get<NodesResponse>(`/api/repositories/${rid}/nodes${q}`));
  }, []);

  useEffect(() => { loadRepos().catch(() => setRepos([])); }, [loadRepos]);
  useEffect(() => { if (repoId) { setParentId(null); setSelected(null); } }, [repoId]);
  useEffect(() => { if (repoId) loadTree(repoId, parentId).catch((e) => setError(String(e))); }, [repoId, parentId, loadTree]);

  // Indexing runs in the worker; the row only becomes useful when it flips to ready.
  useEffect(() => {
    if (!tree?.items.some((n) => n.status === "processing" || n.status === "pending")) return;
    const t = setTimeout(() => { if (repoId) loadTree(repoId, parentId).catch(() => {}); }, 3000);
    return () => clearTimeout(t);
  }, [tree, repoId, parentId, loadTree]);

  const upload = async (files: FileList | null) => {
    if (!files?.length || !repoId) return;
    setUploading(true); setError("");
    try {
      for (const file of Array.from(files)) {
        const fd = new FormData();
        fd.append("file", file);
        if (parentId) fd.append("parent_id", parentId);
        await api(`/api/repositories/${repoId}/files`, { method: "POST", body: fd });
      }
      await loadTree(repoId, parentId);
      await loadRepos();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "업로드에 실패했습니다");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const addFolder = async () => {
    const name = prompt("폴더 이름");
    if (!name || !repoId) return;
    try {
      await post(`/api/repositories/${repoId}/folders`, { name, parent_id: parentId });
      await loadTree(repoId, parentId);
    } catch (e) { setError(e instanceof ApiError ? e.message : "폴더를 만들지 못했습니다"); }
  };

  const removeNode = async (n: StorageNode) => {
    if (!repoId) return;
    if (!confirm(n.kind === "folder" ? `'${n.name}' 폴더와 그 안의 모든 파일을 삭제할까요?` : `'${n.name}'을 삭제할까요?`)) return;
    await del(`/api/repositories/${repoId}/nodes/${n.id}`);
    if (selected?.id === n.id) setSelected(null);
    await loadTree(repoId, parentId);
    await loadRepos();
  };

  const reindex = async () => {
    if (!repoId) return;
    const r = await post<{ queued: number }>(`/api/repositories/${repoId}/reindex`);
    alert(`${r.queued}개 파일을 다시 색인합니다.`);
    await loadTree(repoId, parentId);
  };

  const removeRepo = async (r: Repository) => {
    if (!confirm(`'${r.name}' 저장소를 삭제할까요? 파일과 색인이 모두 사라집니다.`)) return;
    await del(`/api/repositories/${r.id}`);
    setRepoId(null);
    await loadRepos();
  };

  if (repos === null) return <Spinner />;
  const repo = repos.find((r) => r.id === repoId) ?? null;

  return (
    <div className="flex h-full">
      <aside className="flex w-64 shrink-0 flex-col border-r border-line bg-white">
        <div className="flex items-center justify-between px-3 py-2.5">
          <span className="text-[12px] font-semibold uppercase tracking-wide text-[#8b949e]">저장소</span>
          <Button size="sm" variant="ghost" onClick={() => setNewRepo(true)}><Plus className="size-3.5" />새 저장소</Button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
          {repos.map((r) => (
            <button key={r.id} onClick={() => setRepoId(r.id)}
              className={clsx("group mb-0.5 flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left transition-colors",
                repoId === r.id ? "bg-accent-soft" : "hover:bg-muted")}>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] font-medium">{r.name}</span>
                <span className="block truncate text-[11px] text-[#8b949e]">
                  {r.file_count}개 파일 · {r.chunk_count}조각 · {bytes(r.bytes_total)}
                </span>
              </span>
              {r.can_write && (
                <span onClick={(e) => { e.stopPropagation(); removeRepo(r); }}
                  className="hidden rounded p-1 text-[#c1c7cd] hover:text-red-500 group-hover:block">
                  <Trash2 className="size-3.5" />
                </span>
              )}
            </button>
          ))}
          {!repos.length && <p className="px-2 py-6 text-center text-[12.5px] text-[#8b949e]">저장소가 없습니다.</p>}
        </div>
        {!embeddingReady && (
          <p className="m-2 rounded-lg bg-amber-50 px-2.5 py-2 text-[11.5px] leading-relaxed text-amber-700">
            임베딩 키가 없어 <b>키워드 검색만</b> 동작합니다. [관리 → 임베딩]에서 설정하면 기존 문서도 다시 색인할 수 있습니다.
          </p>
        )}
      </aside>

      <section className="flex min-w-0 flex-1 flex-col">
        {!repo ? (
          <Empty title="저장소를 만들어 문서를 올리세요"
            hint="저장소는 폴더 트리를 가지며, 올린 문서는 자동으로 섹션 계층과 임베딩으로 색인됩니다."
            action={<Button className="mt-2" onClick={() => setNewRepo(true)}><Plus className="size-4" />새 저장소</Button>} />
        ) : (
          <>
            <header className="flex h-12 shrink-0 items-center gap-2 border-b border-line bg-white px-4">
              <span className="text-[14px] font-medium">{repo.name}</span>
              <div className="ml-3 flex gap-1">
                {TABS.map((t) => (
                  <button key={t.key} onClick={() => setTab(t.key)}
                    className={clsx("rounded-lg px-2.5 py-1 text-[12.5px] font-medium transition-colors",
                      tab === t.key ? "bg-accent-soft text-accent" : "text-[#8b949e] hover:bg-muted")}>
                    {t.label}
                  </button>
                ))}
              </div>
              {!repo.can_write && <Badge tone="neutral">읽기 전용 · 공유 저장소</Badge>}
              <div className={clsx("ml-auto flex gap-1.5", (tab !== "files" || !repo.can_write) && "hidden")}>
                <Button size="sm" variant="outline" onClick={reindex}><RefreshCw className="size-3.5" />전체 재색인</Button>
                <Button size="sm" variant="outline" onClick={addFolder}><FolderPlus className="size-3.5" />폴더</Button>
                <Button size="sm" busy={uploading} onClick={() => fileRef.current?.click()}>
                  <Upload className="size-3.5" />업로드
                </Button>
                <input ref={fileRef} type="file" multiple hidden onChange={(e) => upload(e.target.files)}
                  accept=".pdf,.docx,.pptx,.xlsx,.xlsm,.md,.markdown,.txt,.csv,.json,.html,.htm,.log,.yaml,.yml" />
              </div>
            </header>

            {tab === "search" ? (
              <SearchPanel repoId={repo.id} />
            ) : tab === "pipeline" ? (
              <PipelinePanel repoId={repo.id} canWrite={repo.can_write}
                files={(tree?.items ?? []).filter((n) => n.kind === "file")} />
            ) : tab === "records" ? (
              <RecordsPanel repoId={repo.id} />
            ) : (
              <div className="flex min-h-0 flex-1">
                <div className="min-w-0 flex-1 overflow-y-auto p-4">
                  <nav className="mb-3 flex flex-wrap items-center gap-1 text-[12.5px] text-[#8b949e]">
                    <button onClick={() => setParentId(null)} className="hover:text-accent">{repo.name}</button>
                    {(tree?.breadcrumbs ?? []).map((b) => (
                      <span key={b.id} className="flex items-center gap-1">
                        <ChevronRight className="size-3" />
                        <button onClick={() => setParentId(b.id)} className="hover:text-accent">{b.name}</button>
                      </span>
                    ))}
                  </nav>
                  {error && <p className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-[13px] text-red-600">{error}</p>}
                  <Card className="divide-y divide-line">
                    {(tree?.items ?? []).map((n) => (
                      <div key={n.id} className="group flex items-center gap-2.5 px-3 py-2.5 hover:bg-muted">
                        <button className="flex min-w-0 flex-1 items-center gap-2.5 text-left"
                          onClick={() => (n.kind === "folder" ? (setParentId(n.id), setSelected(null)) : setSelected(n))}>
                          {n.kind === "folder"
                            ? <Folder className="size-4 shrink-0 text-[#8b949e]" />
                            : <FileText className="size-4 shrink-0 text-accent" />}
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-[13.5px] font-medium">{n.name}</span>
                            {n.kind === "file" && (
                              <span className="block truncate text-[11.5px] text-[#8b949e]">
                                {bytes(n.size_bytes)} · {n.chunk_count}조각 · 섹션 {n.section_count}개 · {when(n.updated_at)}
                              </span>
                            )}
                          </span>
                        </button>
                        {n.kind === "file" && <Badge tone={STATUS[n.status]?.tone}>{STATUS[n.status]?.label ?? n.status}</Badge>}
                        {repo.can_write && (
                          <button onClick={() => removeNode(n)}
                            className="hidden rounded p-1 text-[#c1c7cd] hover:text-red-500 group-hover:block">
                            <Trash2 className="size-3.5" />
                          </button>
                        )}
                      </div>
                    ))}
                    {!(tree?.items ?? []).length && (
                      <Empty title="비어 있습니다" hint="파일을 업로드하면 자동으로 색인됩니다." />
                    )}
                  </Card>
                </div>
                {selected && <NodeDetail repoId={repo.id} node={selected} onClose={() => setSelected(null)}
                  onChanged={() => loadTree(repo.id, parentId)} />}
              </div>
            )}
          </>
        )}
      </section>

      {newRepo && <NewRepoDialog onClose={() => setNewRepo(false)}
        onCreated={async (r) => { setNewRepo(false); await loadRepos(); setRepoId(r.id); }} />}
    </div>
  );
}

function NewRepoDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (r: Repository) => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [visibility, setVisibility] = useState("private");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const create = async () => {
    setBusy(true); setError("");
    try { onCreated(await post<Repository>("/api/repositories", { name, description, visibility })); }
    catch (e) { setError(e instanceof ApiError ? e.message : "만들지 못했습니다"); }
    finally { setBusy(false); }
  };
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/30 p-4" onClick={onClose}>
      <Card className="w-full max-w-md p-5" >
        <div onClick={(e) => e.stopPropagation()} className="space-y-4">
          <h2 className="text-[15px] font-semibold">새 저장소</h2>
          <Field label="이름"><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="예: 사내 규정" /></Field>
          <Field label="설명 (선택)"><Textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
          <Field label="공개 범위" hint="공유로 두면 다른 사용자도 읽고 에이전트에 연결할 수 있습니다.">
            <Select value={visibility} onChange={(e) => setVisibility(e.target.value)}>
              <option value="private">비공개 — 나만</option>
              <option value="shared">공유 — 로그인한 모든 사용자</option>
            </Select>
          </Field>
          {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-[13px] text-red-600">{error}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={onClose}>취소</Button>
            <Button onClick={create} busy={busy} disabled={!name.trim()}>만들기</Button>
          </div>
        </div>
      </Card>
    </div>
  );
}
