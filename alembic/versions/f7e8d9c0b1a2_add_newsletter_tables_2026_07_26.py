"""Add newsletter system tables.

Revision ID: f7e8d9c0b1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-07-26 12:00:00.000000

Tables:
  newsletters          - Newsletter campaigns
  newsletter_subs      - Subscribers
  newsletter_payments  - Crypto payments
  newsletter_deliveries - Delivery tracking
  newsletter_crossposts - Cross-platform publishing records
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f7e8d9c0b1a2"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = ("e7f8a9b0c1d2",)


def upgrade() -> None:
    """Create all newsletter tables."""
    # ── newsletters ──────────────────────────────────────────────────
    op.create_table(
        "newsletters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("creator_id", sa.String(128), nullable=False, index=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("slug", sa.String(256), nullable=True, unique=True),
        sa.Column("content_html", sa.Text, nullable=False),
        sa.Column("content_text", sa.Text, nullable=True),
        sa.Column("excerpt", sa.Text, nullable=True),
        sa.Column("cover_image", sa.String(1024), nullable=True),
        sa.Column("tags", postgresql.JSON, nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft", index=True),
        sa.Column("publish_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("segment", sa.String(32), nullable=False, server_default="all"),
        sa.Column("subject_line", sa.String(256), nullable=True),
        sa.Column("preheader_text", sa.String(256), nullable=True),
        sa.Column("recurrence", sa.String(32), nullable=True),
        sa.Column("recurrence_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_recipients", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_opened", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_clicked", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_revenue", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("ghost_post_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_newsletter_creator_status", "newsletters", ["creator_id", "status"])
    op.create_index("ix_newsletter_scheduled", "newsletters", ["publish_at"])

    # ── newsletter_subs ──────────────────────────────────────────────
    op.create_table(
        "newsletter_subs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "newsletter_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("newsletters.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("email", sa.String(320), nullable=True, index=True),
        sa.Column("wallet_address", sa.String(128), nullable=True, index=True),
        sa.Column("protocol", sa.String(32), nullable=True),
        sa.Column("protocol_id", sa.String(256), nullable=True),
        sa.Column("tier", sa.String(32), nullable=False, server_default="free"),
        sa.Column("status", sa.String(32), nullable=False, server_default="active", index=True),
        sa.Column("subscription_tx_hash", sa.String(256), nullable=True),
        sa.Column("subscription_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("token_gate_holdings", postgresql.JSON, nullable=True),
        sa.Column("interests", postgresql.JSON, nullable=True),
        sa.Column("referrer", sa.String(256), nullable=True),
        sa.Column("source", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_newsletter_sub_email_newsletter", "newsletter_subs", ["email", "newsletter_id"])
    op.create_index("ix_newsletter_sub_wallet", "newsletter_subs", ["wallet_address", "newsletter_id"])

    # ── newsletter_payments ──────────────────────────────────────────
    op.create_table(
        "newsletter_payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "subscriber_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("newsletter_subs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("token", sa.String(32), nullable=False),
        sa.Column("amount", sa.String(64), nullable=False),
        sa.Column("usd_value", sa.Float, nullable=True),
        sa.Column("tx_hash", sa.String(256), nullable=False, unique=True, index=True),
        sa.Column("from_address", sa.String(128), nullable=False),
        sa.Column("block_number", sa.Integer, nullable=True),
        sa.Column("confirmations", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tier", sa.String(32), nullable=False),
        sa.Column("duration_days", sa.Integer, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("platform_fee_pct", sa.Float, nullable=False, server_default="10.0"),
        sa.Column("creator_amount", sa.String(64), nullable=True),
        sa.Column("platform_amount", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending", index=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_newsletter_payment_sub_tier", "newsletter_payments", ["subscriber_id", "tier"])

    # ── newsletter_deliveries ────────────────────────────────────────
    op.create_table(
        "newsletter_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "newsletter_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("newsletters.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "subscriber_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("newsletter_subs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("clicked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("open_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("click_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_newsletter_delivery_nl_subbed", "newsletter_deliveries", ["newsletter_id", "subscriber_id"])

    # ── newsletter_crossposts ────────────────────────────────────────
    op.create_table(
        "newsletter_crossposts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "newsletter_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("newsletters.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("platform_url", sa.String(1024), nullable=True),
        sa.Column("platform_id", sa.String(256), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("view_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("engagement_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_newsletter_crosspost_nl_platform", "newsletter_crossposts", ["newsletter_id", "platform"])


def downgrade() -> None:
    """Drop all newsletter tables."""
    op.drop_table("newsletter_crossposts")
    op.drop_table("newsletter_deliveries")
    op.drop_table("newsletter_payments")
    op.drop_table("newsletter_subs")
    op.drop_table("newsletters")
