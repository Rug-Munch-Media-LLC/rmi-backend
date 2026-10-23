"""restore the missing ``scan_results`` table (idempotent)

Revision ID: c8f9e0a1b2c3
Revises: b7c8d9e0f1a2
Create Date: 2026-10-23 18:25:00.000000

Why: ``alembic_version`` on the ``rmi`` database claims 0001 -> 75c982fccbb8 ->
... -> b7c8d9e0f1a2 are applied, but **none** of the five tables created by
``75c982fccbb8`` (``alert_subscriptions``, ``labels``, ``payments``,
``scan_results``, ``users``) exist. The revision table was stamped from another
environment, so ``alembic upgrade head`` considers the work done and never
re-runs it. Every scanner write (``app/domains/scanner/unified/persistence.py``)
and the 30-minute ``rug_learning_sweep`` therefore fail with
``relation "scan_results" does not exist``.

This revision re-applies only the ``scan_results`` part of 0001, guarded, so it
is safe on a database that already has the table (fresh installs). Columns,
defaults and indexes mirror ``app/models/scan.py`` + ``0001_initial``; nothing
existing is altered or dropped.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c8f9e0a1b2c3"
down_revision: str | Sequence[str] | None = "b7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_exists(name: str) -> bool:
    """Return True when the table is already present (offline mode: False)."""
    ctx = op.get_context()
    if getattr(ctx, "as_sql", False):
        return False
    from sqlalchemy import inspect

    return inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    """Create ``scan_results`` and its indexes when absent."""
    if _table_exists("scan_results"):
        return

    op.create_table(
        "scan_results",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("token_address", sa.String(256), nullable=False),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(64), nullable=False, server_default=""),
        sa.Column("name", sa.String(256), nullable=False, server_default=""),
        sa.Column("safety_score", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("risk_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("risk_level", sa.String(16), nullable=False, server_default="low"),
        sa.Column("risk_flags", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("warnings", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("modules_run", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("extra", postgresql.JSONB(), nullable=True),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tier_required", sa.String(16), nullable=False, server_default="free"),
        sa.Column("confidence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_scan_results_token_address", "scan_results", ["token_address"])
    op.create_index("ix_scan_result_token_chain", "scan_results", ["token_address", "chain"])
    op.create_index("ix_scan_result_scanned_at", "scan_results", ["scanned_at"])
    op.create_index("ix_scan_result_risk_level", "scan_results", ["risk_level"])
    op.create_index("ix_scan_result_chain_risk", "scan_results", ["chain", "risk_level"])
    op.create_index("ix_scan_result_tier", "scan_results", ["tier_required"])


def downgrade() -> None:
    """Drop the table this revision (re)created."""
    if not _table_exists("scan_results"):
        return
    op.drop_index("ix_scan_result_tier", table_name="scan_results")
    op.drop_index("ix_scan_result_chain_risk", table_name="scan_results")
    op.drop_index("ix_scan_result_risk_level", table_name="scan_results")
    op.drop_index("ix_scan_result_scanned_at", table_name="scan_results")
    op.drop_index("ix_scan_result_token_chain", table_name="scan_results")
    op.drop_index("ix_scan_results_token_address", table_name="scan_results")
    op.drop_table("scan_results")
