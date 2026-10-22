"""add news_items.source_weight (effective rank weight)

Revision ID: b7c8d9e0f1a2
Revises: e4f5a6b7c8d9
Create Date: 2026-10-23 21:10:00.000000

Why: the Telegram/web/X intake lanes each carried a *channel* trust in
``source_trust`` but had nowhere to record the effective per-item rank weight
(channel trust x post rubric). Rugbrain ranks on that product, and without a
column every consumer re-derived it differently. Nullable + no default, so the
change is catalog-only on Postgres and old readers ignore it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b7c8d9e0f1a2"
down_revision: str | Sequence[str] | None = "e4f5a6b7c8d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable effective-weight column."""
    op.add_column("news_items", sa.Column("source_weight", sa.Float(), nullable=True))


def downgrade() -> None:
    """Drop the column (no data is preserved)."""
    op.drop_column("news_items", "source_weight")
