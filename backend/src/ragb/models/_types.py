from __future__ import annotations

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Index
from sqlalchemy.dialects.postgresql import CITEXT, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

# One fixed column width for every embedding. Providers disagree on dimensionality, so
# vectors are padded/truncated on the way in (providers.embedding.pad) — that keeps a
# provider switch from being a schema migration.
EMBED_DIM = 1536


def fk(table: str, nullable: bool = False, ondelete: str = "CASCADE", index: bool = True) -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey(f"{table}.id", ondelete=ondelete), nullable=nullable, index=index)


def owner_col(nullable: bool = False) -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=nullable, index=True)


__all__ = ["Vector", "CITEXT", "JSONB", "UUID", "Index", "EMBED_DIM", "fk", "owner_col"]
