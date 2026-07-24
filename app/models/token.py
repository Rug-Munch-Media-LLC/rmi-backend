"""Token ORM model."""

import uuid
from typing import Any

from sqlalchemy import JSON, Boolean, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models.base import TimestampMixin


class Token(Base, TimestampMixin):
    __tablename__ = "tokens"

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
    chain: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="solana",
    )
    name: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    decimals: Mapped[int] = mapped_column(Integer, default=9, nullable=False)
    total_supply: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    circulating_supply: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    holders: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    risk_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), default="low", nullable=False)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True, default=None)

    __table_args__ = (
        UniqueConstraint("chain", "address", name="uq_token_chain_address"),
        Index("ix_token_symbol", "symbol"),
        Index("ix_token_verified", "verified"),
        Index("ix_token_chain_verified", "chain", "verified"),
        Index("ix_token_chain_risk", "chain", "risk_level"),
    )
