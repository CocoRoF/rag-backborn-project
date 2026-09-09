"""The RAG store: a repository holds a folder/file tree; every file grows a section tree and chunks.

Three levels, deliberately:

  StorageNode  — what a person sees and organises (folders and files, materialised ``path``)
  DocSection   — the document's own heading hierarchy, each with a summary + embedding
  Chunk        — the retrievable leaf

Search runs against sections *and* chunks. A question that names a chapter but shares no
words with any sentence in it ("배포 절차 알려줘" against a section titled "릴리스 파이프라인")
finds nothing at chunk level and everything at section level; the reverse is true for a
question about one specific value. Keeping both indexed is the point of the hierarchy.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Computed, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from ragb.db.base import Base, IdMixin, TimestampMixin
from ragb.models._types import EMBED_DIM, JSONB, UUID, Index, Vector, fk, owner_col


class Repository(Base, IdMixin, TimestampMixin):
    """A named RAG store. Agents bind to repositories, never to individual files."""
    __tablename__ = "repositories"
    __table_args__ = (UniqueConstraint("slug", name="uq_repositories_slug"),)
    owner_id: Mapped[uuid.UUID] = owner_col()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(140), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    # "shared" repositories are readable by every signed-in user; agents may bind to them.
    visibility: Mapped[str] = mapped_column(String(16), default="private", server_default="private")
    # Per-repository overrides of the global RAG settings (chunking, retrieval weights).
    settings: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    file_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    bytes_total: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")


class StorageNode(Base, IdMixin, TimestampMixin):
    """One folder or file. ``path`` is the materialised absolute path inside its repository.

    The path is stored rather than walked: subtree scoping is then a single ``path LIKE
    '/a/b/%'`` predicate that Postgres can index, and ordering by path yields the tree in
    display order without a recursive query.
    """
    __tablename__ = "storage_nodes"
    __table_args__ = (
        UniqueConstraint("repository_id", "parent_id", "name", name="uq_storage_nodes_sibling_name"),
        Index("ix_storage_nodes_repo_path", "repository_id", "path"),
        Index("ix_storage_nodes_path_trgm", "path", postgresql_using="gin", postgresql_ops={"path": "gin_trgm_ops"}),
    )
    repository_id: Mapped[uuid.UUID] = fk("repositories")
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)          # folder | file
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    depth: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    mime: Mapped[str] = mapped_column(String(160), default="", server_default="")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    sha256: Mapped[str] = mapped_column(String(64), default="", server_default="")
    storage_path: Mapped[str] = mapped_column(Text, default="", server_default="")   # s3://… or a local path
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    error: Mapped[str] = mapped_column(Text, default="", server_default="")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    section_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    embedding_model: Mapped[str] = mapped_column(String(160), default="", server_default="")
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    text_preview: Mapped[str] = mapped_column(Text, default="", server_default="")
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")


class DocSection(Base, IdMixin):
    """A heading in a document's own hierarchy, with the summary that makes it findable."""
    __tablename__ = "doc_sections"
    __table_args__ = (
        Index("ix_doc_sections_node_ordinal", "node_id", "ordinal"),
        Index("ix_doc_sections_embedding", "embedding", postgresql_using="hnsw",
              postgresql_with={"m": 16, "ef_construction": 64}, postgresql_ops={"embedding": "vector_cosine_ops"}),
    )
    node_id: Mapped[uuid.UUID] = fk("storage_nodes")
    repository_id: Mapped[uuid.UUID] = fk("repositories")
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    level: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    ordinal: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    title: Mapped[str] = mapped_column(Text, default="", server_default="")
    heading_path: Mapped[str] = mapped_column(Text, default="", server_default="")   # "A > B > C"
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    chunk_from: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    chunk_to: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    page: Mapped[int | None] = mapped_column(Integer)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))


class Chunk(Base, IdMixin):
    __tablename__ = "chunks"
    __table_args__ = (
        Index("ix_chunks_node_ordinal", "node_id", "ordinal"),
        Index("ix_chunks_repo", "repository_id"),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
        Index("ix_chunks_text_trgm", "text", postgresql_using="gin", postgresql_ops={"text": "gin_trgm_ops"}),
        Index("ix_chunks_embedding", "embedding", postgresql_using="hnsw",
              postgresql_with={"m": 16, "ef_construction": 64}, postgresql_ops={"embedding": "vector_cosine_ops"}),
    )
    node_id: Mapped[uuid.UUID] = fk("storage_nodes")
    section_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    repository_id: Mapped[uuid.UUID] = fk("repositories")
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    heading_path: Mapped[str] = mapped_column(Text, default="", server_default="")
    page: Mapped[int | None] = mapped_column(Integer)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))
    tsv: Mapped[str | None] = mapped_column(TSVECTOR, Computed("to_tsvector('simple', text)", persisted=True))


__all__ = ["Repository", "StorageNode", "DocSection", "Chunk", "Boolean", "TimestampMixin", "IdMixin"]
