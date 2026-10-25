"""content-quality: allow unknown published_at

An item whose feed carries no machine-readable timestamp has UNKNOWN
freshness. Stamping it with the ingest clock fabricates freshness.

Revision ID: c19a1b2d3e4f
Revises: merge_heads_2026_07_27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c19a1b2d3e4f"
down_revision = "merge_heads_2026_07_27"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("news_items", "published_at", existing_type=sa.DateTime(timezone=True), nullable=True)


def downgrade() -> None:
    # Rows with unknown freshness cannot satisfy NOT NULL; they are stamped
    # with ingested_at so the downgrade stays reversible (documented loss).
    op.execute("UPDATE news_items SET published_at = ingested_at WHERE published_at IS NULL")
    op.alter_column("news_items", "published_at", existing_type=sa.DateTime(timezone=True), nullable=False)
