"""Add kol_wallet KOL attribution table.

Revision ID: a5b6c7d8e9f0
Revises: c1d2e3f4a5b6
Create Date: 2026-08-13 00:00:00.000000

Table (KOL-TRACKING-DESIGN.md §2.5, §4):
  kol_wallet - KOL -> wallet links with confidence tier + evidence.
  UNIQUE (kol_id, chain, address); no FK to kol_account yet (table lands
  in build-order item #1 — see docs/KOL-TRACKING-DESIGN.md §7).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "a5b6c7d8e9f0"
down_revision: str | Sequence[str] | None = "c1d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create kol_wallet."""
    op.create_table(
        "kol_wallet",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("kol_id", sa.String(128), nullable=False),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("address", sa.String(128), nullable=False),
        sa.Column(
            "attribution_method",
            sa.String(32),
            nullable=False,
            server_default="self_disclosed",
        ),
        sa.Column(
            "confidence",
            sa.String(32),
            nullable=False,
            server_default="probabilistic",
        ),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_kol_wallet"),
        sa.UniqueConstraint("kol_id", "chain", "address", name="uq_kol_wallet_kol_chain_addr"),
    )
    op.create_index("ix_kol_wallet_kol_id", "kol_wallet", ["kol_id"])
    op.create_index("ix_kol_wallet_address", "kol_wallet", ["address"])


def downgrade() -> None:
    """Drop kol_wallet."""
    op.drop_index("ix_kol_wallet_address", table_name="kol_wallet")
    op.drop_index("ix_kol_wallet_kol_id", table_name="kol_wallet")
    op.drop_table("kol_wallet")
