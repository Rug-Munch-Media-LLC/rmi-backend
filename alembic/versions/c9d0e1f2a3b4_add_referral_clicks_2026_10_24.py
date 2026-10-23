"""Add referral_clicks attribution table (append-only, hashed identifiers).

Revision ID: c9d0e1f2a3b4
Revises: c8f9e0a1b2c3
Create Date: 2026-10-24 00:00:00.000000

One row per referral attribution event: which partner, which code, which
source surface (`scan_quick|scan_deep|bot|content|frontend`) and tier. User
and chat identifiers are stored ONLY as HMAC digests (`*_hash`); no IP, no
email, no username, no wallet address. Statements are idempotent so the
migration is safe against a DB where the table was bootstrapped by hand.

DOWN drops the table — the data is re-derivable from partner reports and is
not referenced by any other table.
"""

from __future__ import annotations

from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: str | None = "c8f9e0a1b2c3"
branch_labels: str | None = None
depends_on: str | None = None

_UP = (
    """
    CREATE TABLE IF NOT EXISTS referral_clicks (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        click_id      UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
        partner       VARCHAR(64) NOT NULL,
        code          VARCHAR(64) NOT NULL,
        surface       VARCHAR(32) NOT NULL,
        tier          VARCHAR(32) NOT NULL DEFAULT 'anonymous',
        referrer_hash VARCHAR(64),
        chat_hash     VARCHAR(64),
        landed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT ck_referral_clicks_surface CHECK (
            surface IN ('scan_quick', 'scan_deep', 'bot', 'content', 'frontend')
        ),
        CONSTRAINT ck_referral_clicks_tier CHECK (
            tier IN ('anonymous', 'free', 'pro', 'enterprise')
        )
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_referral_clicks_partner_code_time ON referral_clicks (partner, code, landed_at)",
    "CREATE INDEX IF NOT EXISTS ix_referral_clicks_surface_time ON referral_clicks (surface, landed_at)",
    "CREATE INDEX IF NOT EXISTS ix_referral_clicks_landed_at ON referral_clicks (landed_at)",
)


def upgrade() -> None:
    for statement in _UP:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS referral_clicks")
