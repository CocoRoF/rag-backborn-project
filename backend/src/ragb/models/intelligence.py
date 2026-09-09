"""The business layer above retrieval.

A RAG store answers questions about documents. A *programme* — KOAT's AFCI is the case this
was designed against — needs the things retrieval alone has no vocabulary for: named
entities pulled out of those documents, a score with defensible dimensions, a link between
two entities, and a human who signed off on all three.

  Collection   기술 / 기업 / 수요 …  — a named set of records inside a repository
  Record       one entity, with free-form attributes and its own embedding
  RecordLink   기술 ↔ 기업 matching candidate, with evidence and a verdict
  Scorecard    the dimensions and weights of an evaluation (Opportunity Score)
  RecordScore  one record measured against one scorecard
  ReviewItem   Human-in-the-Loop: a person's verdict on any of the above

Every one of these carries `evidence` pointing back at the chunks it came from. An AFCI-style
system stands or falls on being able to answer "why does it say that", and evidence added
afterwards is evidence nobody can trust.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ragb.db.base import Base, IdMixin, TimestampMixin
from ragb.models._types import EMBED_DIM, JSONB, UUID, Index, Vector, fk


class Collection(Base, IdMixin, TimestampMixin):
    """A typed set of records. `key` is what plug-in configs refer to, so it is stable."""
    __tablename__ = "collections"
    __table_args__ = (UniqueConstraint("repository_id", "key", name="uq_collection_key"),)
    repository_id: Mapped[uuid.UUID] = fk("repositories")
    key: Mapped[str] = mapped_column(String(60), nullable=False)          # technology | company | demand | …
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    record_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class Record(Base, IdMixin, TimestampMixin):
    __tablename__ = "records"
    __table_args__ = (
        UniqueConstraint("collection_id", "external_id", name="uq_record_external_id"),
        Index("ix_records_repo_collection", "repository_id", "collection_id"),
        Index("ix_records_title_trgm", "title", postgresql_using="gin", postgresql_ops={"title": "gin_trgm_ops"}),
        Index("ix_records_embedding", "embedding", postgresql_using="hnsw",
              postgresql_with={"m": 16, "ef_construction": 64}, postgresql_ops={"embedding": "vector_cosine_ops"}),
    )
    collection_id: Mapped[uuid.UUID] = fk("collections")
    repository_id: Mapped[uuid.UUID] = fk("repositories")
    # Stable id from the source (CSV column, document path). Re-running a source updates in
    # place instead of duplicating, which is what makes ingestion repeatable.
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    labels: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    # The document this record was derived from, when there is one.
    source_node_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    evidence: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))


class RecordLink(Base, IdMixin, TimestampMixin):
    """A matching candidate. `status` is the Human-in-the-Loop verdict, not the AI's opinion."""
    __tablename__ = "record_links"
    __table_args__ = (
        UniqueConstraint("from_record_id", "to_record_id", "kind", name="uq_record_link"),
        Index("ix_record_links_repo_status", "repository_id", "status"),
    )
    repository_id: Mapped[uuid.UUID] = fk("repositories")
    from_record_id: Mapped[uuid.UUID] = fk("records")
    to_record_id: Mapped[uuid.UUID] = fk("records")
    kind: Mapped[str] = mapped_column(String(40), default="match", server_default="match")
    score: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    rerank_score: Mapped[float | None] = mapped_column(Float)
    rationale: Mapped[str] = mapped_column(Text, default="", server_default="")
    evidence: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    status: Mapped[str] = mapped_column(String(16), default="candidate", server_default="candidate")
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Scorecard(Base, IdMixin, TimestampMixin):
    """Dimensions + weights. The proposal's five (Technology Momentum, Market Attractiveness,
    Industry Demand, Commercialization Feasibility, Strategic Fit) are the shipped default —
    but they are rows, not code, because Delphi/AHP exists to change these numbers."""
    __tablename__ = "scorecards"
    repository_id: Mapped[uuid.UUID] = fk("repositories")
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    # [{key, label, weight, rubric}]
    dimensions: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class RecordScore(Base, IdMixin, TimestampMixin):
    __tablename__ = "record_scores"
    __table_args__ = (
        UniqueConstraint("record_id", "scorecard_id", name="uq_record_score"),
        Index("ix_record_scores_repo_total", "repository_id", "total"),
    )
    repository_id: Mapped[uuid.UUID] = fk("repositories")
    record_id: Mapped[uuid.UUID] = fk("records")
    scorecard_id: Mapped[uuid.UUID] = fk("scorecards")
    total: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    # {dimension_key: {score, rationale, evidence: [...]}}
    dimensions: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    tier: Mapped[str] = mapped_column(String(16), default="long", server_default="long")   # long | short | core
    status: Mapped[str] = mapped_column(String(16), default="candidate", server_default="candidate")
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewItem(Base, IdMixin):
    """One expert verdict. Kept as an append-only trail rather than a mutable flag: "누가
    언제 왜 그렇게 판단했는가" is the artifact a Human-in-the-Loop programme is buying."""
    __tablename__ = "review_items"
    __table_args__ = (Index("ix_review_items_repo_created", "repository_id", "created_at"),)
    repository_id: Mapped[uuid.UUID] = fk("repositories")
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)   # record | link | score
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)       # approved | rejected | revise
    comment: Mapped[str] = mapped_column(Text, default="", server_default="")
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    reviewer_role: Mapped[str] = mapped_column(String(40), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PluginBinding(Base, IdMixin, TimestampMixin):
    """A plug-in configured for one repository. The plug-in itself is code; this is the part
    an operator owns."""
    __tablename__ = "plugin_bindings"
    __table_args__ = (Index("ix_plugin_bindings_repo", "repository_id"),)
    repository_id: Mapped[uuid.UUID] = fk("repositories")
    plugin_id: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(120), default="", server_default="")
    config: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    sort_order: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str] = mapped_column(String(16), default="", server_default="")


class PluginRun(Base, IdMixin):
    __tablename__ = "plugin_runs"
    __table_args__ = (Index("ix_plugin_runs_repo_created", "repository_id", "created_at"),)
    repository_id: Mapped[uuid.UUID] = fk("repositories")
    binding_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    plugin_id: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", server_default="queued")
    config: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    counts: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    log: Mapped[str] = mapped_column(Text, default="", server_default="")
    error: Mapped[str] = mapped_column(Text, default="", server_default="")
    started_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


__all__ = ["Collection", "Record", "RecordLink", "Scorecard", "RecordScore", "ReviewItem",
           "PluginBinding", "PluginRun"]
