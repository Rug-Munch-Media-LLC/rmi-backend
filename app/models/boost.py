"""Paid boost inventory ORM models — OURS, never external promotion feeds.

``boost_purchases`` records a paid placement bought for one token on one
chain. ``boost_events`` is the append-only audit trail for every state
transition.

External promotion data (DexScreener ``token-boosts``, GMGN) is *discovery
signal only*. It is never written here and never makes an item appear in the
paid ``boosted`` surface — see ``app/domains/boost/listing.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime  # noqa: TC003 — SQLAlchemy Mapped[] resolves at runtime
from decimal import Decimal  # noqa: TC003 — SQLAlchemy Mapped[] resolves at runtime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models.base import TimestampMixin

# ── Status vocabulary (single source; migrations/reporters read these) ──
BOOST_PENDING = "pending"
BOOST_ACTIVE = "active"
BOOST_EXPIRED = "expired"
BOOST_CANCELLED = "cancelled"
BOOST_STATUSES = (BOOST_PENDING, BOOST_ACTIVE, BOOST_EXPIRED, BOOST_CANCELLED)

# ── Audit event vocabulary ──
EVENT_CREATED = "created"
EVENT_ACTIVATED = "activated"
EVENT_EXPIRED = "expired"
EVENT_DENIED = "denied"
EVENT_CANCELLED = "cancelled"


class BoostPurchase(Base, TimestampMixin):
    """One paid placement: a token, a plan, a window, and the money side."""

    __tablename__ = "boost_purchases"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    token_address: Mapped[str] = mapped_column(String(128), nullable=False)
    plan_id: Mapped[str] = mapped_column(String(32), nullable=False)
    duration_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    price_amount: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    payment_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    payment_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    buyer_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=BOOST_PENDING,
        server_default=BOOST_PENDING,
    )
    entitlement_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # One receipt can pay for exactly one boost (replay guard).
        UniqueConstraint("payment_ref", name="uq_boost_purchases_payment_ref"),
        Index("ix_boost_chain_status", "chain", "status"),
        Index("ix_boost_chain_token", "chain", "token_address"),
        Index("ix_boost_ends_status", "ends_at", "status"),
    )


class BoostEvent(Base):
    """Append-only audit trail. Never updated, never deleted by the app."""

    __tablename__ = "boost_events"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    boost_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("boost_purchases.id", ondelete="CASCADE"),
        nullable=False,
    )
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="api",
        server_default="api",
    )
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (Index("ix_boost_events_boost", "boost_id", "created_at"),)
