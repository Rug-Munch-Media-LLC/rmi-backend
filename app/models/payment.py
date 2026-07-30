"""Payment ORM model."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import JSON, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models.base import TimestampMixin


class Payment(Base, TimestampMixin):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tool: Mapped[str] = mapped_column(String(128), nullable=False)
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    tx_hash: Mapped[str | None] = mapped_column(
        String(256),
        nullable=True,
        index=True,
    )
    amount_paid: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        default=None,
    )
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    user_address: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )

    __table_args__ = (Index("ix_payment_status", "status"),)
