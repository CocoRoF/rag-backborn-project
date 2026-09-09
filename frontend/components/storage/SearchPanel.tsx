"use client";
import { Search } from "lucide-react";
import { useState } from "react";

import { Badge, Button, Card, Empty, Input, Spinner } from "@/components/ui";
import { ApiError, get } from "@/lib/api";
import type { Citation } from "@/lib/types";

interface Result {
  query: string; legs: Record<string, number>; candidates: number; latency_ms: number;
  reranked: boolean; embedded: boolean; items: Citation[];
}

const LEG_LABEL: Record<string, string> = {
  vector: "벡터", section: "섹션", lexical: "형태소", trigram: "부분일치", path: "경로",
};

/** The retrieval playground. Which legs fired for a query is the single most useful thing to
 *  see when tuning weights, so it is shown rather than hidden behind a log page. */
export function SearchPanel({ repoId }: { repoId: string }) {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const run = async () => {
    if (!q.trim()) return;
    setBusy(true); setError("");
    try { setRes(await get<Result>(`/api/repositories/${repoId}/search?q=${encodeURIComponent(q)}&k=10`)); }
    catch (e) { setError(e instanceof ApiError ? e.message : "검색에 실패했습니다"); }
    finally { setBusy(false); }
  };

  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-5">
      <div className="mx-auto max-w-3xl space-y-4">
        <div className="flex gap-2">
          <Input value={q} placeholder="이 저장소에 질문해 보세요 — 어떤 근거가 검색되는지 그대로 보여줍니다"
            onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && run()} />
          <Button onClick={run} busy={busy}><Search className="size-4" />검색</Button>
        </div>
        {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-[13px] text-red-600">{error}</p>}
        {busy && !res && <Spinner label="검색 중…" />}
        {res && (
          <>
            <Card className="flex flex-wrap items-center gap-2 px-3.5 py-3">
              <span className="text-[12.5px] text-[#57606a]">후보 {res.candidates}건 → {res.items.length}건</span>
              <span className="text-[12.5px] text-[#c1c7cd]">·</span>
              <span className="text-[12.5px] text-[#57606a]">{res.latency_ms}ms</span>
              <div className="ml-auto flex flex-wrap gap-1">
                {Object.entries(res.legs).map(([leg, n]) => (
                  <Badge key={leg} tone={n > 0 ? "accent" : "neutral"}>{LEG_LABEL[leg] ?? leg} {n}</Badge>
                ))}
                {res.reranked && <Badge tone="ok">재순위</Badge>}
                {!res.embedded && <Badge tone="warn">키워드만</Badge>}
              </div>
            </Card>
            {!res.items.length && <Empty title="검색 결과가 없습니다"
              hint="다른 표현으로 다시 검색하거나, [관리 → RAG 설정]에서 각 검색 축의 가중치를 조정해 보세요." />}
            {res.items.map((c, i) => (
              <Card key={i} className="px-4 py-3">
                <p className="mb-1.5 flex items-center gap-2 text-[12.5px]">
                  <span className="grid size-5 place-items-center rounded bg-accent-soft text-[11px] font-semibold text-accent">
                    {i + 1}
                  </span>
                  <span className="min-w-0 flex-1 truncate font-medium">{c.path}</span>
                  {c.heading && <span className="shrink-0 text-[#8b949e]">{c.heading}</span>}
                  <span className="shrink-0 text-[11px] text-[#c1c7cd]">{c.score}</span>
                </p>
                <p className="whitespace-pre-wrap text-[12.5px] leading-relaxed text-[#4b5563]">{c.text}</p>
                <div className="mt-2 flex gap-1">{c.legs.map((l) => <Badge key={l}>{LEG_LABEL[l] ?? l}</Badge>)}</div>
              </Card>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
