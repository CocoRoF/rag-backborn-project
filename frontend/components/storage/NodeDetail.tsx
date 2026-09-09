"use client";
import clsx from "clsx";
import { RefreshCw, X } from "lucide-react";
import { useEffect, useState } from "react";

import { Badge, Button, Spinner, bytes } from "@/components/ui";
import { get, post } from "@/lib/api";
import type { StorageNode } from "@/lib/types";

interface Section {
  id: string; level: number; title: string; heading_path: string; summary: string;
  page: number | null; tokens: number; chunks: number; embedded: boolean;
}
interface Chunk { ordinal: number; heading: string; page: number | null; tokens: number; text: string; embedded: boolean }

export function NodeDetail({ repoId, node, onClose, onChanged }:
  { repoId: string; node: StorageNode; onClose: () => void; onChanged: () => void }) {
  const [tab, setTab] = useState<"outline" | "chunks">("outline");
  const [sections, setSections] = useState<Section[] | null>(null);
  const [chunks, setChunks] = useState<Chunk[] | null>(null);

  useEffect(() => {
    setSections(null); setChunks(null);
    get<{ sections: Section[] }>(`/api/repositories/${repoId}/nodes/${node.id}/outline`)
      .then((d) => setSections(d.sections)).catch(() => setSections([]));
  }, [repoId, node.id]);

  useEffect(() => {
    if (tab !== "chunks" || chunks) return;
    get<{ chunks: Chunk[] }>(`/api/repositories/${repoId}/nodes/${node.id}/content?limit=60`)
      .then((d) => setChunks(d.chunks)).catch(() => setChunks([]));
  }, [tab, chunks, repoId, node.id]);

  const reindex = async () => {
    await post(`/api/repositories/${repoId}/nodes/${node.id}/reindex`);
    onChanged();
  };

  return (
    <aside className="flex w-96 shrink-0 flex-col border-l border-line bg-white">
      <div className="flex shrink-0 items-start gap-2 border-b border-line px-4 py-3">
        <div className="min-w-0 flex-1">
          <p className="truncate text-[13.5px] font-semibold">{node.name}</p>
          <p className="truncate text-[11.5px] text-[#8b949e]">{node.path}</p>
          <div className="mt-1.5 flex flex-wrap gap-1">
            <Badge>{bytes(node.size_bytes)}</Badge>
            <Badge>{node.chunk_count}조각</Badge>
            <Badge>섹션 {node.section_count}</Badge>
            {node.embedding_model ? <Badge tone="ok">{node.embedding_model}</Badge> : <Badge tone="warn">임베딩 없음</Badge>}
          </div>
        </div>
        <button onClick={onClose} className="rounded p-1 text-[#8b949e] hover:bg-muted"><X className="size-4" /></button>
      </div>

      {node.error && <p className="mx-4 mt-3 rounded-lg bg-red-50 px-3 py-2 text-[12.5px] text-red-600">{node.error}</p>}

      <div className="flex shrink-0 items-center gap-1 px-4 py-2">
        {(["outline", "chunks"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={clsx("rounded-lg px-2.5 py-1 text-[12.5px] font-medium transition-colors",
              tab === t ? "bg-accent-soft text-accent" : "text-[#8b949e] hover:bg-muted")}>
            {t === "outline" ? "섹션 계층" : "조각"}
          </button>
        ))}
        <Button size="sm" variant="ghost" className="ml-auto" onClick={reindex}>
          <RefreshCw className="size-3.5" />재색인
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4">
        {tab === "outline" ? (
          sections === null ? <Spinner /> : !sections.length ? (
            <p className="py-6 text-center text-[12.5px] text-[#8b949e]">섹션 정보가 없습니다.</p>
          ) : (
            <div className="space-y-2">
              {sections.map((s) => (
                <div key={s.id} style={{ marginLeft: (s.level - 1) * 12 }}
                  className="rounded-lg border border-line px-2.5 py-2">
                  <p className="flex items-center gap-1.5 text-[12.5px] font-medium">
                    {s.title}
                    {s.page && <span className="text-[11px] text-[#c1c7cd]">p.{s.page}</span>}
                    {!s.embedded && <Badge tone="warn">벡터 없음</Badge>}
                  </p>
                  <p className="mt-1 line-clamp-3 text-[11.5px] leading-relaxed text-[#8b949e]">{s.summary}</p>
                  <p className="mt-1 text-[11px] text-[#c1c7cd]">{s.chunks}조각 · {s.tokens}토큰</p>
                </div>
              ))}
            </div>
          )
        ) : chunks === null ? <Spinner /> : (
          <div className="space-y-2">
            {chunks.map((c) => (
              <div key={c.ordinal} className="rounded-lg border border-line px-2.5 py-2">
                <p className="mb-1 flex items-center gap-1.5 text-[11px] text-[#8b949e]">
                  <span className="font-semibold text-accent">#{c.ordinal}</span>
                  <span className="min-w-0 flex-1 truncate">{c.heading}</span>
                  <span>{c.tokens}t</span>
                </p>
                <p className="whitespace-pre-wrap text-[12px] leading-relaxed text-[#4b5563]">{c.text}</p>
              </div>
            ))}
          </div>
        )}
      </div>
    </aside>
  );
}
