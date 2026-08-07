"""ScanResult ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models.base import TimestampMixin


class ScanResult(Base, TimestampMixin):
    __tablename__ = "scan_results"

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
    symbol: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    name: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    safety_score: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    risk_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), default="low", nullable=False)
    risk_flags: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    warnings: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    modules_run: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    extra: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        default=None,
    )
    scanned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    tier_required: Mapped[str] = mapped_column(
        String(16),
        default="free",
        nullable=False,
    )
    confidence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (
        Index("ix_scan_result_token_chain", "token_address", "chain"),
        Index("ix_scan_result_scanned_at", "scanned_at"),
        Index("ix_scan_result_risk_level", "risk_level"),
        Index("ix_scan_result_chain_risk", "chain", "risk_level"),
        Index("ix_scan_result_tier", "tier_required"),
    )
