"""[관리] — providers, models, embedding + RAG settings, users, logs."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ragb.core.deps import admin_user
from ragb.core.errors import NotFound, ValidationFailed
from ragb.db.session import get_session
from ragb.models import (
    AuditLog,
    Conversation,
    Job,
    LlmCall,
    Message,
    ModelCatalog,
    Repository,
    RetrievalLog,
    StorageNode,
    User,
)
from ragb.providers.llm import PROVIDERS
from ragb.services import accounts as A
from ragb.services import audit, catalog, claude_code, keycheck, objectstore, runtime
from ragb.services import settings as S

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(admin_user)])


# ── overview ────────────────────────────────────────────────────────────────────

@router.get("/overview")
async def overview(db: AsyncSession = Depends(get_session)):
    async def count(model) -> int:
        return int((await db.execute(select(func.count()).select_from(model))).scalar_one())
    since = datetime.now(UTC) - timedelta(days=1)
    calls_24h = (await db.execute(select(func.count(), func.coalesce(func.sum(LlmCall.tokens_in), 0),
                                         func.coalesce(func.sum(LlmCall.tokens_out), 0))
                                  .where(LlmCall.created_at >= since))).one()
    jobs = dict((await db.execute(select(Job.status, func.count()).group_by(Job.status))).all())
    files, bytes_total = (await db.execute(select(func.count(), func.coalesce(func.sum(StorageNode.size_bytes), 0))
                                           .where(StorageNode.kind == "file"))).one()
    failed = int((await db.execute(select(func.count()).select_from(StorageNode)
                                   .where(StorageNode.status == "failed"))).scalar_one())
    return {
        "users": await count(User), "agents": await _agents(db),
        "repositories": await count(Repository), "files": int(files), "bytes": int(bytes_total),
        "failed_files": failed, "conversations": await count(Conversation), "messages": await count(Message),
        "calls_24h": {"count": int(calls_24h[0]), "tokens_in": int(calls_24h[1]), "tokens_out": int(calls_24h[2])},
        "jobs": {k: int(v) for k, v in jobs.items()}, "active_turns": runtime.active(),
        "service_name": await S.get(db, "branding.service_name"),
    }


async def _agents(db: AsyncSession) -> int:
    from ragb.models import Agent
    return int((await db.execute(select(func.count()).select_from(Agent))).scalar_one())


@router.get("/health")
async def health(db: AsyncSession = Depends(get_session)):
    return {"storage": await objectstore.health(),
            "embedding": await keycheck.probe_embedding(db),
            "claude_code": await claude_code.status(db),
            "providers": {p: await keycheck.probe(db, p) for p in ("anthropic", "openai", "google", "voyage")}}


# ── settings ────────────────────────────────────────────────────────────────────

class SettingsIn(BaseModel):
    values: dict[str, Any]


@router.get("/settings")
async def get_settings_view(prefix: str = "", db: AsyncSession = Depends(get_session)):
    return {"values": await S.public_view(db, prefix), "defaults": {k: v[0] for k, v in S.DEFAULTS.items()},
            "secret_keys": [k for k, v in S.DEFAULTS.items() if v[1]]}


@router.put("/settings")
async def put_settings(body: SettingsIn, user: User = Depends(admin_user), db: AsyncSession = Depends(get_session)):
    changed = []
    for key, value in body.values.items():
        if key not in S.DEFAULTS:
            raise ValidationFailed(f"알 수 없는 설정 키: {key}", code="unknown_setting")
        # A masked secret coming back unchanged from the form must not overwrite the real one.
        if S.is_secret(key) and isinstance(value, str) and "…" in value:
            continue
        await S.put(db, key, value, updated_by=user.id)
        changed.append(key)
    audit.record(db, "settings.update", actor_id=user.id, meta={"keys": changed})
    await db.commit()
    S.invalidate()
    return {"updated": changed}


@router.post("/providers/{provider}/test")
async def test_provider(provider: str, db: AsyncSession = Depends(get_session)):
    if provider == "embedding":
        return await keycheck.probe_embedding(db)
    return await keycheck.probe(db, provider)


# ── claude code login ───────────────────────────────────────────────────────────

class LoginStart(BaseModel):
    console: bool = False


class LoginInput(BaseModel):
    text: str


@router.get("/claude/status")
async def claude_status(db: AsyncSession = Depends(get_session)):
    return await claude_code.status(db)


@router.post("/claude/login")
async def claude_login(body: LoginStart):
    return await claude_code.start_login(console=body.console)


@router.get("/claude/login")
async def claude_login_state():
    return claude_code.login_state()


@router.post("/claude/login/input")
async def claude_login_input(body: LoginInput, db: AsyncSession = Depends(get_session)):
    state = await claude_code.send_login_input(body.text)
    await claude_code.backup_credentials(db)
    await db.commit()
    return state


@router.delete("/claude/login")
async def claude_login_cancel():
    return await claude_code.cancel_login()


class CredentialsIn(BaseModel):
    credentials_json: str


@router.post("/claude/credentials")
async def claude_import(body: CredentialsIn, user: User = Depends(admin_user),
                        db: AsyncSession = Depends(get_session)):
    st = await claude_code.import_credentials(db, body.credentials_json, updated_by=user.id)
    audit.record(db, "claude.credentials_import", actor_id=user.id)
    await db.commit()
    return st


@router.post("/claude/backup")
async def claude_backup(db: AsyncSession = Depends(get_session)):
    saved = await claude_code.backup_credentials(db)
    await db.commit()
    return {"saved": saved}


# ── model catalog ───────────────────────────────────────────────────────────────

class ModelPatch(BaseModel):
    enabled: bool | None = None
    is_default: bool | None = None
    display_name: str | None = None


@router.get("/models")
async def list_models(db: AsyncSession = Depends(get_session)):
    rows = await catalog.list_models(db)
    return {"providers": list(PROVIDERS), "items": [
        {"id": str(m.id), "provider": m.provider, "model_id": m.model_id, "display_name": m.display_name,
         "cli_alias": m.cli_alias, "context_window": m.context_window, "max_output": m.max_output,
         "supports_tools": m.supports_tools, "enabled": m.enabled, "is_default": m.is_default,
         "sort_order": m.sort_order} for m in rows]}


@router.post("/models/seed")
async def seed_models(db: AsyncSession = Depends(get_session)):
    out = await catalog.seed(db)
    await db.commit()
    return out


@router.patch("/models/{model_id}")
async def patch_model(model_id: uuid.UUID, body: ModelPatch, db: AsyncSession = Depends(get_session)):
    row = await db.get(ModelCatalog, model_id)
    if row is None:
        raise NotFound("모델을 찾을 수 없습니다", code="model_not_found")
    if body.enabled is not None:
        row.enabled = body.enabled
    if body.display_name:
        row.display_name = body.display_name[:160]
    if body.is_default:
        await db.execute(ModelCatalog.__table__.update().values(is_default=False))
        row.is_default, row.enabled = True, True
    await db.commit()
    return {"ok": True}


# ── users ───────────────────────────────────────────────────────────────────────

class UserPatch(BaseModel):
    role: str | None = None
    status: str | None = None


@router.get("/users")
async def list_users(q: str = "", limit: int = 100, db: AsyncSession = Depends(get_session)):
    stmt = select(User).order_by(User.created_at.desc()).limit(min(limit, 300))
    if q:
        stmt = stmt.where(User.email.ilike(f"%{q}%") | User.name.ilike(f"%{q}%"))
    rows = list((await db.execute(stmt)).scalars().all())
    return {"items": [A.public_user(u) | {"last_login_at": u.last_login_at.isoformat() if u.last_login_at else None}
                      for u in rows]}


@router.patch("/users/{user_id}")
async def patch_user(user_id: uuid.UUID, body: UserPatch, actor: User = Depends(admin_user),
                     db: AsyncSession = Depends(get_session)):
    target = await db.get(User, user_id)
    if target is None:
        raise NotFound("사용자를 찾을 수 없습니다", code="user_not_found")
    if target.id == actor.id and (body.role == "user" or body.status == "disabled"):
        raise ValidationFailed("자기 자신의 권한은 낮출 수 없습니다", code="self_demote")
    if body.role in ("user", "admin"):
        target.role = body.role
    if body.status in ("active", "disabled"):
        target.status = body.status
    audit.record(db, "user.update", actor_id=actor.id, target_type="user", target_id=target.id,
                 meta={"role": target.role, "status": target.status})
    await db.commit()
    return A.public_user(target)


# ── logs ────────────────────────────────────────────────────────────────────────

@router.get("/logs/audit")
async def audit_logs(limit: int = 100, action: str = "", db: AsyncSession = Depends(get_session)):
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(min(limit, 500))
    if action:
        stmt = stmt.where(AuditLog.action.ilike(f"%{action}%"))
    rows = list((await db.execute(stmt)).scalars().all())
    return {"items": [{"id": str(r.id), "actor_id": str(r.actor_id) if r.actor_id else None,
                       "action": r.action, "target_type": r.target_type, "target_id": r.target_id,
                       "ip": r.ip, "meta": r.meta, "created_at": r.created_at.isoformat()} for r in rows]}


@router.get("/logs/jobs")
async def job_logs(limit: int = 100, status: str = "", db: AsyncSession = Depends(get_session)):
    stmt = select(Job).order_by(Job.created_at.desc()).limit(min(limit, 500))
    if status:
        stmt = stmt.where(Job.status == status)
    rows = list((await db.execute(stmt)).scalars().all())
    return {"items": [{"id": str(r.id), "kind": r.kind, "status": r.status, "attempts": r.attempts,
                       "payload": r.payload, "last_error": r.last_error, "result": r.result,
                       "created_at": r.created_at.isoformat(),
                       "finished_at": r.finished_at.isoformat() if r.finished_at else None} for r in rows]}


@router.post("/logs/jobs/{job_id}/retry")
async def retry_job(job_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    job = await db.get(Job, job_id)
    if job is None:
        raise NotFound("작업을 찾을 수 없습니다", code="job_not_found")
    job.status, job.attempts, job.run_at, job.last_error = "queued", 0, datetime.now(UTC), None
    await db.commit()
    return {"ok": True}


@router.get("/logs/calls")
async def call_logs(limit: int = 100, db: AsyncSession = Depends(get_session)):
    rows = list((await db.execute(select(LlmCall).order_by(LlmCall.created_at.desc())
                                  .limit(min(limit, 500)))).scalars().all())
    return {"items": [{"id": str(r.id), "provider": r.provider, "model": r.model, "purpose": r.purpose,
                       "tokens_in": r.tokens_in, "tokens_out": r.tokens_out, "tool_calls": r.tool_calls,
                       "latency_ms": r.latency_ms, "status": r.status, "error": r.error,
                       "agent_id": str(r.agent_id) if r.agent_id else None,
                       "created_at": r.created_at.isoformat()} for r in rows]}


@router.get("/logs/retrieval")
async def retrieval_logs(limit: int = 100, db: AsyncSession = Depends(get_session)):
    rows = list((await db.execute(select(RetrievalLog).order_by(RetrievalLog.created_at.desc())
                                  .limit(min(limit, 500)))).scalars().all())
    return {"items": [{"id": str(r.id), "query": r.query, "legs": r.legs, "candidates": r.candidates,
                       "returned": r.returned, "top_score": r.top_score, "reranked": r.reranked,
                       "latency_ms": r.latency_ms, "passages": r.passages,
                       "created_at": r.created_at.isoformat()} for r in rows]}


@router.delete("/logs")
async def purge_logs(days: int = Query(30, ge=1, le=365), user: User = Depends(admin_user),
                     db: AsyncSession = Depends(get_session)):
    cutoff = datetime.now(UTC) - timedelta(days=days)
    removed = {}
    for model in (AuditLog, LlmCall, RetrievalLog):
        r = await db.execute(delete(model).where(model.created_at < cutoff))
        removed[model.__tablename__] = int(r.rowcount or 0)
    r = await db.execute(delete(Job).where(Job.status.in_(("done", "dead")), Job.created_at < cutoff))
    removed["jobs"] = int(r.rowcount or 0)
    audit.record(db, "logs.purge", actor_id=user.id, meta=removed)
    await db.commit()
    return removed


# ── repositories (all of them, not just mine) ───────────────────────────────────

@router.get("/repositories")
async def all_repositories(db: AsyncSession = Depends(get_session)):
    rows = list((await db.execute(select(Repository).order_by(Repository.created_at.desc()))).scalars().all())
    return {"items": [{"id": str(r.id), "name": r.name, "owner_id": str(r.owner_id), "visibility": r.visibility,
                       "file_count": r.file_count, "chunk_count": r.chunk_count, "bytes_total": r.bytes_total,
                       "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]}
