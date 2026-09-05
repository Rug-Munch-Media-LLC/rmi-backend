"""User portfolio CRUD — /api/v1/portfolio/*.

Auth-scoped (Supabase session via app.routers.auth.verify_session) and
DB-backed in Postgres (user_portfolio table, app/models/portfolio.py).
"""

from __future__ import annotations

from decimal import Decimal  # noqa: TC003 — Pydantic resolves annotations at runtime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002 — FastAPI Depends resolves annotations at runtime

from app.core.dependencies import get_db
from app.core.logging import get_logger
from app.routers.auth import verify_session

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/portfolio", tags=["portfolio"])


class PortfolioEntry(BaseModel):
    """Request body for adding a token to the user's portfolio."""

    symbol: str = Field(min_length=1, max_length=32)
    chain: str = Field(min_length=1, max_length=64, default="ethereum")
    address: str | None = Field(default=None, max_length=128)
    amount: Decimal | None = None
    avg_buy_price: Decimal | None = None


def _entry_to_dict(entry: Any) -> dict[str, Any]:
    return {
        "id": str(entry.id),
        "symbol": entry.symbol,
        "chain": entry.chain,
        "address": entry.address,
        "amount": entry.amount,
        "avg_buy_price": entry.avg_buy_price,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


@router.get("/")
async def get_portfolio(
    user: dict[str, Any] = Depends(verify_session),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get the authenticated user's saved portfolio tokens."""
    from app.models.portfolio import UserPortfolioEntry

    result = await db.execute(
        select(UserPortfolioEntry)
        .where(UserPortfolioEntry.user_id == user["id"])
        .order_by(UserPortfolioEntry.created_at.desc())
    )
    entries = result.scalars().all()
    return {"entries": [_entry_to_dict(e) for e in entries], "count": len(entries)}


@router.post("/")
async def add_to_portfolio(
    body: PortfolioEntry,
    user: dict[str, Any] = Depends(verify_session),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Add a token to the user's portfolio (upsert on symbol+chain)."""
    from app.models.portfolio import UserPortfolioEntry

    existing = await db.execute(
        select(UserPortfolioEntry).where(
            UserPortfolioEntry.user_id == user["id"],
            UserPortfolioEntry.symbol == body.symbol,
            UserPortfolioEntry.chain == body.chain,
        )
    )
    entry = existing.scalar_one_or_none()
    if entry is None:
        entry = UserPortfolioEntry(user_id=user["id"], **body.model_dump())
        db.add(entry)
        await db.flush()
    else:
        for field, value in body.model_dump(exclude={"symbol", "chain"}).items():
            setattr(entry, field, value)
    return {"entry": _entry_to_dict(entry)}


@router.delete("/{entry_id}")
async def remove_from_portfolio(
    entry_id: str,
    user: dict[str, Any] = Depends(verify_session),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Remove a portfolio entry by id (scoped to the authenticated user)."""
    from app.models.portfolio import UserPortfolioEntry

    result = await db.execute(
        select(UserPortfolioEntry).where(
            UserPortfolioEntry.id == entry_id,
            UserPortfolioEntry.user_id == user["id"],
        )
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Portfolio entry not found")
    await db.delete(entry)
    return {"removed": str(entry.id)}
