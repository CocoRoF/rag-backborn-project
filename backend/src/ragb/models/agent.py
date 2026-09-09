from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ragb.db.base import Base, IdMixin, TimestampMixin
from ragb.models._types import JSONB, UUID, fk, owner_col


class Agent(Base, IdMixin, TimestampMixin):
    __tablename__ = "agents"
    owner_id: Mapped[uuid.UUID] = owner_col()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    system_prompt: Mapped[str] = mapped_column(Text, default="", server_default="")
    provider: Mapped[str] = mapped_column(String(32), default="claude_code", server_default="claude_code")
    model: Mapped[str] = mapped_column(String(120), default="", server_default="")
    temperature: Mapped[float] = mapped_column(Float, default=0.2, server_default="0.2")
    max_tokens: Mapped[int] = mapped_column(Integer, default=4096, server_default="4096")
    # off       — plain chat, no retrieval
    # auto      — retrieve once from the question, inject, answer (classic RAG)
    # agentic   — the model drives rag_search / rag_read / rag_browse itself
    retrieval_mode: Mapped[str] = mapped_column(String(16), default="agentic", server_default="agentic")
    top_k: Mapped[int] = mapped_column(Integer, default=8, server_default="8")
    # Per-agent overrides of the global retrieval settings (weights, rerank, expansion).
    settings: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class AgentRepository(Base):
    __tablename__ = "agent_repositories"
    __table_args__ = (UniqueConstraint("agent_id", "repository_id", name="uq_agent_repository"),)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    repository_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)


__all__ = ["Agent", "AgentRepository", "fk"]
