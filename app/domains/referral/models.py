"""ORM model for the append-only referral click log (Postgres canonical)."""

from __future__ import annotations

import uuid
from datetime import datetime  # noqa: TC003 — SQLAlchemy Mapped[] needs runtime resolution

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class ReferralClick(Base):
    """One attribution event. No raw identifiers — only HMAC digests."""

    __tablename__ = "referral_clicks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    click_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True, default=uuid.uuid4)
    partner: Mapped[str] = mapped_column(String(64), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    surface: Mapped[str] = mapped_column(String(32), nullable=False)
    tier: Mapped[str] = mapped_column(String(32), nullable=False)
    referrer_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chat_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    landed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_referral_clicks_partner_code_time", "partner", "code", "landed_at"),
        Index("ix_referral_clicks_surface_time", "surface", "landed_at"),
    )
