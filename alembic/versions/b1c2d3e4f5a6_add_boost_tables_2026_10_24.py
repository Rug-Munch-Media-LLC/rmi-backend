"""Add paid boost inventory tables.

Revision ID: b1c2d3e4f5a6
Revises: c9d0e1f2a3b4
Create Date: 2026-10-24

``boost_purchases`` — our own paid placements (never external promo feeds).
``boost_events`` — append-only audit trail.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "b1c2d3e4f5a6"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "boost_purchases",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("chain", sa.String(length=32), nullable=False),
        sa.Column("token_address", sa.String(length=128), nullable=False),
        sa.Column("plan_id", sa.String(length=32), nullable=False),
        sa.Column("duration_hours", sa.Integer(), nullable=False),
        sa.Column("price_amount", sa.Numeric(18, 6), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("payment_provider", sa.String(length=32), nullable=False),
        sa.Column("payment_ref", sa.String(length=256), nullable=True),
        sa.Column("buyer_ref", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("entitlement_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("payment_ref", name="uq_boost_purchases_payment_ref"),
    )
    op.create_index("ix_boost_chain_status", "boost_purchases", ["chain", "status"])
    op.create_index("ix_boost_chain_token", "boost_purchases", ["chain", "token_address"])
    op.create_index("ix_boost_ends_status", "boost_purchases", ["ends_at", "status"])

    op.create_table(
        "boost_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column(
            "boost_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("boost_purchases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False, server_default="api"),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_boost_events_boost", "boost_events", ["boost_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_boost_events_boost", table_name="boost_events")
    op.drop_table("boost_events")
    op.drop_index("ix_boost_ends_status", table_name="boost_purchases")
    op.drop_index("ix_boost_chain_token", table_name="boost_purchases")
    op.drop_index("ix_boost_chain_status", table_name="boost_purchases")
    op.drop_table("boost_purchases")
