"""Add polymarket partner attribution ledger.

Revision ID: a1b2c3d4e5f6
Revises: d1e2f3a4b5c6
Create Date: 2026-10-24 00:00:00.000000

Stores one row per attributed surface event. Wallets and builder codes are
salted SHA-256 hashes only — no raw PII. ``(partner, idem_key)`` is unique so
ingest replays collapse instead of double-counting earnings. Every statement
is idempotent so this forward migration is safe to re-run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = """
CREATE TABLE IF NOT EXISTS poly_attribution_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    partner VARCHAR(64) NOT NULL,
    campaign VARCHAR(64) NOT NULL,
    surface VARCHAR(16) NOT NULL,
    event VARCHAR(16) NOT NULL,
    market_id VARCHAR(128),
    wallet_hash VARCHAR(64),
    builder_code_hash VARCHAR(64),
    idem_key VARCHAR(128) NOT NULL,
    fingerprint VARCHAR(64) NOT NULL,
    ts TIMESTAMPTZ NOT NULL,
    value_usd NUMERIC(18, 6),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_poly_attr_partner_idem UNIQUE (partner, idem_key)
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_poly_attr_partner_ts ON poly_attribution_events (partner, ts)",
    "CREATE INDEX IF NOT EXISTS ix_poly_attr_partner_campaign ON poly_attribution_events (partner, campaign)",
    "CREATE INDEX IF NOT EXISTS ix_poly_attr_market ON poly_attribution_events (market_id)",
    "CREATE INDEX IF NOT EXISTS ix_poly_attr_wallet ON poly_attribution_events (wallet_hash)",
)


def upgrade() -> None:
    """Create the ledger table and its query indexes."""
    op.execute(_TABLE)
    for stmt in _INDEXES:
        op.execute(stmt)


def downgrade() -> None:
    """Drop the ledger table (indexes go with it)."""
    op.execute("DROP TABLE IF EXISTS poly_attribution_events")
