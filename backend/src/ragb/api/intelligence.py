"""[RAG 저장소 → 파이프라인 · 레코드] — plug-in bindings, runs, records, scores, matches, review."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ragb.core.deps import current_user
from ragb.core.errors import NotFound, ValidationFailed
from ragb.db.session import get_session
from ragb.models import (
    Collection,
    PluginBinding,
    PluginRun,
    Record,
    RecordLink,
    RecordScore,
    ReviewItem,
    Scorecard,
    User,
)
from ragb.plugins import catalog as plugin_catalog
from ragb.services import audit
from ragb.services import pipeline as P
from ragb.services import records as R
from ragb.services import storage as ST

router = APIRouter(prefix="/api/repositories/{repo_id}", tags=["intelligence"])
meta_router = APIRouter(prefix="/api/plugins", tags=["intelligence"])


@meta_router.get("")
async def list_plugins(_: User = Depends(current_user)):
    """The plug-in catalog: what exists, what each one needs, what it produces."""
    return {"items": plugin_catalog()}


# ── collections & records ───────────────────────────────────────────────────────

def record_json(r: Record, score: RecordScore | None = None) -> dict[str, Any]:
    out = {"id": str(r.id), "collection_id": str(r.collection_id), "external_id": r.external_id,
           "title": r.title, "summary": r.summary, "attributes": r.attributes or {},
           "labels": r.labels or [], "status": r.status, "embedded": r.embedding is not None,
           "source_node_id": str(r.source_node_id) if r.source_node_id else None,
           "evidence": r.evidence or [], "updated_at": r.updated_at.isoformat() if r.updated_at else None}
    if score is not None:
        out["score"] = {"id": str(score.id), "total": score.total, "tier": score.tier,
                        "status": score.status,
                        "dimensions": {k: v for k, v in (score.dimensions or {}).items() if not k.startswith("_")},
                        "evidence": (score.dimensions or {}).get("_evidence") or []}
    return out


@router.get("/collections")
async def list_collections(repo_id: uuid.UUID, user: User = Depends(current_user),
                           db: AsyncSession = Depends(get_session)):
    repo = await ST.get_repository(db, user, repo_id)
    await R.ensure_defaults(db, repo)
    await R.refresh_counts(db, repo.id)
    await db.commit()
    cols = list((await db.execute(select(Collection).where(Collection.repository_id == repo.id)
                                  .order_by(Collection.created_at))).scalars().all())
    cards = list((await db.execute(select(Scorecard).where(Scorecard.repository_id == repo.id)
                                   .order_by(Scorecard.created_at))).scalars().all())
    return {"items": [{"id": str(c.id), "key": c.key, "name": c.name, "description": c.description,
                       "record_count": c.record_count} for c in cols],
            "scorecards": [{"id": str(s.id), "name": s.name, "description": s.description,
                            "dimensions": s.dimensions, "is_default": s.is_default} for s in cards]}


class CollectionIn(BaseModel):
    key: str
    name: str
    description: str = ""


@router.post("/collections")
async def create_collection(repo_id: uuid.UUID, body: CollectionIn, user: User = Depends(current_user),
                            db: AsyncSession = Depends(get_session)):
    repo = await ST.get_repository(db, user, repo_id, write=True)
    key = "".join(ch for ch in body.key.strip().lower() if ch.isalnum() or ch in "_-")[:60]
    if not key:
        raise ValidationFailed("컬렉션 키는 영문·숫자여야 합니다", code="bad_key")
    col = Collection(repository_id=repo.id, key=key, name=body.name[:120] or key,
                     description=body.description[:2000])
    db.add(col)
    await db.commit()
    return {"id": str(col.id), "key": col.key, "name": col.name}


@router.get("/records")
async def list_records(repo_id: uuid.UUID, collection: str = "", q: str = "", tier: str = "",
                       limit: int = Query(60, le=300), user: User = Depends(current_user),
                       db: AsyncSession = Depends(get_session)):
    repo = await ST.get_repository(db, user, repo_id)
    stmt = select(Record).where(Record.repository_id == repo.id, Record.status == "active")
    if collection:
        col = await R.get_collection(db, repo.id, collection)
        stmt = stmt.where(Record.collection_id == col.id)
    if q:
        stmt = stmt.where(Record.title.ilike(f"%{q}%") | Record.summary.ilike(f"%{q}%"))
    rows = list((await db.execute(stmt.order_by(Record.created_at.desc()).limit(limit))).scalars().all())
    scores = {s.record_id: s for s in (await db.execute(select(RecordScore).where(
        RecordScore.record_id.in_([r.id for r in rows] or [uuid.uuid4()])))).scalars().all()}
    items = [record_json(r, scores.get(r.id)) for r in rows]
    if tier:
        items = [i for i in items if (i.get("score") or {}).get("tier") == tier]
    # Scored records first, best first — that is the Long/Short/Core view the programme wants.
    items.sort(key=lambda i: -((i.get("score") or {}).get("total") or -1))
    return {"items": items}


@router.get("/records/{record_id}")
async def get_record(repo_id: uuid.UUID, record_id: uuid.UUID, user: User = Depends(current_user),
                     db: AsyncSession = Depends(get_session)):
    repo = await ST.get_repository(db, user, repo_id)
    rec = await db.get(Record, record_id)
    if rec is None or rec.repository_id != repo.id:
        raise NotFound("레코드를 찾을 수 없습니다", code="record_not_found")
    score = (await db.execute(select(RecordScore).where(RecordScore.record_id == rec.id))).scalars().first()
    links = list((await db.execute(select(RecordLink).where(
        (RecordLink.from_record_id == rec.id) | (RecordLink.to_record_id == rec.id))
        .order_by(RecordLink.score.desc()).limit(20))).scalars().all())
    others = {r.id: r for r in (await db.execute(select(Record).where(Record.id.in_(
        [x.to_record_id for x in links] + [x.from_record_id for x in links] or [uuid.uuid4()])))).scalars().all()}
    reviews = list((await db.execute(select(ReviewItem).where(
        ReviewItem.target_id.in_([rec.id] + [x.id for x in links] + ([score.id] if score else [])))
        .order_by(ReviewItem.created_at.desc()).limit(30))).scalars().all())
    return {"record": record_json(rec, score),
            "links": [_link_json(x, others) for x in links],
            "reviews": [{"id": str(v.id), "target_type": v.target_type, "target_id": str(v.target_id),
                         "verdict": v.verdict, "comment": v.comment, "reviewer_role": v.reviewer_role,
                         "created_at": v.created_at.isoformat()} for v in reviews]}


def _link_json(x: RecordLink, others: dict[uuid.UUID, Record]) -> dict[str, Any]:
    a, b = others.get(x.from_record_id), others.get(x.to_record_id)
    return {"id": str(x.id), "from": {"id": str(x.from_record_id), "title": a.title if a else ""},
            "to": {"id": str(x.to_record_id), "title": b.title if b else ""},
            "score": x.score, "rerank_score": x.rerank_score, "rationale": x.rationale,
            "status": x.status, "kind": x.kind}


@router.get("/links")
async def list_links(repo_id: uuid.UUID, status: str = "", limit: int = Query(100, le=400),
                     user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    repo = await ST.get_repository(db, user, repo_id)
    stmt = select(RecordLink).where(RecordLink.repository_id == repo.id)
    if status:
        stmt = stmt.where(RecordLink.status == status)
    rows = list((await db.execute(stmt.order_by(RecordLink.score.desc()).limit(limit))).scalars().all())
    ids = {x.from_record_id for x in rows} | {x.to_record_id for x in rows}
    others = {r.id: r for r in (await db.execute(select(Record).where(
        Record.id.in_(ids or {uuid.uuid4()})))).scalars().all()}
    return {"items": [_link_json(x, others) for x in rows]}


class ReviewIn(BaseModel):
    target_type: str
    target_id: uuid.UUID
    verdict: str
    comment: str = ""
    reviewer_role: str = ""


@router.post("/review")
async def submit_review(repo_id: uuid.UUID, body: ReviewIn, user: User = Depends(current_user),
                        db: AsyncSession = Depends(get_session)):
    """Human-in-the-Loop. Any signed-in user may review — expertise is a matter of who the
    operator invites, and an approval trail that records the person is more useful than a
    role gate that stops the wrong person from ever being asked."""
    repo = await ST.get_repository(db, user, repo_id)
    item = await R.review(db, repo.id, target_type=body.target_type, target_id=body.target_id,
                          verdict=body.verdict, comment=body.comment, reviewer_id=user.id,
                          reviewer_role=body.reviewer_role or ("admin" if user.is_admin else "user"))
    audit.record(db, "review.submit", actor_id=user.id, target_type=body.target_type,
                 target_id=body.target_id, meta={"verdict": body.verdict})
    await db.commit()
    return {"id": str(item.id), "verdict": item.verdict}


class ScorecardIn(BaseModel):
    name: str
    description: str = ""
    dimensions: list[dict] = []
    is_default: bool = False


@router.put("/scorecards/{scorecard_id}")
async def update_scorecard(repo_id: uuid.UUID, scorecard_id: uuid.UUID, body: ScorecardIn,
                           user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    repo = await ST.get_repository(db, user, repo_id, write=True)
    card = await db.get(Scorecard, scorecard_id)
    if card is None or card.repository_id != repo.id:
        raise NotFound("스코어카드를 찾을 수 없습니다", code="scorecard_not_found")
    card.name = body.name[:120] or card.name
    card.description = body.description[:2000]
    card.dimensions = R.normalise_dimensions(body.dimensions) or card.dimensions
    if body.is_default:
        for other in (await db.execute(select(Scorecard).where(Scorecard.repository_id == repo.id))).scalars().all():
            other.is_default = other.id == card.id
    await db.commit()
    return {"id": str(card.id), "dimensions": card.dimensions}


# ── pipeline (plug-in bindings + runs) ──────────────────────────────────────────

def binding_json(b: PluginBinding) -> dict[str, Any]:
    spec = P.spec_of(b.plugin_id).as_dict()
    return {"id": str(b.id), "plugin_id": b.plugin_id, "name": b.name, "config": b.config or {},
            "enabled": b.enabled, "sort_order": b.sort_order, "kind": spec["kind"],
            "kind_label": spec["kind_label"], "plugin_name": spec["name"],
            "last_run_at": b.last_run_at.isoformat() if b.last_run_at else None,
            "last_status": b.last_status}


@router.get("/pipeline")
async def get_pipeline(repo_id: uuid.UUID, user: User = Depends(current_user),
                       db: AsyncSession = Depends(get_session)):
    repo = await ST.get_repository(db, user, repo_id)
    rows = list((await db.execute(select(PluginBinding).where(PluginBinding.repository_id == repo.id)
                                  .order_by(PluginBinding.sort_order, PluginBinding.created_at))).scalars().all())
    runs = list((await db.execute(select(PluginRun).where(PluginRun.repository_id == repo.id)
                                  .order_by(PluginRun.created_at.desc()).limit(40))).scalars().all())
    return {"items": [binding_json(b) for b in rows],
            "runs": [{"id": str(r.id), "binding_id": str(r.binding_id) if r.binding_id else None,
                      "plugin_id": r.plugin_id, "status": r.status, "summary": r.summary,
                      "counts": r.counts or {}, "error": r.error, "log": r.log,
                      "created_at": r.created_at.isoformat(),
                      "finished_at": r.finished_at.isoformat() if r.finished_at else None} for r in runs]}


class BindingIn(BaseModel):
    plugin_id: str = ""
    name: str = ""
    config: dict = {}
    enabled: bool = True
    sort_order: int | None = None


@router.post("/pipeline")
async def add_binding(repo_id: uuid.UUID, body: BindingIn, user: User = Depends(current_user),
                      db: AsyncSession = Depends(get_session)):
    repo = await ST.get_repository(db, user, repo_id, write=True)
    await R.ensure_defaults(db, repo)
    count = int((await db.execute(select(func.count()).select_from(PluginBinding).where(
        PluginBinding.repository_id == repo.id))).scalar_one())
    b = await P.create_binding(db, repo, plugin_id=body.plugin_id, name=body.name, config=body.config)
    b.sort_order = count * 10
    audit.record(db, "pipeline.bind", actor_id=user.id, target_type="repository", target_id=repo.id,
                 meta={"plugin": body.plugin_id})
    await db.commit()
    return binding_json(b)


@router.patch("/pipeline/{binding_id}")
async def patch_binding(repo_id: uuid.UUID, binding_id: uuid.UUID, body: BindingIn,
                        user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    repo = await ST.get_repository(db, user, repo_id, write=True)
    b = await P.get_binding(db, repo, binding_id)
    if body.name:
        b.name = body.name[:120]
    if body.config:
        b.config = P.coerce(P.spec_of(b.plugin_id), body.config)
    if body.sort_order is not None:
        b.sort_order = body.sort_order
    b.enabled = body.enabled
    await db.commit()
    return binding_json(b)


@router.delete("/pipeline/{binding_id}")
async def remove_binding(repo_id: uuid.UUID, binding_id: uuid.UUID, user: User = Depends(current_user),
                         db: AsyncSession = Depends(get_session)):
    repo = await ST.get_repository(db, user, repo_id, write=True)
    b = await P.get_binding(db, repo, binding_id)
    await db.execute(delete(PluginRun).where(PluginRun.binding_id == b.id))
    await db.delete(b)
    await db.commit()
    return {"ok": True}


@router.post("/pipeline/{binding_id}/run")
async def run_binding(repo_id: uuid.UUID, binding_id: uuid.UUID, user: User = Depends(current_user),
                      db: AsyncSession = Depends(get_session)):
    repo = await ST.get_repository(db, user, repo_id, write=True)
    b = await P.get_binding(db, repo, binding_id)
    run = await P.queue_run(db, repo, b, actor_id=user.id)
    audit.record(db, "pipeline.run", actor_id=user.id, target_type="plugin", target_id=b.plugin_id)
    await db.commit()
    return {"run_id": str(run.id), "status": run.status}
