"""Aggregate a partner's ledger window for earnings estimation."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — query parameter type
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002 — DI signature

from app.models.poly_attribution import PolyAttributionEvent


async def summarize(session: AsyncSession, partner: str, since: datetime) -> tuple[dict[str, int], Decimal | None, int]:
    """Return ``(counts_by_event, gross_value_usd, total_events)`` for the window."""
    stmt = (
        select(
            PolyAttributionEvent.event,
            func.count().label("n"),
            func.coalesce(func.sum(PolyAttributionEvent.value_usd), 0),
        )
        .where(
            PolyAttributionEvent.partner == partner,
            PolyAttributionEvent.ts >= since,
        )
        .group_by(PolyAttributionEvent.event)
    )
    rows = (await session.execute(stmt)).all()

    counts: dict[str, int] = {}
    gross = Decimal("0")
    total = 0
    for event_type, n, summed in rows:
        counts[str(event_type)] = int(n)
        total += int(n)
        gross += Decimal(str(summed or 0))
    return counts, gross, total
