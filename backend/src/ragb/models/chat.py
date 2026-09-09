from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ragb.db.base import Base, IdMixin, TimestampMixin
from ragb.models._types import JSONB, Index, fk, owner_col


class Conversation(Base, IdMixin, TimestampMixin):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_user_updated", "user_id", "updated_at"),)
    user_id: Mapped[uuid.UUID] = owner_col()
    agent_id: Mapped[uuid.UUID] = fk("agents")
    title: Mapped[str] = mapped_column(String(200), default="", server_default="")
    message_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class Message(Base, IdMixin):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation_created", "conversation_id", "created_at"),)
    conversation_id: Mapped[uuid.UUID] = fk("conversations")
    role: Mapped[str] = mapped_column(String(16), nullable=False)     # user | assistant
    content: Mapped[str] = mapped_column(Text, default="", server_default="")
    thinking: Mapped[str] = mapped_column(Text, default="", server_default="")
    citations: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    tool_events: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = ["Conversation", "Message"]
