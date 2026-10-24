"""Expire boosts whose paid window has closed.

Idempotent: only ``active`` rows past ``ends_at`` are touched, and each
transition writes one audit event. Safe to call on a schedule (cron/worker) or
on demand through the internal endpoint.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.core.logging import get_logger
from app.domains.boost.audit import record_event
from app.models.boost import BOOST_ACTIVE, BOOST_EXPIRED, EVENT_EXPIRED, BoostPurchase

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


async def expire_due_boosts(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    actor: str = "scheduler",
    limit: int = 500,
) -> int:
    """Flip every due boost to ``expired``; return how many changed.

    The caller owns the transaction, so a failure mid-sweep rolls the batch
    back and the rows stay ``active`` for the next run.
    """
    moment = now or datetime.now(UTC)
    stmt = (
        select(BoostPurchase)
        .where(
            BoostPurchase.status == BOOST_ACTIVE,
            BoostPurchase.ends_at.is_not(None),
            BoostPurchase.ends_at <= moment,
        )
        .order_by(BoostPurchase.ends_at)
        .limit(max(1, int(limit)))
        .with_for_update(skip_locked=True)
    )
    due = list((await session.execute(stmt)).scalars().all())

    for boost in due:
        boost.status = BOOST_EXPIRED
        await record_event(
            session,
            boost.id,
            EVENT_EXPIRED,
            actor=actor,
            detail={"ends_at": boost.ends_at.isoformat() if boost.ends_at else None},
        )

    if due:
        await session.flush()
        logger.info("boosts_expired count=%d at=%s", len(due), moment.isoformat())
    return len(due)
