"""System settings: DB-backed, admin-owned, secrets encrypted at rest, short in-process cache."""
from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragb.core.redact import mask
from ragb.core.security import decrypt, encrypt
from ragb.models import SystemSetting

# key -> (default, is_secret)
DEFAULTS: dict[str, tuple[Any, bool]] = {
    "branding.service_name": ("RAG Backborn", False),
    "branding.tagline": ("계층형 파일 저장소 위에서 동작하는 RAG 챗봇 백본", False),
    "signup.mode": ("open", False),                       # open | closed

    # ── providers (public APIs only) ──────────────────────────────────────────
    "providers.claude_code.auth_mode": ("oauth", False),  # oauth | api_key | setup_token
    "providers.claude_code.credentials_json": ("", True),
    "providers.claude_code.setup_token": ("", True),
    "providers.anthropic.api_key": ("", True),
    "providers.openai.api_key": ("", True),
    "providers.google.api_key": ("", True),
    "providers.voyage.api_key": ("", True),
    "providers.status": ({}, False),

    # ── embedding ─────────────────────────────────────────────────────────────
    "embedding.provider": ("openai", False),              # openai | gemini | voyage | hash
    "embedding.model": ("text-embedding-3-small", False),
    "embedding.dim": (1536, False),
    "embedding.batch_size": (64, False),

    # ── chunking ──────────────────────────────────────────────────────────────
    "rag.chunk_tokens": (700, False),
    "rag.chunk_overlap": (100, False),
    "rag.chunk_max_tokens": (1000, False),

    # ── indexing ──────────────────────────────────────────────────────────────
    # Section summaries are what makes the hierarchical leg semantic rather than lexical.
    # They cost one cheap model call per section, so they are a switch, not a given.
    "rag.section_summary_enabled": (True, False),
    "rag.section_summary_provider": ("claude_code", False),
    "rag.section_summary_model": ("claude-haiku-4-5-20251001", False),
    "rag.section_summary_max_sections": (60, False),

    # ── retrieval ─────────────────────────────────────────────────────────────
    "rag.top_k": (8, False),
    "rag.candidates_per_leg": (24, False),
    "rag.rrf_k": (60, False),
    "rag.weight_vector": (1.0, False),      # dense chunk search
    "rag.weight_section": (0.8, False),     # dense section-summary search (the hierarchy leg)
    "rag.weight_lexical": (0.7, False),     # tsvector
    "rag.weight_trigram": (0.5, False),     # ILIKE + similarity (Korean agglutination)
    "rag.weight_path": (0.4, False),        # file name / folder path match
    "rag.min_vector_score": (0.15, False),
    "rag.neighbor_window": (1, False),      # merge ±N adjacent chunks into one passage
    "rag.max_per_document": (3, False),     # diversity cap
    "rag.context_token_budget": (6000, False),
    "rag.query_expansion": (False, False),  # LLM rewrites/expands the query first
    "rag.rerank_enabled": (False, False),
    "rag.rerank_provider": ("voyage", False),   # voyage | llm
    "rag.rerank_model": ("rerank-2", False),
    "rag.rerank_candidates": (30, False),

    # ── answering ─────────────────────────────────────────────────────────────
    "chat.default_retrieval_mode": ("agentic", False),
    "chat.max_tool_rounds": (6, False),
    "chat.history_messages": (12, False),

    "log.retention_days": (30, False),
}

_cache: dict[str, tuple[float, Any]] = {}
_TTL = 15.0


def is_secret(key: str) -> bool:
    return DEFAULTS.get(key, (None, False))[1]


def default_for(key: str) -> Any:
    return DEFAULTS.get(key, (None, False))[0]


async def get(db: AsyncSession, key: str, *, use_cache: bool = True) -> Any:
    now = time.monotonic()
    if use_cache and key in _cache and _cache[key][0] > now:
        return _cache[key][1]
    row = await db.get(SystemSetting, key)
    if row is None:
        value = default_for(key)
    else:
        raw = row.value.get("v") if isinstance(row.value, dict) else row.value
        value = decrypt(raw) if (row.is_secret and raw) else raw
    _cache[key] = (now + _TTL, value)
    return value


async def get_many(db: AsyncSession, prefix: str = "") -> dict[str, Any]:
    out = {k: default_for(k) for k in DEFAULTS if k.startswith(prefix)}
    rows = (await db.execute(select(SystemSetting).where(SystemSetting.key.like(prefix + "%")))).scalars().all()
    for row in rows:
        raw = row.value.get("v") if isinstance(row.value, dict) else row.value
        out[row.key] = decrypt(raw) if (row.is_secret and raw) else raw
    return out


async def put(db: AsyncSession, key: str, value: Any, *, updated_by: uuid.UUID | None = None) -> None:
    secret = is_secret(key)
    stored = encrypt(str(value)) if (secret and value) else value
    row = await db.get(SystemSetting, key)
    if row is None:
        row = SystemSetting(key=key, value={"v": stored}, is_secret=secret, updated_at=datetime.now(UTC))
        db.add(row)
    else:
        row.value = {"v": stored}
        row.is_secret = secret
        row.updated_at = datetime.now(UTC)
    row.updated_by = updated_by
    _cache.pop(key, None)


def invalidate(prefix: str = "") -> None:
    for k in list(_cache):
        if k.startswith(prefix):
            _cache.pop(k, None)


async def public_view(db: AsyncSession, prefix: str = "") -> dict[str, Any]:
    """What the admin console GETs: secrets become {has_value, masked}, never the value."""
    values = await get_many(db, prefix)
    out: dict[str, Any] = {}
    for k, v in values.items():
        out[k] = {"has_value": bool(v), "masked": mask(str(v)) if v else ""} if is_secret(k) else v
    return out


async def rag_config(db: AsyncSession, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Effective retrieval configuration: globals, then repository/agent overrides on top."""
    cfg = {k.removeprefix("rag."): v for k, v in (await get_many(db, "rag.")).items()}
    for k, v in (overrides or {}).items():
        if v is not None and k in cfg:
            cfg[k] = v
    return cfg
