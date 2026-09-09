"""Retrieval: five candidate legs, weighted RRF, hierarchical expansion, optional rerank.

The design in one line: *fuse at chunk level, but let sections and file paths vote.*

  1. chunk vector      — the sentence that answers the question
  2. section vector    — the chapter the question is about (the hierarchy leg)
  3. tsvector          — exact words
  4. trigram / ILIKE   — Korean glues particles onto nouns, which defeats (3)
  5. path / filename   — "그 매뉴얼 pdf에 뭐라고 써 있어?" names the file, not its contents

Legs 2 and 5 do not produce chunks of their own; they project their rank onto the chunks
underneath them, capped so one long chapter cannot flood the fusion. Everything then meets
in one reciprocal-rank fusion with per-leg weights an admin can tune, because which leg
matters depends entirely on the corpus — a wiki and a scanned PDF archive want different
numbers, and the honest answer is to expose them rather than to pick one and pretend.

After fusion the surviving chunks are grown back into readable passages: adjacent chunks
merge, each passage keeps its heading path for citation, and a per-document cap keeps one
big file from taking the whole context window.
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text as sql
from sqlalchemy.ext.asyncio import AsyncSession

from ragb.core.logging import get_logger
from ragb.providers.embedding import get_embedding_provider, pad
from ragb.providers.http import request
from ragb.services import settings as S

log = get_logger("ragb.retrieval")

# Korean attaches particles to nouns, so "보증기간을" and "보증기간이" must reduce to one stem
# before any lexical leg can match. This is a stemmer's job; a stemmer is a dependency and a
# model download, and for query terms a suffix list gets most of the benefit for none of it.
_PARTICLES = ("으로서", "으로써", "에서는", "에게는", "이라고", "라고", "에서", "에게", "부터", "까지",
              "하고", "이랑", "으로", "이나", "인가요", "인가", "나요", "까요", "은", "는", "이", "가",
              "을", "를", "에", "의", "와", "과", "도", "만", "로", "요")
_STOPWORDS = {"어떤", "무엇", "뭐", "누구", "언제", "어디", "어떻게", "얼마", "왜", "그리고", "하는", "있나",
              "있나요", "있어요", "해주세요", "알려", "알려줘", "알려주세요", "무슨", "관련", "내용", "정리",
              "what", "which", "who", "when", "where", "how", "the", "and", "for", "you", "your", "are", "is"}


def query_terms(query: str, limit: int = 8) -> list[str]:
    out: list[str] = []
    for raw in re.split(r"[^\w가-힣]+", (query or "").lower()):
        if len(raw) < 2:
            continue
        tok = raw
        for p in _PARTICLES:
            if tok.endswith(p) and len(tok) > len(p) + 1:
                tok = tok[: -len(p)]
                break
        if len(tok) >= 2 and tok not in _STOPWORDS and tok not in out:
            out.append(tok)
    return out[:limit]


@dataclass
class Passage:
    node_id: str
    node_name: str
    node_path: str
    repository_id: str
    repository_name: str
    heading_path: str
    page: int | None
    text: str
    score: float
    ordinals: list[int] = field(default_factory=list)
    legs: list[str] = field(default_factory=list)
    section_title: str = ""

    def cite(self, n: int) -> str:
        where = " > ".join(x for x in [self.node_path.lstrip("/"), self.heading_path] if x)
        page = f" p.{self.page}" if self.page else ""
        return f"[{n}] {where}{page}"

    def as_dict(self) -> dict[str, Any]:
        return {"node_id": self.node_id, "name": self.node_name, "path": self.node_path,
                "repository_id": self.repository_id, "repository": self.repository_name,
                "heading": self.heading_path, "page": self.page, "text": self.text,
                "score": round(self.score, 4), "legs": self.legs}


@dataclass
class SearchResult:
    passages: list[Passage] = field(default_factory=list)
    legs: dict[str, int] = field(default_factory=dict)
    candidates: int = 0
    latency_ms: int = 0
    reranked: bool = False
    embedded: bool = False
    note: str = ""


async def embed_query(db: AsyncSession, q: str) -> list[float] | None:
    """None means the lexical legs carry the search alone — a missing key degrades quality,
    it does not break the feature."""
    try:
        emb = await get_embedding_provider(db)
        return pad((await emb.embed([q], kind="query"))[0])
    except Exception as e:  # noqa: BLE001
        log.info("query embedding unavailable", err=str(e)[:120])
        return None


async def search(db: AsyncSession, *, repository_ids: list[uuid.UUID], query: str, cfg: dict[str, Any],
                 k: int | None = None, path_prefix: str = "") -> SearchResult:
    t0 = time.monotonic()
    res = SearchResult()
    query = (query or "").strip()
    if not query or not repository_ids:
        return res
    k = int(k or cfg.get("top_k") or 8)
    per_leg = int(cfg.get("candidates_per_leg") or 24)
    rrf_k = float(cfg.get("rrf_k") or 60)
    terms = query_terms(query)

    params: dict[str, Any] = {"repos": [str(r) for r in repository_ids], "q": query, "n": per_leg}
    scope = "c.repository_id = ANY(CAST(:repos AS uuid[]))"
    node_scope = "n.repository_id = ANY(CAST(:repos AS uuid[]))"
    if path_prefix:
        params["prefix"] = path_prefix.rstrip("/") + "/%"
        scope += " AND (n.path LIKE :prefix OR n.path = :prefix_exact)"
        node_scope += " AND (n.path LIKE :prefix OR n.path = :prefix_exact)"
        params["prefix_exact"] = path_prefix.rstrip("/")

    ranks: dict[uuid.UUID, float] = {}
    legs_of: dict[uuid.UUID, list[str]] = {}
    rows: dict[uuid.UUID, Any] = {}

    def absorb(leg: str, weight: float, found: list[Any]) -> None:
        res.legs[leg] = len(found)
        if weight <= 0:
            return
        for i, r in enumerate(found):
            cid = r["id"]
            ranks[cid] = ranks.get(cid, 0.0) + weight / (rrf_k + i)
            rows.setdefault(cid, r)
            legs_of.setdefault(cid, []).append(leg)

    SELECT = ("c.id, c.node_id, c.section_id, c.ordinal, c.text, c.heading_path, c.page, "
              "n.name AS node_name, n.path AS node_path, n.repository_id, r.name AS repo_name")
    JOIN = ("FROM chunks c JOIN storage_nodes n ON n.id = c.node_id "
            "JOIN repositories r ON r.id = c.repository_id")
    READY = "n.status = 'ready'"

    qvec = await embed_query(db, query)
    res.embedded = qvec is not None

    # ── 1. dense chunks ──────────────────────────────────────────────────────────
    if qvec is not None:
        p = {**params, "v": str(qvec), "minscore": float(cfg.get("min_vector_score") or 0.15)}
        found = (await db.execute(sql(f"""
            SELECT {SELECT}, 1 - (c.embedding <=> CAST(:v AS vector)) AS score
            {JOIN} WHERE {scope} AND {READY} AND c.embedding IS NOT NULL
            ORDER BY c.embedding <=> CAST(:v AS vector) LIMIT :n"""), p)).mappings().all()
        found = [r for r in found if float(r["score"]) >= p["minscore"]]
        absorb("vector", float(cfg.get("weight_vector") or 0), found)

        # ── 2. dense sections, projected onto their best chunks ──────────────────
        sec_cap = max(2, k // 2)
        p2 = {**params, "v": str(qvec), "sn": max(4, per_leg // 3), "cap": sec_cap}
        found = (await db.execute(sql(f"""
            WITH top_sections AS (
                SELECT s.id, s.node_id, s.chunk_from, s.chunk_to,
                       1 - (s.embedding <=> CAST(:v AS vector)) AS sscore,
                       ROW_NUMBER() OVER (ORDER BY s.embedding <=> CAST(:v AS vector)) AS srank
                FROM doc_sections s JOIN storage_nodes n ON n.id = s.node_id
                WHERE {node_scope.replace('n.repository_id', 's.repository_id')} AND {READY}
                  AND s.embedding IS NOT NULL
                ORDER BY s.embedding <=> CAST(:v AS vector) LIMIT :sn
            ), ranked AS (
                SELECT {SELECT}, t.sscore AS score, t.srank,
                       ROW_NUMBER() OVER (PARTITION BY t.id
                                          ORDER BY similarity(c.text, :q) DESC, c.ordinal) AS within
                {JOIN} JOIN top_sections t ON c.section_id = t.id
            )
            SELECT * FROM ranked WHERE within <= :cap ORDER BY srank, within"""), p2)).mappings().all()
        absorb("section", float(cfg.get("weight_section") or 0), found)

    # ── 3. tsvector ──────────────────────────────────────────────────────────────
    found = (await db.execute(sql(f"""
        SELECT {SELECT}, ts_rank(c.tsv, plainto_tsquery('simple', :q)) AS score
        {JOIN} WHERE {scope} AND {READY} AND c.tsv @@ plainto_tsquery('simple', :q)
        ORDER BY score DESC LIMIT :n"""), params)).mappings().all()
    absorb("lexical", float(cfg.get("weight_lexical") or 0), found)

    # ── 4. trigram / substring (Korean) ──────────────────────────────────────────
    if terms:
        like = " OR ".join(f"c.text ILIKE :t{i}" for i in range(len(terms)))
        p = {**params, **{f"t{i}": f"%{t}%" for i, t in enumerate(terms)}}
        found = (await db.execute(sql(f"""
            SELECT {SELECT}, similarity(c.text, :q) AS score
            {JOIN} WHERE {scope} AND {READY} AND ({like})
            ORDER BY score DESC, length(c.text) ASC LIMIT :n"""), p)).mappings().all()
        absorb("trigram", float(cfg.get("weight_trigram") or 0), found)

        # ── 5. file name / folder path ───────────────────────────────────────────
        plike = " OR ".join(f"n.path ILIKE :p{i}" for i in range(len(terms)))
        p = {**params, **{f"p{i}": f"%{t}%" for i, t in enumerate(terms)}, "cap": 3}
        found = (await db.execute(sql(f"""
            WITH hits AS (
                SELECT n.id, similarity(n.path, :q) AS pscore,
                       ROW_NUMBER() OVER (ORDER BY similarity(n.path, :q) DESC) AS prank
                FROM storage_nodes n
                WHERE {node_scope} AND n.kind = 'file' AND {READY} AND ({plike})
                ORDER BY pscore DESC LIMIT 8
            ), ranked AS (
                SELECT {SELECT}, h.pscore AS score, h.prank,
                       ROW_NUMBER() OVER (PARTITION BY h.id
                                          ORDER BY similarity(c.text, :q) DESC, c.ordinal) AS within
                {JOIN} JOIN hits h ON h.id = c.node_id
            )
            SELECT * FROM ranked WHERE within <= :cap ORDER BY prank, within"""), p)).mappings().all()
        absorb("path", float(cfg.get("weight_path") or 0), found)

    res.candidates = len(ranks)
    if not ranks:
        res.latency_ms = int((time.monotonic() - t0) * 1000)
        return res

    pool = int(cfg.get("rerank_candidates") or 30) if cfg.get("rerank_enabled") else max(k * 3, k)
    ordered = sorted(ranks.items(), key=lambda kv: kv[1], reverse=True)[:max(pool, k)]
    passages = await _expand(db, ordered, rows, legs_of, cfg)
    passages = _diversify(passages, int(cfg.get("max_per_document") or 3), max(pool, k))

    if cfg.get("rerank_enabled") and len(passages) > 1:
        reranked = await _rerank(db, query, passages, cfg, k)
        if reranked is not None:
            passages, res.reranked = reranked, True

    passages = _fit_budget(passages[:k], int(cfg.get("context_token_budget") or 6000))
    res.passages = passages
    res.latency_ms = int((time.monotonic() - t0) * 1000)
    return res


async def _expand(db: AsyncSession, ordered: list[tuple[uuid.UUID, float]], rows: dict[uuid.UUID, Any],
                  legs_of: dict[uuid.UUID, list[str]], cfg: dict[str, Any]) -> list[Passage]:
    """Grow winning chunks back into passages: neighbours joined, contiguous runs merged.

    A chunk boundary is an artefact of the chunker, not of the document. Answering from one
    side of it and citing only that side is how a RAG bot produces a confident half-answer.
    """
    window = int(cfg.get("neighbor_window") or 0)
    winners = [cid for cid, _ in ordered]
    by_node: dict[str, list[tuple[int, uuid.UUID, float]]] = {}
    scores = dict(ordered)
    for cid in winners:
        r = rows[cid]
        by_node.setdefault(str(r["node_id"]), []).append((int(r["ordinal"]), cid, scores[cid]))

    neighbours: dict[tuple[str, int], Any] = {}
    if window > 0:
        wanted: list[dict[str, Any]] = []
        for node_id, items in by_node.items():
            for ordinal, _, _ in items:
                for o in range(ordinal - window, ordinal + window + 1):
                    if o >= 0:
                        wanted.append({"n": node_id, "o": o})
        if wanted:
            got = (await db.execute(sql("""
                SELECT c.node_id, c.ordinal, c.text, c.heading_path, c.page
                FROM chunks c JOIN unnest(CAST(:nodes AS uuid[]), CAST(:ords AS int[])) AS w(n, o)
                  ON c.node_id = w.n AND c.ordinal = w.o"""),
                {"nodes": [w["n"] for w in wanted], "ords": [w["o"] for w in wanted]})).mappings().all()
            for g in got:
                neighbours[(str(g["node_id"]), int(g["ordinal"]))] = g

    passages: list[Passage] = []
    for node_id, items in by_node.items():
        items.sort()
        # Merge chunk ordinals that touch (including through the neighbour window) into runs.
        runs: list[list[tuple[int, uuid.UUID, float]]] = []
        for item in items:
            if runs and item[0] - runs[-1][-1][0] <= (2 * window + 1):
                runs[-1].append(item)
            else:
                runs.append([item])
        for run in runs:
            lo, hi = run[0][0] - window, run[-1][0] + window
            parts: list[str] = []
            ordinals: list[int] = []
            for o in range(max(0, lo), hi + 1):
                g = neighbours.get((node_id, o))
                if g is not None:
                    parts.append(g["text"])
                    ordinals.append(o)
                else:
                    hit = next((c for ordv, c, _ in run if ordv == o), None)
                    if hit is not None:
                        parts.append(rows[hit]["text"])
                        ordinals.append(o)
            head = rows[run[0][1]]
            legs: list[str] = []
            for _, cid, _ in run:
                for leg in legs_of.get(cid, []):
                    if leg not in legs:
                        legs.append(leg)
            passages.append(Passage(
                node_id=node_id, node_name=head["node_name"], node_path=head["node_path"],
                repository_id=str(head["repository_id"]), repository_name=head["repo_name"],
                heading_path=head["heading_path"] or "", page=head["page"],
                text="\n".join(p for p in parts if p).strip(),
                score=max(s for _, _, s in run), ordinals=ordinals, legs=legs))
    passages.sort(key=lambda p: p.score, reverse=True)
    return passages


def _diversify(passages: list[Passage], max_per_doc: int, limit: int) -> list[Passage]:
    """One document answering everything is usually retrieval collapse, not relevance."""
    seen: dict[str, int] = {}
    out: list[Passage] = []
    overflow: list[Passage] = []
    for p in passages:
        if seen.get(p.node_id, 0) < max_per_doc:
            seen[p.node_id] = seen.get(p.node_id, 0) + 1
            out.append(p)
        else:
            overflow.append(p)
    # Overflow is kept, just demoted: with one relevant document the cap must not empty the
    # result set.
    return (out + overflow)[:limit]


def _fit_budget(passages: list[Passage], budget_tokens: int) -> list[Passage]:
    """Rough 3 chars ≈ 1 token for mixed Korean/English; the exact tokenizer is not worth a
    second pass here, and the budget is a guard rail rather than a contract."""
    remaining = budget_tokens * 3
    out: list[Passage] = []
    for p in passages:
        if remaining <= 200:
            break
        if len(p.text) > remaining:
            p.text = p.text[:remaining].rstrip() + "…"
        remaining -= len(p.text)
        out.append(p)
    return out


async def _rerank(db: AsyncSession, query: str, passages: list[Passage], cfg: dict[str, Any],
                  k: int) -> list[Passage] | None:
    """Cross-encoder rerank. Returns None on any failure — a reranker that is down must cost
    ranking quality, never the answer."""
    provider = str(cfg.get("rerank_provider") or "voyage")
    try:
        if provider == "voyage":
            key = await S.get(db, "providers.voyage.api_key")
            if not key:
                return None
            r = await request("POST", "https://api.voyageai.com/v1/rerank",
                              headers={"Authorization": f"Bearer {key}"},
                              json={"model": str(cfg.get("rerank_model") or "rerank-2"), "query": query,
                                    "documents": [p.text[:8000] for p in passages], "top_k": min(k, len(passages))})
            order = sorted(r.json()["data"], key=lambda d: -float(d["relevance_score"]))
            out = []
            for d in order:
                p = passages[int(d["index"])]
                p.score = float(d["relevance_score"])
                out.append(p)
            return out
        if provider == "llm":
            from ragb.providers.llm.simple import complete
            listing = "\n\n".join(f"[{i}] {p.node_name} > {p.heading_path}\n{p.text[:900]}" for i, p in enumerate(passages))
            text, _ = await complete(
                db, provider=str(cfg.get("section_summary_provider") or "claude_code"),
                model=str(cfg.get("section_summary_model") or ""),
                system="너는 검색 결과 재순위기다. 질문에 실제로 답이 되는 순서대로 번호만 쉼표로 출력한다. 설명 금지.",
                user_text=f"[질문] {query}\n\n{listing[:20000]}", max_tokens=200, timeout_s=60)
            idx = [int(x) for x in re.findall(r"\d+", text)]
            picked, seen = [], set()
            for i in idx:
                if 0 <= i < len(passages) and i not in seen:
                    seen.add(i)
                    picked.append(passages[i])
            return picked[:k] or None
    except Exception as e:  # noqa: BLE001
        log.warning("rerank failed", provider=provider, err=str(e)[:160])
    return None


def format_context(passages: list[Passage]) -> str:
    """The context block the model sees. Numbered so the answer can cite [1] and mean it."""
    if not passages:
        return ""
    out = ["[검색된 문서 발췌]"]
    for i, p in enumerate(passages, 1):
        out.append(f"\n{p.cite(i)}\n{p.text}")
    return "\n".join(out)
