"use client";
import clsx from "clsx";
import { Check, Link2, ThumbsDown, ThumbsUp, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Badge, Button, Card, Empty, Input, Spinner, when } from "@/components/ui";
import { ApiError, get, post } from "@/lib/api";
import { TIER_LABEL, type Collection, type DataRecord, type Link as MatchLink, type Scorecard } from "@/lib/plugins";

const TIER_TONE: Record<string, "ok" | "accent" | "neutral"> = { core: "ok", short: "accent", long: "neutral" };
const LINK_TONE: Record<string, "ok" | "bad" | "neutral"> = { approved: "ok", rejected: "bad", candidate: "neutral" };

/** [레코드] — 파이프라인이 만든 개체·점수·매칭, 그리고 전문가 검증(Human-in-the-Loop).
 *
 *  AI 가 만든 것은 전부 `candidate` 로 들어오고, 사람이 승인/반려해야 상태가 바뀐다.
 *  판단의 흔적은 지워지지 않고 검토 이력으로 남는다. */
export function RecordsPanel({ repoId }: { repoId: string }) {
  const [tab, setTab] = useState<"records" | "matches">("records");
  const [collections, setCollections] = useState<Collection[] | null>(null);
  const [scorecards, setScorecards] = useState<Scorecard[]>([]);
  const [collection, setCollection] = useState("");
  const [q, setQ] = useState("");
  const [records, setRecords] = useState<DataRecord[] | null>(null);
  const [links, setLinks] = useState<MatchLink[] | null>(null);
  const [selected, setSelected] = useState<DataRecord | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    get<{ items: Collection[]; scorecards: Scorecard[] }>(`/api/repositories/${repoId}/collections`)
      .then((d) => {
        setCollections(d.items);
        setScorecards(d.scorecards);
        setCollection((c) => c || d.items[0]?.key || "");
      }).catch(() => setCollections([]));
  }, [repoId]);

  const loadRecords = useCallback(async () => {
    const qs = new URLSearchParams({ collection, ...(q ? { q } : {}) });
    const d = await get<{ items: DataRecord[] }>(`/api/repositories/${repoId}/records?${qs}`);
    setRecords(d.items);
  }, [repoId, collection, q]);

  const loadLinks = useCallback(async () => {
    const d = await get<{ items: MatchLink[] }>(`/api/repositories/${repoId}/links`);
    setLinks(d.items);
  }, [repoId]);

  useEffect(() => { if (collection) loadRecords().catch(() => setRecords([])); }, [collection, loadRecords]);
  useEffect(() => { if (tab === "matches") loadLinks().catch(() => setLinks([])); }, [tab, loadLinks]);

  const review = async (targetType: string, targetId: string, verdict: "approved" | "rejected") => {
    setError("");
    try {
      await post(`/api/repositories/${repoId}/review`, { target_type: targetType, target_id: targetId, verdict });
      if (tab === "matches") await loadLinks(); else await loadRecords();
      if (selected) setSelected(await reload(repoId, selected.id));
    } catch (e) { setError(e instanceof ApiError ? e.message : "검토 저장에 실패했습니다"); }
  };

  if (collections === null) return <Spinner />;

  return (
    <div className="flex min-h-0 flex-1">
      <div className="min-w-0 flex-1 overflow-y-auto p-5">
        <div className="mx-auto max-w-3xl space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            {(["records", "matches"] as const).map((t) => (
              <button key={t} onClick={() => setTab(t)}
                className={clsx("rounded-lg px-2.5 py-1 text-[12.5px] font-medium transition-colors",
                  tab === t ? "bg-accent-soft text-accent" : "text-[#8b949e] hover:bg-muted")}>
                {t === "records" ? "레코드" : "매칭 후보"}
              </button>
            ))}
            {tab === "records" && (
              <>
                <div className="ml-2 flex gap-1">
                  {collections.map((c) => (
                    <button key={c.key} onClick={() => setCollection(c.key)}
                      className={clsx("rounded-lg px-2.5 py-1 text-[12.5px] transition-colors",
                        collection === c.key ? "bg-ink text-white" : "text-[#57606a] hover:bg-muted")}>
                      {c.name} <span className="opacity-60">{c.record_count}</span>
                    </button>
                  ))}
                </div>
                <Input className="ml-auto max-w-52" placeholder="레코드 검색" value={q}
                  onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && loadRecords()} />
              </>
            )}
          </div>

          {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-[13px] text-red-600">{error}</p>}

          {tab === "records" ? (
            records === null ? <Spinner /> : !records.length ? (
              <Empty title="레코드가 없습니다"
                hint="[파이프라인]에서 수집(SOURCE) 플러그인을 실행하면 문서가 레코드로 들어옵니다." />
            ) : (
              <div className="space-y-1.5">
                {records.map((r) => (
                  <Card key={r.id} className={clsx("cursor-pointer px-3.5 py-2.5 transition-colors hover:bg-muted",
                    selected?.id === r.id && "border-accent")}
                    onClick={async () => setSelected(await reload(repoId, r.id))}>
                    <div className="flex items-center gap-2.5">
                      {r.score ? (
                        <span className="w-11 shrink-0 text-center">
                          <span className="block text-[15px] font-semibold tabular-nums">{r.score.total}</span>
                          <span className="block text-[10px] text-[#c1c7cd]">점</span>
                        </span>
                      ) : <span className="w-11 shrink-0 text-center text-[11px] text-[#c1c7cd]">미평가</span>}
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[13.5px] font-medium">{r.title}</span>
                        <span className="block truncate text-[11.5px] text-[#8b949e]">
                          {r.summary || r.external_id}
                        </span>
                      </span>
                      {r.score && <Badge tone={TIER_TONE[r.score.tier]}>{TIER_LABEL[r.score.tier]}</Badge>}
                      {!r.embedded && <Badge tone="warn">벡터 없음</Badge>}
                    </div>
                    {!!r.labels.length && (
                      <div className="mt-1.5 flex flex-wrap gap-1">
                        {r.labels.map((l) => <Badge key={l}>{l}</Badge>)}
                      </div>
                    )}
                  </Card>
                ))}
              </div>
            )
          ) : links === null ? <Spinner /> : !links.length ? (
            <Empty title="매칭 후보가 없습니다"
              hint="[파이프라인]에서 매칭(MATCH) 플러그인을 실행하면 후보가 쌓입니다." />
          ) : (
            <div className="space-y-1.5">
              {links.map((l) => (
                <Card key={l.id} className="px-3.5 py-2.5">
                  <div className="flex items-center gap-2.5">
                    <Link2 className="size-4 shrink-0 text-[#c1c7cd]" />
                    <span className="min-w-0 flex-1 text-[13px]">
                      <span className="font-medium">{l.from.title}</span>
                      <span className="mx-1.5 text-[#c1c7cd]">→</span>
                      <span className="font-medium">{l.to.title}</span>
                    </span>
                    <span className="shrink-0 text-[11.5px] text-[#8b949e]">
                      유사도 {l.score}{l.rerank_score !== null && ` · 재평가 ${l.rerank_score}`}
                    </span>
                    <Badge tone={LINK_TONE[l.status] ?? "neutral"}>{l.status}</Badge>
                    {l.status === "candidate" && (
                      <>
                        <button onClick={() => review("link", l.id, "approved")} title="승인"
                          className="rounded p-1.5 text-[#8b949e] hover:bg-emerald-50 hover:text-emerald-600">
                          <ThumbsUp className="size-3.5" />
                        </button>
                        <button onClick={() => review("link", l.id, "rejected")} title="반려"
                          className="rounded p-1.5 text-[#8b949e] hover:bg-red-50 hover:text-red-600">
                          <ThumbsDown className="size-3.5" />
                        </button>
                      </>
                    )}
                  </div>
                  {l.rationale && <p className="mt-1.5 text-[12px] text-[#57606a]">{l.rationale}</p>}
                </Card>
              ))}
            </div>
          )}

          {tab === "records" && !!scorecards.length && <ScorecardCard cards={scorecards} />}
        </div>
      </div>

      {selected && <RecordDetail repoId={repoId} record={selected} onClose={() => setSelected(null)}
        onReview={review} />}
    </div>
  );
}

async function reload(repoId: string, id: string): Promise<DataRecord> {
  const d = await get<{ record: DataRecord; links: MatchLink[]; reviews: unknown[] }>(
    `/api/repositories/${repoId}/records/${id}`);
  return { ...d.record, ...({ _links: d.links, _reviews: d.reviews } as object) } as DataRecord;
}

function ScorecardCard({ cards }: { cards: Scorecard[] }) {
  return (
    <Card className="px-4 py-3">
      <p className="text-[13px] font-semibold">스코어카드</p>
      <p className="mt-0.5 text-[12px] text-[#8b949e]">
        평가 차원과 가중치입니다. 전문가 검증(Delphi/AHP) 결과에 따라 조정하면 다음 평가부터 반영됩니다.
      </p>
      {cards.map((s) => (
        <div key={s.id} className="mt-2.5">
          <p className="flex items-center gap-1.5 text-[12.5px] font-medium">
            {s.name}{s.is_default && <Badge tone="accent">기본</Badge>}
          </p>
          <div className="mt-1 space-y-0.5">
            {s.dimensions.map((d) => (
              <div key={d.key} className="flex items-center gap-2 text-[12px]">
                <span className="w-44 shrink-0 truncate text-[#57606a]">{d.label}</span>
                <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                  <span className="block h-full rounded-full bg-accent" style={{ width: `${d.weight * 100}%` }} />
                </span>
                <span className="w-10 shrink-0 text-right tabular-nums text-[#8b949e]">
                  {(d.weight * 100).toFixed(0)}%
                </span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </Card>
  );
}

function RecordDetail({ repoId, record, onClose, onReview }: {
  repoId: string; record: DataRecord & { _links?: MatchLink[] };
  onClose: () => void; onReview: (t: string, id: string, v: "approved" | "rejected") => void;
}) {
  const score = record.score;
  const dims = score ? Object.entries(score.dimensions) : [];
  return (
    <aside className="flex w-[26rem] shrink-0 flex-col border-l border-line bg-white">
      <div className="flex shrink-0 items-start gap-2 border-b border-line px-4 py-3">
        <div className="min-w-0 flex-1">
          <p className="text-[13.5px] font-semibold">{record.title}</p>
          <p className="truncate text-[11.5px] text-[#8b949e]">{record.external_id}</p>
          <div className="mt-1.5 flex flex-wrap gap-1">
            {score && <Badge tone={TIER_TONE[score.tier]}>{TIER_LABEL[score.tier]} · {score.total}점</Badge>}
            {record.labels.map((l) => <Badge key={l}>{l}</Badge>)}
          </div>
        </div>
        <button onClick={onClose} className="rounded p-1 text-[#8b949e] hover:bg-muted"><X className="size-4" /></button>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-3">
        {record.summary && (
          <section>
            <h4 className="mb-1 text-[12px] font-semibold text-[#57606a]">개요</h4>
            <p className="whitespace-pre-wrap text-[12.5px] leading-relaxed text-[#4b5563]">{record.summary}</p>
          </section>
        )}

        {!!Object.keys(record.attributes).length && (
          <section>
            <h4 className="mb-1 text-[12px] font-semibold text-[#57606a]">추출 항목</h4>
            <dl className="space-y-1">
              {Object.entries(record.attributes).map(([k, v]) => (
                <div key={k} className="flex gap-2 text-[12px]">
                  <dt className="w-24 shrink-0 truncate text-[#8b949e]">{k}</dt>
                  <dd className="min-w-0 flex-1">{String(v)}</dd>
                </div>
              ))}
            </dl>
          </section>
        )}

        {score && (
          <section>
            <div className="mb-1.5 flex items-center gap-2">
              <h4 className="text-[12px] font-semibold text-[#57606a]">차원별 평가</h4>
              <Badge tone={score.status === "approved" ? "ok" : score.status === "rejected" ? "bad" : "neutral"}>
                {score.status}
              </Badge>
              {score.status === "candidate" && (
                <span className="ml-auto flex gap-1">
                  <Button size="sm" variant="outline" onClick={() => onReview("score", score.id, "approved")}>
                    <Check className="size-3" />승인
                  </Button>
                  <Button size="sm" variant="danger" onClick={() => onReview("score", score.id, "rejected")}>반려</Button>
                </span>
              )}
            </div>
            <div className="space-y-2">
              {dims.map(([k, d]) => (
                <div key={k} className="rounded-lg border border-line px-2.5 py-2">
                  <p className="flex items-center gap-2 text-[12px]">
                    <span className="min-w-0 flex-1 truncate font-medium">{d.label}</span>
                    <span className="text-[11px] text-[#c1c7cd]">{(d.weight * 100).toFixed(0)}%</span>
                    <span className="w-8 text-right font-semibold tabular-nums">{d.score}</span>
                  </p>
                  {d.rationale && <p className="mt-1 text-[11.5px] leading-relaxed text-[#8b949e]">{d.rationale}</p>}
                </div>
              ))}
            </div>
          </section>
        )}

        {!!record._links?.length && (
          <section>
            <h4 className="mb-1 text-[12px] font-semibold text-[#57606a]">매칭 후보</h4>
            <div className="space-y-1">
              {record._links.map((l) => (
                <div key={l.id} className="flex items-center gap-2 rounded-lg border border-line px-2.5 py-1.5">
                  <span className="min-w-0 flex-1 truncate text-[12px]">
                    {l.from.id === record.id ? l.to.title : l.from.title}
                  </span>
                  <span className="shrink-0 text-[11px] text-[#c1c7cd]">{l.score}</span>
                  <Badge tone={LINK_TONE[l.status] ?? "neutral"}>{l.status}</Badge>
                </div>
              ))}
            </div>
          </section>
        )}

        {!!(score?.evidence?.length || record.evidence.length) && (
          <section>
            <h4 className="mb-1 text-[12px] font-semibold text-[#57606a]">근거 인용</h4>
            <div className="space-y-1.5">
              {(score?.evidence?.length ? score.evidence : record.evidence).map((e, i) => (
                <div key={i} className="rounded-lg border border-line px-2.5 py-2">
                  <p className="truncate text-[11.5px] font-medium">{e.path}{e.heading ? ` · ${e.heading}` : ""}</p>
                  <p className="mt-1 line-clamp-4 text-[11.5px] leading-relaxed text-[#8b949e]">{e.quote}</p>
                </div>
              ))}
            </div>
          </section>
        )}

        <p className="text-[11px] text-[#c1c7cd]">최근 갱신 {when(record.updated_at)}</p>
      </div>
    </aside>
  );
}
