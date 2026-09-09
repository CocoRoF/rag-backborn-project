from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from ragb.db.base import Base, IdMixin, TimestampMixin
from ragb.models._types import CITEXT, JSONB, UUID, owner_col


class User(Base, IdMixin, TimestampMixin):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), default="", server_default="")
    password_hash: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="user", server_default="user")
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    prefs: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class RefreshToken(Base, IdMixin):
    __tablename__ = "refresh_tokens"
    user_id: Mapped[uuid.UUID] = owner_col()
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ua: Mapped[str] = mapped_column(String(300), default="", server_default="")
    ip: Mapped[str] = mapped_column(String(64), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = ["User", "RefreshToken", "UUID", "Boolean"]
