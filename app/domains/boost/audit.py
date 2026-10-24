"""Append-only audit trail for boost state transitions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.models.boost import BoostEvent

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession


async def record_event(
    session: AsyncSession,
    boost_id: uuid.UUID,
    event: str,
    *,
    actor: str = "api",
    detail: dict[str, Any] | None = None,
) -> BoostEvent:
    """Append one audit row and flush it (caller owns the transaction)."""
    row = BoostEvent(boost_id=boost_id, event=event, actor=actor, detail=detail)
    session.add(row)
    await session.flush()
    return row
