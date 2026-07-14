"""initial

Revision ID: 0001
Revises:
Create Date: 2026-07-14 10:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", sa.String(320), nullable=True, unique=True),
        sa.Column("wallet_address", sa.String(128), nullable=True, unique=True),
        sa.Column("role", sa.String(32), nullable=False, server_default="USER"),
        sa.Column("tier", sa.String(32), nullable=False, server_default="free"),
        sa.Column("xp", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("level", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_wallet_address", "users", ["wallet_address"])

    op.create_table(
        "tokens",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("address", sa.String(256), nullable=False),
        sa.Column("chain", sa.String(32), nullable=False, server_default="solana"),
        sa.Column("name", sa.String(256), nullable=False, server_default=""),
        sa.Column("symbol", sa.String(64), nullable=False, server_default=""),
        sa.Column("decimals", sa.Integer(), nullable=False, server_default="9"),
        sa.Column("total_supply", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("circulating_supply", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("holders", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("risk_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("risk_level", sa.String(16), nullable=False, server_default="low"),
        sa.Column("extra", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("chain", "address", name="uq_token_chain_address"),
    )
    op.create_index("ix_tokens_address", "tokens", ["address"])
    op.create_index("ix_token_symbol", "tokens", ["symbol"])

    op.create_table(
        "alerts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("token_address", sa.String(256), nullable=False),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("alert_type", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="info"),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_alerts_token_address", "alerts", ["token_address"])
    op.create_index("ix_alerts_owner_id", "alerts", ["owner_id"])
    op.create_index("ix_alerts_fired_at", "alerts", ["fired_at"])

    op.create_table(
        "alert_subscriptions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("token_address", sa.String(256), nullable=False),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("alert_types", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("webhook_url", sa.String(2048), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_alert_subscriptions_owner_id", "alert_subscriptions", ["owner_id"])
    op.create_index("ix_alert_sub_token_chain", "alert_subscriptions", ["token_address", "chain"])

    op.create_table(
        "labels",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("address", sa.String(256), nullable=False),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("label_type", sa.String(64), nullable=False),
        sa.Column("label_value", sa.String(256), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("entity", sa.String(256), nullable=True),
        sa.Column("entity_category", sa.String(64), nullable=True),
        sa.Column("attributes", postgresql.JSONB(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("address", "chain", "label_type", "label_value", name="uq_label"),
    )
    op.create_index("ix_labels_address", "labels", ["address"])
    op.create_index("ix_label_label_type", "labels", ["label_type"])
    op.create_index("ix_label_entity", "labels", ["entity"])

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
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_scan_results_token_address", "scan_results", ["token_address"])
    op.create_index("ix_scan_result_token_chain", "scan_results", ["token_address", "chain"])
    op.create_index("ix_scan_result_scanned_at", "scan_results", ["scanned_at"])

    op.create_table(
        "payments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tool", sa.String(128), nullable=False),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("tx_hash", sa.String(256), nullable=True),
        sa.Column("amount_paid", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("user_address", sa.String(128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_payments_tx_hash", "payments", ["tx_hash"])
    op.create_index("ix_payments_user_address", "payments", ["user_address"])
    op.create_index("ix_payments_status", "payments", ["status"])


def downgrade() -> None:
    op.drop_table("payments")
    op.drop_table("scan_results")
    op.drop_table("labels")
    op.drop_table("alert_subscriptions")
    op.drop_table("alerts")
    op.drop_table("tokens")
    op.drop_table("users")
