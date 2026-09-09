from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ragb.db.base import Base, IdMixin, TimestampMixin
from ragb.models._types import JSONB, UUID, Index


class SystemSetting(Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ModelCatalog(Base, IdMixin, TimestampMixin):
    __tablename__ = "model_catalog"
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    model_id: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), default="", server_default="")
    # What the Claude Code CLI is launched with *instead of* model_id. A pinned row must
    # leave this empty, or the console names one model while the CLI runs another.
    cli_alias: Mapped[str | None] = mapped_column(String(60))
    context_window: Mapped[int] = mapped_column(Integer, default=200_000, server_default="200000")
    max_output: Mapped[int] = mapped_column(Integer, default=8192, server_default="8192")
    supports_tools: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    supports_vision: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    sort_order: Mapped[int] = mapped_column(Integer, default=100, server_default="100")


class AuditLog(Base, IdMixin):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_created", "created_at"),)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    actor_kind: Mapped[str] = mapped_column(String(16), default="user", server_default="user")
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(80))
    ip: Mapped[str | None] = mapped_column(String(64))
    ua: Mapped[str] = mapped_column(String(400), default="", server_default="")
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Job(Base, IdMixin):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_claim", "status", "run_at", "priority"),
        Index("ix_jobs_dedupe_active", "dedupe_key", unique=True,
              postgresql_where="dedupe_key IS NOT NULL AND status IN ('queued', 'running')"),
    )
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(String(16), default="queued", server_default="queued")
    priority: Mapped[int] = mapped_column(Integer, default=5, server_default="5")
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, server_default="5")
    locked_by: Mapped[str | None] = mapped_column(String(80))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict | None] = mapped_column(JSONB)
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LlmCall(Base, IdMixin):
    """One model call. The admin console's cost/latency view reads nothing else."""
    __tablename__ = "llm_calls"
    __table_args__ = (Index("ix_llm_calls_created", "created_at"),)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    purpose: Mapped[str] = mapped_column(String(32), default="chat", server_default="chat")
    provider: Mapped[str] = mapped_column(String(32), default="", server_default="")
    model: Mapped[str] = mapped_column(String(120), default="", server_default="")
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    tool_calls: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(16), default="ok", server_default="ok")
    error: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RetrievalLog(Base, IdMixin):
    """Why the answer cited what it cited. This is the tuning surface for the RAG settings."""
    __tablename__ = "retrieval_logs"
    __table_args__ = (Index("ix_retrieval_logs_created", "created_at"),)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    query: Mapped[str] = mapped_column(Text, default="", server_default="")
    repository_ids: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    legs: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")   # leg -> hits
    candidates: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    returned: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    top_score: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    reranked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    passages: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = ["SystemSetting", "ModelCatalog", "AuditLog", "Job", "LlmCall", "RetrievalLog"]
