"""Polymarket partner attribution ledger ORM model.

One row per attributed surface event (view/click/signup/order) tagged with
partner + campaign. Wallets and builder codes are stored ONLY as salted
SHA-256 hashes — the raw identifiers never reach the database.
"""

from __future__ import annotations

import uuid  # noqa: TC003 — Mapped[uuid.UUID] resolved at runtime
from datetime import datetime  # noqa: TC003 — SQLAlchemy Mapped[] needs runtime resolution
from decimal import Decimal  # noqa: TC003 — SQLAlchemy Mapped[] needs runtime resolution

from sqlalchemy import DateTime, Index, Numeric, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models.base import TimestampMixin


class PolyAttributionEvent(Base, TimestampMixin):
    """Immutable attribution event. Replays collapse on (partner, idem_key)."""

    __tablename__ = "poly_attribution_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    partner: Mapped[str] = mapped_column(String(64), nullable=False)
    campaign: Mapped[str] = mapped_column(String(64), nullable=False)
    surface: Mapped[str] = mapped_column(String(16), nullable=False)
    event: Mapped[str] = mapped_column(String(16), nullable=False)
    market_id: Mapped[str | None] = mapped_column(String(128))
    wallet_hash: Mapped[str | None] = mapped_column(String(64))
    builder_code_hash: Mapped[str | None] = mapped_column(String(64))
    idem_key: Mapped[str] = mapped_column(String(128), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    value_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))

    __table_args__ = (
        UniqueConstraint("partner", "idem_key", name="uq_poly_attr_partner_idem"),
        Index("ix_poly_attr_partner_ts", "partner", "ts"),
        Index("ix_poly_attr_partner_campaign", "partner", "campaign"),
        Index("ix_poly_attr_market", "market_id"),
        Index("ix_poly_attr_wallet", "wallet_hash"),
    )
