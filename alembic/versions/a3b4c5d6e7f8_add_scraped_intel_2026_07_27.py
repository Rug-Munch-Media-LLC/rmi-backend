"""Add scraped_intel table for Pryscraper DataBus bridge.

Table:
  scraped_intel — stores scraped intelligence from Pryscraper for downstream
  consumption by DataBus, scanner pipeline, RAG, and analytics.

Revision ID: a3b4c5d6e7f8
Revises: f7e8d9c0b1a2
Create Date: 2026-07-27 12:00:00.000000
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "a3b4c5d6e7f8"
down_revision: str | Sequence[str] | None = "f7e8d9c0b1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the scraped_intel table."""
    op.create_table(
        "scraped_intel",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("source", sa.String(64), nullable=False, index=True),
        sa.Column("data_type", sa.String(64), nullable=False, index=True),
        sa.Column("content", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("url", sa.Text, nullable=True),
        sa.Column("token_address", sa.String(128), nullable=True, index=True),
        sa.Column("chain", sa.String(32), nullable=True, index=True),
        sa.Column("scraped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            index=True,
        ),
        sa.Column("error", sa.Text, nullable=True),
    )
    op.create_index(
        "ix_scraped_intel_lookup",
        "scraped_intel",
        ["data_type", "token_address", "chain"],
    )
    op.create_index(
        "ix_scraped_intel_source_type",
        "scraped_intel",
        ["source", "data_type"],
    )


def downgrade() -> None:
    """Drop the scraped_intel table."""
    op.drop_index("ix_scraped_intel_source_type", table_name="scraped_intel")
    op.drop_index("ix_scraped_intel_lookup", table_name="scraped_intel")
    op.drop_table("scraped_intel")
