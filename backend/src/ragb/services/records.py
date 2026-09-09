"""Record CRUD shared by every plug-in: upsert, embed, evidence, review verdicts."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ragb.core.errors import NotFound, ValidationFailed
from ragb.models import Collection, Record, RecordLink, RecordScore, Repository, ReviewItem, Scorecard
from ragb.providers.embedding import EmbeddingUnavailable, get_embedding_provider, pad

# The proposal's five dimensions. Shipped as data because Delphi/AHP exists to change them.
DEFAULT_SCORECARD = {
    "name": "사업화 유망도 (Commercialization Opportunity)",
    "description": "기술 후보를 5개 차원으로 평가한다. 가중치는 전문가 검증(Delphi/AHP) 결과로 조정한다.",
    "dimensions": [
        {"key": "momentum", "label": "기술 성장성 (Technology Momentum)", "weight": 0.25,
         "rubric": "특허·논문·R&D 증가 추세, 신규성, 기술융합 정도, IP 경쟁력"},
        {"key": "market", "label": "시장 매력도 (Market Attractiveness)", "weight": 0.2,
         "rubric": "시장 규모와 성장률, 신규 기업 진입, 투자 유입"},
        {"key": "demand", "label": "산업 수요 (Industry Demand)", "weight": 0.2,
         "rubric": "기업의 실제 기술수요, 현장 애로 해결 정도"},
        {"key": "feasibility", "label": "사업화 가능성 (Commercialization Feasibility)", "weight": 0.2,
         "rubric": "기술성숙도(TRL), 이전 가능성, 현장 적용 난이도"},
        {"key": "fit", "label": "전략 적합성 (Strategic Fit)", "weight": 0.15,
         "rubric": "기관 사업방향 및 정책과의 연계성"},
    ],
}

# Seeded on a new repository so a pipeline has somewhere to put things on day one.
DEFAULT_COLLECTIONS = [
    ("technology", "기술", "발굴·평가 대상 기술 후보"),
    ("company", "기업", "기술이전·사업화 대상 기업"),
    ("demand", "수요", "기업이 제기한 기술수요·현장 애로"),
]


async def ensure_defaults(db: AsyncSession, repo: Repository) -> None:
    """Give a new repository the collections and scorecard a pipeline expects. Idempotent."""
    existing = {c.key for c in (await db.execute(select(Collection).where(
        Collection.repository_id == repo.id))).scalars().all()}
    for key, name, desc in DEFAULT_COLLECTIONS:
        if key not in existing:
            db.add(Collection(repository_id=repo.id, key=key, name=name, description=desc))
    has_card = (await db.execute(select(Scorecard.id).where(Scorecard.repository_id == repo.id))).first()
    if has_card is None:
        db.add(Scorecard(repository_id=repo.id, name=DEFAULT_SCORECARD["name"],
                         description=DEFAULT_SCORECARD["description"],
                         dimensions=DEFAULT_SCORECARD["dimensions"], is_default=True))
    await db.flush()


async def get_collection(db: AsyncSession, repo_id: uuid.UUID, key_or_id: str) -> Collection:
    stmt = select(Collection).where(Collection.repository_id == repo_id)
    try:
        col = (await db.execute(stmt.where(Collection.id == uuid.UUID(str(key_or_id))))).scalars().first()
    except (ValueError, AttributeError):
        col = None
    if col is None:
        col = (await db.execute(stmt.where(Collection.key == str(key_or_id)))).scalars().first()
    if col is None:
        raise NotFound(f"컬렉션을 찾을 수 없습니다: {key_or_id}", code="collection_not_found")
    return col


def record_text(rec: Record) -> str:
    """What a record's embedding is built from. Attributes are included because a technology
    record's distinguishing content is usually in its extracted fields, not its title."""
    parts = [rec.title, rec.summary]
    for k, v in (rec.attributes or {}).items():
        if isinstance(v, (str, int, float)) and str(v).strip():
            parts.append(f"{k}: {v}")
    if rec.labels:
        parts.append("분류: " + ", ".join(str(x) for x in rec.labels))
    return "\n".join(p for p in parts if p)[:8000]


async def upsert(db: AsyncSession, collection: Collection, *, external_id: str, title: str,
                 summary: str = "", attributes: dict | None = None, labels: list | None = None,
                 source_node_id: uuid.UUID | None = None, evidence: list | None = None) -> tuple[Record, bool]:
    """Upsert on (collection, external_id). Returns (record, created)."""
    external_id = (external_id or title)[:200].strip() or "unknown"
    rec = (await db.execute(select(Record).where(Record.collection_id == collection.id,
                                                 Record.external_id == external_id))).scalars().first()
    created = rec is None
    if rec is None:
        rec = Record(collection_id=collection.id, repository_id=collection.repository_id,
                     external_id=external_id, title=title[:2000] or external_id)
        db.add(rec)
    rec.title = (title or rec.title)[:2000]
    if summary:
        rec.summary = summary[:8000]
    if attributes:
        rec.attributes = {**(rec.attributes or {}), **attributes}
    if labels is not None:
        rec.labels = labels
    if source_node_id is not None:
        rec.source_node_id = source_node_id
    if evidence is not None:
        rec.evidence = evidence[:20]
    rec.updated_at = datetime.now(UTC)
    await db.flush()
    return rec, created


async def embed_records(db: AsyncSession, records: list[Record]) -> int:
    """Embed in one batch. Returns how many got a vector; 0 means no embedding key, which is
    a degraded state for matching, not a failure of the run."""
    pending = [r for r in records if r.embedding is None or r.updated_at]
    if not pending:
        return 0
    try:
        emb = await get_embedding_provider(db)
        vectors = await emb.embed([record_text(r) for r in pending])
    except EmbeddingUnavailable:
        return 0
    for rec, vec in zip(pending, vectors, strict=True):
        rec.embedding = pad(vec)
    await db.flush()
    return len(pending)


async def refresh_counts(db: AsyncSession, repo_id: uuid.UUID) -> None:
    rows = (await db.execute(select(Record.collection_id, func.count()).where(
        Record.repository_id == repo_id).group_by(Record.collection_id))).all()
    counts = {cid: int(n) for cid, n in rows}
    for col in (await db.execute(select(Collection).where(Collection.repository_id == repo_id))).scalars().all():
        col.record_count = counts.get(col.id, 0)


async def default_scorecard(db: AsyncSession, repo_id: uuid.UUID) -> Scorecard | None:
    stmt = select(Scorecard).where(Scorecard.repository_id == repo_id, Scorecard.enabled.is_(True))
    return ((await db.execute(stmt.where(Scorecard.is_default.is_(True)))).scalars().first()
            or (await db.execute(stmt)).scalars().first())


def normalise_dimensions(dimensions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Weights are stored as given and normalised at use. An operator typing 25/20/20/20/15
    means percentages; one typing 0.25 means fractions; both must produce the same score."""
    out = []
    for d in dimensions or []:
        key = str(d.get("key") or "").strip()
        if not key:
            continue
        out.append({"key": key, "label": str(d.get("label") or key),
                    "weight": max(0.0, float(d.get("weight") or 0)), "rubric": str(d.get("rubric") or "")})
    total = sum(d["weight"] for d in out)
    if total <= 0:
        for d in out:
            d["weight"] = 1.0 / max(1, len(out))
    else:
        for d in out:
            d["weight"] = d["weight"] / total
    return out


TIERS = {"long": "Long-list", "short": "Short-list", "core": "핵심 Portfolio"}


async def review(db: AsyncSession, repo_id: uuid.UUID, *, target_type: str, target_id: uuid.UUID,
                 verdict: str, comment: str = "", reviewer_id: uuid.UUID | None = None,
                 reviewer_role: str = "") -> ReviewItem:
    """Record a verdict and apply it. The trail is append-only; the status on the target is
    just the latest verdict made cheap to query."""
    if verdict not in ("approved", "rejected", "revise"):
        raise ValidationFailed("verdict 는 approved | rejected | revise 중 하나여야 합니다", code="bad_verdict")
    now = datetime.now(UTC)
    if target_type == "link":
        target = await db.get(RecordLink, target_id)
        model = RecordLink
    elif target_type == "score":
        target = await db.get(RecordScore, target_id)
        model = RecordScore
    elif target_type == "record":
        target = await db.get(Record, target_id)
        model = Record
    else:
        raise ValidationFailed("target_type 은 record | link | score 중 하나여야 합니다", code="bad_target")
    if target is None or target.repository_id != repo_id:
        raise NotFound("검토 대상을 찾을 수 없습니다", code="review_target_not_found")
    values: dict[str, Any] = {"status": {"approved": "approved", "rejected": "rejected",
                                         "revise": "candidate"}[verdict]}
    if model is not Record:
        values |= {"reviewed_by": reviewer_id, "reviewed_at": now}
    elif verdict == "rejected":
        values["status"] = "archived"
    await db.execute(update(model).where(model.id == target_id).values(**values))
    item = ReviewItem(repository_id=repo_id, target_type=target_type, target_id=target_id, verdict=verdict,
                      comment=comment[:4000], reviewer_id=reviewer_id, reviewer_role=reviewer_role[:40],
                      created_at=now)
    db.add(item)
    await db.flush()
    return item
