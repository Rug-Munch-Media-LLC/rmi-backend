"""Label ORM model."""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from typing import Any

from app.models import Base
from app.models.base import TimestampMixin


class Label(Base, TimestampMixin):
    __tablename__ = "labels"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    address: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        index=True,
    )
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    label_type: Mapped[str] = mapped_column(String(64), nullable=False)
    label_value: Mapped[str] = mapped_column(String(256), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    entity: Mapped[str | None] = mapped_column(String(256), nullable=True)
    entity_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attributes: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True, default=None)
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "address",
            "chain",
            "label_type",
            "label_value",
            name="uq_label",
        ),
        Index("ix_label_label_type", "label_type"),
        Index("ix_label_entity", "entity"),
    )
