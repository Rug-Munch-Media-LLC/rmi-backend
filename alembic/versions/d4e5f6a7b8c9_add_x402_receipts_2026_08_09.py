"""add x402_receipts table

Revision ID: d4e5f6a7b8c9
Revises: merge_heads_2026_08_07
Create Date: 2026-08-09 00:45:00.000000

Creates the x402_receipts table (T34 payment ledger) that the x402
middleware writes to but that was previously only defined in
app/catalog/init_schema.py SCHEMA — which is never run against the
production database. This migration makes the table exist wherever
``alembic upgrade head`` is executed (CI + deploy).

Idempotent DDL (CREATE TABLE IF NOT EXISTS) matches init_schema so
both paths can coexist.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "merge_heads_2026_08_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create x402_receipts table + indexes."""
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS x402_receipts (
            tx_hash     TEXT PRIMARY KEY,
            agent_id    TEXT,
            tool        TEXT NOT NULL,
            amount_usd  REAL NOT NULL,
            chain       TEXT,
            paid_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            tier        TEXT
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_x402_paid_at ON x402_receipts(paid_at DESC);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_x402_tool ON x402_receipts(tool);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_x402_agent ON x402_receipts(agent_id);")


def downgrade() -> None:
    """Drop x402_receipts table + indexes."""
    op.execute("DROP INDEX IF EXISTS idx_x402_agent;")
    op.execute("DROP INDEX IF EXISTS idx_x402_tool;")
    op.execute("DROP INDEX IF EXISTS idx_x402_paid_at;")
    op.execute("DROP TABLE IF EXISTS x402_receipts;")
