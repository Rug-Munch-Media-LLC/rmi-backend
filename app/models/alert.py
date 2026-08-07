"""Alert and AlertSubscription ORM models."""

from __future__ import annotations
from datetime import datetime

import uuid
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models.base import TimestampMixin



class Alert(Base, TimestampMixin):
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    token_address: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        index=True,
    )
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    alert_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="info", nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True, default=None)
    fired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )

    __table_args__ = (
        Index("ix_alert_alert_type", "alert_type"),
        Index("ix_alert_severity", "severity"),
        Index("ix_alert_token_chain_type", "token_address", "chain", "alert_type"),
        Index("ix_alert_fired_at", "fired_at"),
    )


class AlertSubscription(Base, TimestampMixin):
    __tablename__ = "alert_subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    token_address: Mapped[str] = mapped_column(String(256), nullable=False)
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    alert_types: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    webhook_url: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )

    __table_args__ = (Index("ix_alert_sub_token_chain", "token_address", "chain"),)
