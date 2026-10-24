"""Create a paid boost — the only write path into the inventory.

Order is deliberate: prove the money first, then check conflicts, then
persist. A denied entitlement leaves no row behind, so an unpaid request can
never occupy a placement slot.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.logging import get_logger
from app.domains.boost.audit import record_event
from app.domains.boost.entitlement import EntitlementCheck, EntitlementDenied, EntitlementVerifier
from app.domains.boost.pricing import get_plan
from app.models.boost import (
    BOOST_ACTIVE,
    EVENT_ACTIVATED,
    EVENT_CREATED,
    BoostPurchase,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.domains.boost.schemas import BoostCreateRequest

logger = get_logger(__name__)


class DuplicateBoost(Exception):
    """The receipt was already spent, or the token already holds a live boost."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


async def _ensure_none_live(session: AsyncSession, *, chain: str, token_address: str, now: datetime) -> None:
    """Reject a second live placement for the same token (one slot, no races)."""
    stmt = (
        select(BoostPurchase.id)
        .where(
            BoostPurchase.chain == chain,
            BoostPurchase.token_address == token_address,
            BoostPurchase.status == BOOST_ACTIVE,
            BoostPurchase.ends_at > now,
        )
        .limit(1)
    )
    if (await session.execute(stmt)).first() is not None:
        raise DuplicateBoost(f"{chain}:{token_address} already has an active boost")


async def _ensure_receipt_unused(session: AsyncSession, payment_ref: str) -> None:
    stmt = select(BoostPurchase.id).where(BoostPurchase.payment_ref == payment_ref).limit(1)
    if (await session.execute(stmt)).first() is not None:
        raise DuplicateBoost(f"payment reference {payment_ref!r} was already used for a boost")


async def create_boost(
    session: AsyncSession,
    request: BoostCreateRequest,
    verifier: EntitlementVerifier,
    *,
    now: datetime | None = None,
) -> BoostPurchase:
    """Activate a paid placement, or raise (``EntitlementDenied`` /
    ``DuplicateBoost`` / ``InvalidPlanError``)."""
    started = now or datetime.now(UTC)
    plan = get_plan(request.plan_id)

    check = EntitlementCheck(
        payment_ref=request.payment_ref,
        chain=request.chain,
        amount=Decimal(plan.price_amount),
        currency=plan.currency,
        provider=request.payment_provider,
    )
    await verifier(session, check)

    await _ensure_receipt_unused(session, request.payment_ref)
    await _ensure_none_live(
        session,
        chain=request.chain,
        token_address=request.token_address,
        now=started,
    )

    boost = BoostPurchase(
        id=uuid.uuid4(),
        chain=request.chain,
        token_address=request.token_address,
        plan_id=plan.plan_id,
        duration_hours=plan.duration_hours,
        price_amount=plan.price_amount,
        currency=plan.currency,
        payment_provider=request.payment_provider,
        payment_ref=request.payment_ref,
        buyer_ref=request.buyer_ref,
        status=BOOST_ACTIVE,
        entitlement_verified=True,
        starts_at=started,
        ends_at=started + timedelta(hours=plan.duration_hours),
    )
    session.add(boost)
    try:
        await session.flush()
    except IntegrityError as exc:  # unique payment_ref lost the race
        await session.rollback()
        raise DuplicateBoost(f"payment reference {request.payment_ref!r} was already used for a boost") from exc

    await record_event(
        session,
        boost.id,
        EVENT_CREATED,
        actor="api",
        detail={
            "plan_id": plan.plan_id,
            "price_amount": str(plan.price_amount),
            "currency": plan.currency,
            "payment_provider": request.payment_provider,
        },
    )
    await record_event(
        session,
        boost.id,
        EVENT_ACTIVATED,
        actor="system",
        detail={"starts_at": started.isoformat(), "ends_at": boost.ends_at.isoformat()},
    )

    logger.info(
        "boost_created id=%s chain=%s token=%s plan=%s ends_at=%s",
        boost.id,
        boost.chain,
        boost.token_address,
        boost.plan_id,
        boost.ends_at,
    )
    return boost


__all__ = ["DuplicateBoost", "EntitlementDenied", "create_boost"]
