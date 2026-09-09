"""Extract → hierarchy → embed → store. Runs in the worker, one file per job.

Two embedding passes, not one. Chunks carry the answer; section summaries carry the topic,
and a question phrased in the document's vocabulary rather than the author's only ever
matches the second one. Sections are summarised by a cheap model when one is configured and
fall back to their own opening lines when it is not — a degraded summary still beats no
section index at all.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from ragb.core.logging import get_logger
from ragb.models import Chunk, DocSection, Repository, StorageNode
from ragb.providers.embedding import EmbeddingUnavailable, get_embedding_provider, pad
from ragb.services import chunking, objectstore
from ragb.services import settings as S
from ragb.services import storage as ST
from ragb.services.extract import extract

log = get_logger("ragb.indexing")

SUMMARY_SYSTEM = (
    "너는 문서 색인기다. 주어진 섹션 본문을 검색용으로 요약한다. "
    "2~3문장, 한국어, 이 섹션에서만 답할 수 있는 고유 명사·수치·용어를 반드시 포함한다. "
    "설명이나 머리말 없이 요약문만 출력한다."
)


def _extract_sync(path: str, mime: str, filename: str) -> str:
    with open(path, "rb") as fh:
        data = fh.read()
    return extract(data, mime, filename).text


async def _materialise(node: StorageNode) -> tuple[str, str | None]:
    """The parsers take a path; object storage has none. Returns (path, tempfile-to-remove)."""
    src = node.storage_path or ""
    if not src.startswith(objectstore.S3_PREFIX):
        return src, None
    blob = await objectstore.get(src)
    fd, tmp = tempfile.mkstemp(suffix=os.path.splitext(node.name)[1])
    with os.fdopen(fd, "wb") as fh:
        fh.write(blob)
    return tmp, tmp


async def _mark_failed(node_id: uuid.UUID, error: str) -> None:
    """Own transaction: the worker rolls the job transaction back when the handler raises."""
    from ragb.db.session import session_scope
    async with session_scope() as db2:
        await db2.execute(update(StorageNode).where(StorageNode.id == node_id).values(
            status="failed", error=error[:500]))


async def index_node(db: AsyncSession, node_id: uuid.UUID) -> dict[str, Any]:
    node = await db.get(StorageNode, node_id)
    if node is None or node.kind != "file":
        return {"skipped": "missing"}
    repo = await db.get(Repository, node.repository_id)
    cfg = await S.rag_config(db, (repo.settings if repo else {}) or {})

    tmp = None
    try:
        path, tmp = await _materialise(node)
        if not path:
            raise ValueError("blob_missing")
        full = await asyncio.to_thread(_extract_sync, path, node.mime or "", node.name)
    except (ValueError, OSError, LookupError) as e:
        node.status, node.error = "failed", str(e)[:500] or e.__class__.__name__
        return {"failed": node.error}
    finally:
        if tmp:
            with contextlib.suppress(OSError):
                os.remove(tmp)

    hier = await asyncio.to_thread(
        chunking.build, full,
        target=int(cfg["chunk_tokens"]), max_tokens=int(cfg["chunk_max_tokens"]), overlap=int(cfg["chunk_overlap"]))
    if not hier.chunks:
        node.status, node.error = "failed", "no_text"
        return {"failed": "no_text"}

    # Summaries before embedding: the section vector is built from the summary, so a summary
    # that arrives later would need a second embedding pass to be worth anything.
    summaries = await _summarise_sections(db, hier, cfg, node)

    emb = None
    chunk_vecs: list[list[float] | None] = [None] * len(hier.chunks)
    section_vecs: list[list[float] | None] = [None] * len(hier.sections)
    try:
        emb = await get_embedding_provider(db)
        chunk_vecs = list(await emb.embed([_embed_text(c.heading_path, c.text) for c in hier.chunks]))
        section_vecs = list(await emb.embed([_embed_text(s.heading_path, summaries[i]) for i, s in enumerate(hier.sections)]))
    except EmbeddingUnavailable:
        # Not a failure. Search runs five legs and three of them need no vector at all, so an
        # install without an embedding key still answers — by keyword — and re-indexes later.
        log.warning("indexing without embeddings", node=str(node.id))
    except Exception as e:  # noqa: BLE001
        await _mark_failed(node.id, f"embedding_failed: {str(e)[:200]}")
        raise

    await db.execute(delete(Chunk).where(Chunk.node_id == node.id))
    await db.execute(delete(DocSection).where(DocSection.node_id == node.id))
    await db.flush()

    section_ids: dict[int, uuid.UUID] = {}
    for i, s in enumerate(hier.sections):
        row = DocSection(node_id=node.id, repository_id=node.repository_id, level=s.level, ordinal=s.index,
                         title=s.title[:2000], heading_path=s.heading_path[:2000], summary=summaries[i][:4000],
                         tokens=s.tokens, chunk_from=s.chunk_from, chunk_to=s.chunk_to, page=s.page,
                         embedding=pad(section_vecs[i]) if section_vecs[i] is not None else None)
        db.add(row)
        await db.flush()
        section_ids[s.index] = row.id
    # A synthesised parent index refers to positions in the same list, so resolve after insert.
    for s in hier.sections:
        if s.parent is not None and s.parent in section_ids:
            await db.execute(update(DocSection).where(DocSection.id == section_ids[s.index]).values(
                parent_id=section_ids[s.parent]))

    for c, v in zip(hier.chunks, chunk_vecs, strict=True):
        db.add(Chunk(node_id=node.id, section_id=section_ids.get(c.section), repository_id=node.repository_id,
                     ordinal=c.ordinal, text=c.text, tokens=c.tokens, heading_path=c.heading_path[:2000],
                     page=c.page, embedding=pad(v) if v is not None else None))

    node.chunk_count = len(hier.chunks)
    node.section_count = len(hier.sections)
    node.tokens = sum(c.tokens for c in hier.chunks)
    node.status, node.error = "ready", ""
    node.embedding_model = f"{emb.provider}/{emb.model}" if emb is not None else ""
    node.text_preview = full[:2000]
    node.summary = (summaries[0] if summaries else "")[:2000]
    node.indexed_at = datetime.now(UTC)
    await db.flush()
    await ST.refresh_counts(db, node.repository_id)
    return {"chunks": len(hier.chunks), "sections": len(hier.sections), "embedded": emb is not None}


def _embed_text(heading_path: str, body: str) -> str:
    """The heading path rides along in the embedded text. Without it a chunk that says only
    "3개월" is a vector about a number; with it, it is a vector about that number in that
    chapter of that document."""
    head = (heading_path or "").strip()
    return f"{head}\n{body}" if head else body


def _fallback_summary(section: chunking.Section) -> str:
    body = " ".join(section.text.split())[:400]
    return f"{section.heading_path or section.title}: {body}" if body else (section.heading_path or section.title)


async def _summarise_sections(db: AsyncSession, hier: chunking.Hierarchy, cfg: dict[str, Any],
                              node: StorageNode) -> list[str]:
    fallback = [_fallback_summary(s) for s in hier.sections]
    if not cfg.get("section_summary_enabled") or not hier.sections:
        return fallback
    limit = int(cfg.get("section_summary_max_sections") or 60)
    if len(hier.sections) > limit:
        log.info("section summaries capped", node=str(node.id), sections=len(hier.sections), limit=limit)
    from ragb.providers.llm.simple import complete
    provider = str(cfg.get("section_summary_provider") or "claude_code")
    model = str(cfg.get("section_summary_model") or "")
    out = list(fallback)
    # Sequential on purpose: this runs inside a worker job that already has the whole file,
    # and a burst of parallel CLI spawns is the fastest way to make indexing the thing that
    # takes the machine down.
    for i, s in enumerate(hier.sections[:limit]):
        body = s.text.strip()
        if len(body) < 200:
            continue
        try:
            text, _ = await complete(db, provider=provider, model=model, system=SUMMARY_SYSTEM,
                                     user_text=f"[문서] {node.name}\n[섹션] {s.heading_path or s.title}\n\n{body[:6000]}",
                                     max_tokens=300, timeout_s=90)
            if text:
                out[i] = f"{s.heading_path or s.title}: {text}"
        except Exception as e:  # noqa: BLE001
            log.warning("section summary failed", node=str(node.id), section=i, err=str(e)[:160])
            break     # one failure means the provider is down; the rest would fail the same way
    return out
