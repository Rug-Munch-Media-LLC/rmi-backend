"""User watchlist CRUD — /api/v1/watchlist/*.

Auth-scoped (Supabase session via app.routers.auth.verify_session) and
DB-backed in Postgres (user_watchlist table, app/models/portfolio.py).
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

router = APIRouter(prefix="/api/v1/watchlist", tags=["watchlist"])


class WatchlistEntry(BaseModel):
    """Request body for adding a token to the user's watchlist."""

    symbol: str = Field(min_length=1, max_length=32)
    chain: str = Field(min_length=1, max_length=64, default="ethereum")
    address: str | None = Field(default=None, max_length=128)
    alert_above: Decimal | None = None
    alert_below: Decimal | None = None


class WatchlistUpdate(BaseModel):
    """Request body for updating a watchlist entry's alert thresholds."""

    alert_above: Decimal | None = None
    alert_below: Decimal | None = None


def _entry_to_dict(entry: Any) -> dict[str, Any]:
    return {
        "id": str(entry.id),
        "symbol": entry.symbol,
        "chain": entry.chain,
        "address": entry.address,
        "alert_above": entry.alert_above,
        "alert_below": entry.alert_below,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


@router.get("/")
async def get_watchlist(
    user: dict[str, Any] = Depends(verify_session),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get the authenticated user's watchlist."""
    from app.models.portfolio import UserWatchlistEntry

    result = await db.execute(
        select(UserWatchlistEntry)
        .where(UserWatchlistEntry.user_id == user["id"])
        .order_by(UserWatchlistEntry.created_at.desc())
    )
    entries = result.scalars().all()
    return {"entries": [_entry_to_dict(e) for e in entries], "count": len(entries)}


@router.post("/")
async def add_to_watchlist(
    body: WatchlistEntry,
    user: dict[str, Any] = Depends(verify_session),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Add a token to the user's watchlist (upsert on symbol+chain)."""
    from app.models.portfolio import UserWatchlistEntry

    existing = await db.execute(
        select(UserWatchlistEntry).where(
            UserWatchlistEntry.user_id == user["id"],
            UserWatchlistEntry.symbol == body.symbol,
            UserWatchlistEntry.chain == body.chain,
        )
    )
    entry = existing.scalar_one_or_none()
    if entry is None:
        entry = UserWatchlistEntry(user_id=user["id"], **body.model_dump())
        db.add(entry)
        await db.flush()
    else:
        for field, value in body.model_dump(exclude={"symbol", "chain"}).items():
            setattr(entry, field, value)
    return {"entry": _entry_to_dict(entry)}


@router.delete("/{entry_id}")
async def remove_from_watchlist(
    entry_id: str,
    user: dict[str, Any] = Depends(verify_session),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Remove a watchlist entry by id (scoped to the authenticated user)."""
    from app.models.portfolio import UserWatchlistEntry

    result = await db.execute(
        select(UserWatchlistEntry).where(
            UserWatchlistEntry.id == entry_id,
            UserWatchlistEntry.user_id == user["id"],
        )
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Watchlist entry not found")
    await db.delete(entry)
    return {"removed": str(entry.id)}


@router.put("/{entry_id}")
async def update_watchlist_entry(
    entry_id: str,
    body: WatchlistUpdate,
    user: dict[str, Any] = Depends(verify_session),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Update a watchlist entry's alert thresholds."""
    from app.models.portfolio import UserWatchlistEntry

    result = await db.execute(
        select(UserWatchlistEntry).where(
            UserWatchlistEntry.id == entry_id,
            UserWatchlistEntry.user_id == user["id"],
        )
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Watchlist entry not found")
    for field, value in body.model_dump().items():
        setattr(entry, field, value)
    return {"entry": _entry_to_dict(entry)}
