"""Add nullable image_url column to news_items.

Revision ID: cab99e895e61
Revises: a5b6c7d8e9f0
Create Date: 2026-08-16 03:31:51.688495

Column (T28 news feed):
  news_items.image_url - best-effort article hero image (inline image link or
  the source page's og:image, populated by the TG news sweep). Nullable; the
  website feed + RAG surface it when present.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "cab99e895e61"
down_revision: str | Sequence[str] | None = "a5b6c7d8e9f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add news_items.image_url (nullable)."""
    op.add_column("news_items", sa.Column("image_url", sa.Text(), nullable=True))


def downgrade() -> None:
    """Drop news_items.image_url."""
    op.drop_column("news_items", "image_url")
