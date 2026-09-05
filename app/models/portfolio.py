"""Portfolio & watchlist ORM models — user-saved token tracking.

Backs the auth-scoped CRUD endpoints in app/routers/portfolio.py and
app/routers/watchlist.py. ``user_id`` is the Supabase auth user id (string),
matching the identity returned by app.routers.auth.verify_session.
"""

from __future__ import annotations

import uuid
from decimal import Decimal  # noqa: TC003 — SQLAlchemy Mapped[] needs runtime resolution

from sqlalchemy import Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models.base import TimestampMixin


class UserPortfolioEntry(Base, TimestampMixin):
    """A token position saved by a user (symbol/chain/address + size + cost basis)."""

    __tablename__ = "user_portfolio"
    __table_args__ = (UniqueConstraint("user_id", "symbol", "chain", name="uq_user_portfolio_user_symbol_chain"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    chain: Mapped[str] = mapped_column(String(64), nullable=False, default="ethereum")
    address: Mapped[str | None] = mapped_column(String(128), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    avg_buy_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)


class UserWatchlistEntry(Base, TimestampMixin):
    """A token a user watches, with optional price-alert thresholds."""

    __tablename__ = "user_watchlist"
    __table_args__ = (UniqueConstraint("user_id", "symbol", "chain", name="uq_user_watchlist_user_symbol_chain"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    chain: Mapped[str] = mapped_column(String(64), nullable=False, default="ethereum")
    address: Mapped[str | None] = mapped_column(String(128), nullable=True)
    alert_above: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    alert_below: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
