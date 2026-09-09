"""drop agents.emoji

The agent icon was a decoration nobody chose and everybody saw. Removing the column rather
than hiding the field: a value the UI never shows and the API never returns is a third state
that only confuses whoever reads the model next.

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("agents", "emoji")


def downgrade() -> None:
    op.add_column("agents", sa.Column("emoji", sa.String(length=8), nullable=False, server_default="🤖"))
