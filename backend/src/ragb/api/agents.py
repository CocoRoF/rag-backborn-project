"""[채팅] — agents, their model + repository binding, conversations."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ragb.core.deps import client_ip, current_user
from ragb.core.errors import Forbidden, NotFound, ValidationFailed
from ragb.db.session import get_session
from ragb.models import Agent, AgentRepository, Conversation, Message, User
from ragb.services import audit, catalog
from ragb.services import settings as S
from ragb.services import storage as ST

router = APIRouter(prefix="/api/agents", tags=["agents"])
MODES = ("off", "auto", "agentic")


def agent_json(a: Agent, repo_ids: list[uuid.UUID] | None = None) -> dict:
    return {"id": str(a.id), "name": a.name, "description": a.description, "emoji": a.emoji,
            "system_prompt": a.system_prompt, "provider": a.provider, "model": a.model,
            "temperature": a.temperature, "max_tokens": a.max_tokens, "retrieval_mode": a.retrieval_mode,
            "top_k": a.top_k, "settings": a.settings or {}, "enabled": a.enabled,
            "repository_ids": [str(r) for r in (repo_ids or [])],
            "created_at": a.created_at.isoformat() if a.created_at else None}


class AgentIn(BaseModel):
    name: str
    description: str = ""
    emoji: str = "🤖"
    system_prompt: str = ""
    provider: str = ""
    model: str = ""
    temperature: float = 0.2
    max_tokens: int = 4096
    retrieval_mode: str = ""
    top_k: int = 8
    settings: dict = {}
    repository_ids: list[uuid.UUID] = []


async def _owned(db: AsyncSession, user: User, agent_id: uuid.UUID) -> Agent:
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise NotFound("에이전트를 찾을 수 없습니다", code="agent_not_found")
    if agent.owner_id != user.id and not user.is_admin:
        raise Forbidden("이 에이전트에 대한 권한이 없습니다", code="agent_forbidden")
    return agent


@router.get("")
async def list_agents(user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    agents = list((await db.execute(select(Agent).where(Agent.owner_id == user.id)
                                    .order_by(Agent.created_at.desc()))).scalars().all())
    bindings = (await db.execute(select(AgentRepository).where(
        AgentRepository.agent_id.in_([a.id for a in agents] or [uuid.uuid4()])))).scalars().all()
    by_agent: dict[uuid.UUID, list[uuid.UUID]] = {}
    for b in bindings:
        by_agent.setdefault(b.agent_id, []).append(b.repository_id)
    return {"items": [agent_json(a, by_agent.get(a.id, [])) for a in agents]}


@router.get("/options")
async def options(user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    """Everything the [새 에이전트] form needs: models to pick, repositories to bind."""
    models = await catalog.list_models(db, enabled_only=True)
    default = await catalog.default_model(db)
    repos = await ST.visible_repositories(db, user)
    return {
        "models": [{"provider": m.provider, "model_id": m.model_id, "display_name": m.display_name,
                    "context_window": m.context_window, "supports_tools": m.supports_tools,
                    "is_default": m.is_default} for m in models],
        "default": {"provider": default.provider, "model_id": default.model_id} if default else None,
        "repositories": [{"id": str(r.id), "name": r.name, "file_count": r.file_count,
                          "chunk_count": r.chunk_count, "visibility": r.visibility} for r in repos],
        "default_retrieval_mode": await S.get(db, "chat.default_retrieval_mode"),
    }


@router.post("")
async def create_agent(body: AgentIn, request: Request, user: User = Depends(current_user),
                       db: AsyncSession = Depends(get_session)):
    if not body.name.strip():
        raise ValidationFailed("이름이 필요합니다", code="name_required")
    row, _ = await catalog.resolve(db, body.provider or "claude_code", body.model or "")
    if row is None:
        raise ValidationFailed("사용 가능한 모델이 없습니다. 관리자에게 문의하세요", code="no_model")
    mode = body.retrieval_mode if body.retrieval_mode in MODES else await S.get(db, "chat.default_retrieval_mode")
    agent = Agent(owner_id=user.id, name=body.name[:120], description=body.description[:2000],
                  emoji=(body.emoji or "🤖")[:8], system_prompt=body.system_prompt[:8000],
                  provider=row.provider, model=row.model_id, temperature=max(0.0, min(2.0, body.temperature)),
                  max_tokens=max(256, min(64000, body.max_tokens)), retrieval_mode=mode,
                  top_k=max(1, min(20, body.top_k)), settings=body.settings or {})
    db.add(agent)
    await db.flush()
    repo_ids = await ST.set_agent_repositories(db, user, agent, body.repository_ids)
    audit.record(db, "agent.create", actor_id=user.id, target_type="agent", target_id=agent.id,
                 ip=client_ip(request), meta={"name": agent.name, "model": agent.model})
    await db.commit()
    return agent_json(agent, repo_ids)


@router.get("/{agent_id}")
async def get_agent(agent_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    agent = await _owned(db, user, agent_id)
    return agent_json(agent, await ST.agent_repository_ids(db, agent.id))


@router.patch("/{agent_id}")
async def update_agent(agent_id: uuid.UUID, body: AgentIn, user: User = Depends(current_user),
                       db: AsyncSession = Depends(get_session)):
    agent = await _owned(db, user, agent_id)
    row, _ = await catalog.resolve(db, body.provider or agent.provider, body.model or agent.model)
    agent.name = body.name[:120] or agent.name
    agent.description, agent.emoji = body.description[:2000], (body.emoji or agent.emoji)[:8]
    agent.system_prompt = body.system_prompt[:8000]
    if row is not None:
        agent.provider, agent.model = row.provider, row.model_id
    agent.temperature = max(0.0, min(2.0, body.temperature))
    agent.max_tokens = max(256, min(64000, body.max_tokens))
    if body.retrieval_mode in MODES:
        agent.retrieval_mode = body.retrieval_mode
    agent.top_k = max(1, min(20, body.top_k))
    agent.settings = body.settings or {}
    repo_ids = await ST.set_agent_repositories(db, user, agent, body.repository_ids)
    await db.commit()
    return agent_json(agent, repo_ids)


@router.delete("/{agent_id}")
async def delete_agent(agent_id: uuid.UUID, user: User = Depends(current_user),
                       db: AsyncSession = Depends(get_session)):
    agent = await _owned(db, user, agent_id)
    await db.execute(delete(AgentRepository).where(AgentRepository.agent_id == agent.id))
    await db.delete(agent)
    await db.commit()
    return {"ok": True}


# ── conversations ───────────────────────────────────────────────────────────────

def conv_json(c: Conversation) -> dict:
    return {"id": str(c.id), "agent_id": str(c.agent_id), "title": c.title or "새 대화",
            "message_count": c.message_count,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None}


@router.get("/{agent_id}/conversations")
async def list_conversations(agent_id: uuid.UUID, user: User = Depends(current_user),
                             db: AsyncSession = Depends(get_session)):
    await _owned(db, user, agent_id)
    rows = list((await db.execute(select(Conversation).where(
        Conversation.agent_id == agent_id, Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc()).limit(100))).scalars().all())
    return {"items": [conv_json(c) for c in rows]}


@router.post("/{agent_id}/conversations")
async def create_conversation(agent_id: uuid.UUID, user: User = Depends(current_user),
                              db: AsyncSession = Depends(get_session)):
    await _owned(db, user, agent_id)
    conv = Conversation(user_id=user.id, agent_id=agent_id, title="")
    db.add(conv)
    await db.commit()
    return conv_json(conv)


@router.get("/{agent_id}/conversations/{conversation_id}")
async def get_conversation(agent_id: uuid.UUID, conversation_id: uuid.UUID, user: User = Depends(current_user),
                           db: AsyncSession = Depends(get_session)):
    await _owned(db, user, agent_id)
    conv = await db.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user.id or conv.agent_id != agent_id:
        raise NotFound("대화를 찾을 수 없습니다", code="conversation_not_found")
    rows = list((await db.execute(select(Message).where(Message.conversation_id == conv.id)
                                  .order_by(Message.created_at).limit(400))).scalars().all())
    return {"conversation": conv_json(conv), "messages": [
        {"id": str(m.id), "role": m.role, "content": m.content, "citations": m.citations or [],
         "tool_events": m.tool_events or [], "error": m.error, "latency_ms": m.latency_ms,
         "created_at": m.created_at.isoformat()} for m in rows]}


@router.delete("/{agent_id}/conversations/{conversation_id}")
async def delete_conversation(agent_id: uuid.UUID, conversation_id: uuid.UUID, user: User = Depends(current_user),
                              db: AsyncSession = Depends(get_session)):
    conv = await db.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user.id:
        raise NotFound("대화를 찾을 수 없습니다", code="conversation_not_found")
    await db.delete(conv)
    await db.commit()
    return {"ok": True}
