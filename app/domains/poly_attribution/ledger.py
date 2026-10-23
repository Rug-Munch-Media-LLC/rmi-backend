"""Idempotent append to the attribution ledger."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002 — DI signature

from app.domains.poly_attribution.contract import EventInput  # noqa: TC001 — constructed locally
from app.models.poly_attribution import PolyAttributionEvent


class IdempotencyConflict(Exception):
    """Same idempotency key replayed with a different payload."""


async def record_event(session: AsyncSession, event: EventInput) -> tuple[PolyAttributionEvent, str]:
    """Insert ``event`` once. Returns ``(row, "created" | "duplicate")``."""
    existing = await _find(session, event.partner, event.idem_key)
    if existing is not None:
        _guard_fingerprint(existing, event)
        return existing, "duplicate"

    row = PolyAttributionEvent(
        partner=event.partner,
        campaign=event.campaign,
        surface=event.surface,
        event=event.event,
        market_id=event.market_id,
        wallet_hash=event.wallet_hash,
        builder_code_hash=event.builder_code_hash,
        idem_key=event.idem_key,
        fingerprint=event.fingerprint(),
        ts=event.ts,
        value_usd=event.value_usd,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError:
        # Concurrent replay won the unique constraint — return the winner.
        await session.rollback()
        existing = await _find(session, event.partner, event.idem_key)
        if existing is None:
            raise
        _guard_fingerprint(existing, event)
        return existing, "duplicate"
    return row, "created"


async def _find(session: AsyncSession, partner: str, idem_key: str) -> PolyAttributionEvent | None:
    stmt = select(PolyAttributionEvent).where(
        PolyAttributionEvent.partner == partner,
        PolyAttributionEvent.idem_key == idem_key,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def _guard_fingerprint(row: PolyAttributionEvent, event: EventInput) -> None:
    if row.fingerprint != event.fingerprint():
        raise IdempotencyConflict("idempotency key reused with a different payload")
