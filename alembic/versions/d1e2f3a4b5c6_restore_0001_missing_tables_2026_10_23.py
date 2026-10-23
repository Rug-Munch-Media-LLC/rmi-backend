"""restore the four remaining 0001-era tables (idempotent)

Revision ID: d1e2f3a4b5c6
Revises: c8f9e0a1b2c3
Create Date: 2026-10-23 16:55:00.000000

Why: ``alembic_version`` on the ``rmi`` database was stamped past
``75c982fccbb8``; that revision's five tables were never created. The first
one (``scan_results``) was restored by ``c8f9e0a1b2c3``. The remaining four --
``users``, ``payments``, ``labels``, ``alert_subscriptions`` -- are still absent
and every code path that touches them raises ``relation ... does not exist``:

- ``users``: ``app/services/live/xp_service.py``, ``app/db_client/repositories.py``
- ``payments``: ``app/models/payment.py`` consumers (x402 / billing)
- ``labels``: ``app/domains/labels/sources/*``, ``app/domains/labels/federated.py``
- ``alert_subscriptions``: ``app/domains/alerts/service.py``

Columns, defaults, FKs and indexes mirror ``0001_initial`` /
``75c982fccbb8`` and ``app/models/*``. Each table is created only when absent,
so the revision is a no-op on a correctly provisioned database (fresh installs,
CI). Nothing existing is altered or dropped; the migration is forward-only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

revision: str = "d1e2f3a4b5c6"
down_revision: str | Sequence[str] | None = "c8f9e0a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("now()")


def _table_exists(name: str) -> bool:
    """Return True when the table is already present (offline mode: False)."""
    ctx = op.get_context()
    if getattr(ctx, "as_sql", False):
        return False
    from sqlalchemy import inspect

    return inspect(op.get_bind()).has_table(name)


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def _timestamps() -> tuple[sa.Column, sa.Column]:
    """``created_at`` / ``updated_at`` as declared by ``TimestampMixin``."""
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
    )


def _create_users() -> None:
    op.create_table(
        "users",
        _uuid_pk(),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("wallet_address", sa.String(128), nullable=True),
        sa.Column("role", sa.String(32), nullable=False, server_default="USER"),
        sa.Column("tier", sa.String(32), nullable=False, server_default="free"),
        sa.Column("xp", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("level", sa.Integer(), nullable=False, server_default="1"),
        *_timestamps(),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_wallet_address", "users", ["wallet_address"], unique=True)


def _create_payments() -> None:
    op.create_table(
        "payments",
        _uuid_pk(),
        sa.Column("tool", sa.String(128), nullable=False),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("tx_hash", sa.String(256), nullable=True),
        sa.Column("amount_paid", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("user_address", sa.String(128), nullable=True),
        *_timestamps(),
        sa.Index("ix_payment_status", "status"),
    )
    op.create_index("ix_payments_tx_hash", "payments", ["tx_hash"], unique=False)
    op.create_index("ix_payments_user_address", "payments", ["user_address"], unique=False)


def _create_labels() -> None:
    op.create_table(
        "labels",
        _uuid_pk(),
        sa.Column("address", sa.String(256), nullable=False),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("label_type", sa.String(64), nullable=False),
        sa.Column("label_value", sa.String(256), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("entity", sa.String(256), nullable=True),
        sa.Column("entity_category", sa.String(64), nullable=True),
        sa.Column("attributes", sa.JSON(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("address", "chain", "label_type", "label_value", name="uq_label"),
        sa.Index("ix_label_label_type", "label_type"),
        sa.Index("ix_label_entity", "entity"),
    )
    op.create_index("ix_labels_address", "labels", ["address"], unique=False)


def _create_alert_subscriptions() -> None:
    op.create_table(
        "alert_subscriptions",
        _uuid_pk(),
        sa.Column("token_address", sa.String(256), nullable=False),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("alert_types", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("webhook_url", sa.String(2048), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_alert_sub_token_chain", "alert_subscriptions", ["token_address", "chain"])
    op.create_index("ix_alert_subscriptions_owner_id", "alert_subscriptions", ["owner_id"])


# ``users`` first: ``alert_subscriptions.owner_id`` references it.
_TABLES: tuple[tuple[str, Callable[[], None]], ...] = (
    ("users", _create_users),
    ("payments", _create_payments),
    ("labels", _create_labels),
    ("alert_subscriptions", _create_alert_subscriptions),
)


def upgrade() -> None:
    """Create each table of the stamped-past revision when it is absent."""
    for name, create in _TABLES:
        if not _table_exists(name):
            create()


def downgrade() -> None:
    """Drop only the tables this revision created (forward-only in production)."""
    for name, _create in reversed(_TABLES):
        if _table_exists(name):
            op.drop_table(name)
