"""add_missing_tables_2026_07_20

Revision ID: 75c982fccbb8
Revises: 0001
Create Date: 2026-07-20 15:17:52.624623

NOTE: This migration ONLY adds tables that are defined in the ORM models
but do not yet exist in the database.  It deliberately does NOT touch the
pre-existing ``alerts`` and ``tokens`` tables, which have a completely
different schema (created by an older Supabase / Prisma migration).  Those
tables are accessed through db_client.py and other direct-SQL paths, not
through the ORM models.

If you later need to reconcile the ORM model definitions with the live
``tokens`` / ``alerts`` schemas, do so in a separate, carefully reviewed
migration — the schemas are fundamentally incompatible (different PKs,
different column sets) and a naive ALTER will destroy data.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "75c982fccbb8"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create tables missing from the ORM model definitions.

    Creates 5 tables for which the ORM models exist but the corresponding
    database table was never created (or was lost during schema migration):

      - alert_subscriptions
      - labels
      - payments
      - scan_results
      - users

    The existing ``alerts`` and ``tokens`` tables are left untouched.
    """
    # ------------------------------------------------------------------ #
    # alert_subscriptions                                                 #
    # ------------------------------------------------------------------ #
    op.create_table(
        "alert_subscriptions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("token_address", sa.String(length=256), nullable=False),
        sa.Column("chain", sa.String(length=32), nullable=False),
        sa.Column("alert_types", sa.JSON(), nullable=False),
        sa.Column("webhook_url", sa.String(length=2048), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_alert_sub_token_chain",
        "alert_subscriptions",
        ["token_address", "chain"],
        unique=False,
    )
    op.create_index(
        op.f("ix_alert_subscriptions_owner_id"),
        "alert_subscriptions",
        ["owner_id"],
        unique=False,
    )

    # ------------------------------------------------------------------ #
    # labels                                                              #
    # ------------------------------------------------------------------ #
    op.create_table(
        "labels",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("address", sa.String(length=256), nullable=False),
        sa.Column("chain", sa.String(length=32), nullable=False),
        sa.Column("label_type", sa.String(length=64), nullable=False),
        sa.Column("label_value", sa.String(length=256), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("entity", sa.String(length=256), nullable=True),
        sa.Column("entity_category", sa.String(length=64), nullable=True),
        sa.Column("attributes", sa.JSON(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("address", "chain", "label_type", "label_value", name="uq_label"),
    )
    op.create_index("ix_label_entity", "labels", ["entity"], unique=False)
    op.create_index("ix_label_label_type", "labels", ["label_type"], unique=False)
    op.create_index(op.f("ix_labels_address"), "labels", ["address"], unique=False)

    # ------------------------------------------------------------------ #
    # payments                                                            #
    # ------------------------------------------------------------------ #
    op.create_table(
        "payments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tool", sa.String(length=128), nullable=False),
        sa.Column("chain", sa.String(length=32), nullable=False),
        sa.Column("tx_hash", sa.String(length=256), nullable=True),
        sa.Column("amount_paid", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("user_address", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_payments_tx_hash"), "payments", ["tx_hash"], unique=False)
    op.create_index(
        op.f("ix_payments_user_address"),
        "payments",
        ["user_address"],
        unique=False,
    )

    # ------------------------------------------------------------------ #
    # scan_results                                                        #
    # ------------------------------------------------------------------ #
    op.create_table(
        "scan_results",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("token_address", sa.String(length=256), nullable=False),
        sa.Column("chain", sa.String(length=32), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("safety_score", sa.Integer(), nullable=False),
        sa.Column("risk_score", sa.Integer(), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("risk_flags", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("modules_run", sa.JSON(), nullable=False),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tier_required", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scan_result_scanned_at", "scan_results", ["scanned_at"], unique=False)
    op.create_index(
        "ix_scan_result_token_chain",
        "scan_results",
        ["token_address", "chain"],
        unique=False,
    )
    op.create_index(
        op.f("ix_scan_results_token_address"),
        "scan_results",
        ["token_address"],
        unique=False,
    )

    # ------------------------------------------------------------------ #
    # users                                                               #
    # ------------------------------------------------------------------ #
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("wallet_address", sa.String(length=128), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("tier", sa.String(length=32), nullable=False),
        sa.Column("xp", sa.Integer(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
    op.create_index(
        op.f("ix_users_wallet_address"),
        "users",
        ["wallet_address"],
        unique=True,
    )


def downgrade() -> None:
    """Drop the 5 tables that this migration created."""
    op.drop_index(op.f("ix_users_wallet_address"), table_name="users")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
    op.drop_index(op.f("ix_scan_results_token_address"), table_name="scan_results")
    op.drop_index("ix_scan_result_token_chain", table_name="scan_results")
    op.drop_index("ix_scan_result_scanned_at", table_name="scan_results")
    op.drop_table("scan_results")
    op.drop_index(op.f("ix_payments_user_address"), table_name="payments")
    op.drop_index(op.f("ix_payments_tx_hash"), table_name="payments")
    op.drop_table("payments")
    op.drop_index(op.f("ix_labels_address"), table_name="labels")
    op.drop_index("ix_label_label_type", table_name="labels")
    op.drop_index("ix_label_entity", table_name="labels")
    op.drop_table("labels")
    op.drop_index(
        op.f("ix_alert_subscriptions_owner_id"),
        table_name="alert_subscriptions",
    )
    op.drop_index("ix_alert_sub_token_chain", table_name="alert_subscriptions")
    op.drop_table("alert_subscriptions")
