"""intelligence layer: collections, records, links, scorecards, reviews, plug-in bindings/runs

Revision ID: 0002
Revises: 0001
"""
from alembic import op

from ragb.models import Base

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

NEW = ("collections", "records", "record_links", "scorecards", "record_scores",
       "review_items", "plugin_bindings", "plugin_runs")


def upgrade() -> None:
    conn = op.get_bind()
    Base.metadata.create_all(bind=conn, tables=[Base.metadata.tables[t] for t in NEW])


def downgrade() -> None:
    conn = op.get_bind()
    Base.metadata.drop_all(bind=conn, tables=[Base.metadata.tables[t] for t in reversed(NEW)])
