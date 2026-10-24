"""V1 paid boost endpoints — /api/v1/boosts/*.

Our own paid-placement inventory. Purchase, list the ``boosted`` surface for
one chain, and sweep expiries. Nothing here touches ranking: paid placement is
rendered in the ``boosted`` slot only, never in trending rank
(``app/routers/v1/market.py`` remains unpaid/ClickHouse-derived).

Payment safety: ``POST /boosts`` activates a placement only after the injected
entitlement verifier confirms a settled receipt in our payments ledger. There
is no path that marks a purchase paid without that proof.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002 — FastAPI resolves annotations at runtime

from app.core.database import get_db
from app.core.internal_key import get_internal_key_or_none
from app.core.logging import get_logger
from app.domains.boost.create import DuplicateBoost, create_boost
from app.domains.boost.entitlement import (
    EntitlementDenied,
    EntitlementVerifier,
    verify_entitlement,
)
from app.domains.boost.expiry import expire_due_boosts
from app.domains.boost.listing import list_active_boosts
from app.domains.boost.pricing import PLANS, InvalidPlanError
from app.domains.boost.schemas import (
    BoostCreateRequest,
    BoostListResponse,
    BoostOut,
    BoostPlansResponse,
    ExpireResponse,
    normalize_chain,
    plan_catalog,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/boosts", tags=["boosts-v1"])


async def get_verifier() -> EntitlementVerifier:
    """DI seam: swap in another payment provider's verifier for tests/rollout."""
    return verify_entitlement


def require_internal_key(x_rmi_key: str | None = Header(default=None)) -> None:
    """Guard the maintenance sweep with the fleet internal key."""
    expected = get_internal_key_or_none()
    presented = x_rmi_key or ""
    if not expected or not hmac.compare_digest(presented, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="internal key required for boost expiry",
        )


@router.get("/plans", response_model=BoostPlansResponse, summary="Published boost pricing")
async def list_plans() -> BoostPlansResponse:
    """Published tiers with explicit currency — the price the buyer will pay."""
    return plan_catalog()


@router.post(
    "",
    response_model=BoostOut,
    status_code=status.HTTP_201_CREATED,
    summary="Buy a paid placement",
)
async def create(
    request: BoostCreateRequest,
    session: AsyncSession = Depends(get_db),
    verifier: EntitlementVerifier = Depends(get_verifier),
) -> BoostOut:
    """Activate a placement after the receipt is proven settled."""
    try:
        boost = await create_boost(session, request, verifier)
    except EntitlementDenied as exc:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(exc)) from exc
    except DuplicateBoost as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.reason) from exc
    except InvalidPlanError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return BoostOut.model_validate(boost)


@router.get(
    "/boosted",
    response_model=BoostListResponse,
    summary="Active paid placements for one chain",
)
async def boosted(
    chain: str = Query(min_length=2, max_length=32),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
) -> BoostListResponse:
    """The only paid-placement surface. Active, unexpired, one chain."""
    canonical = normalize_chain(chain)
    items = await list_active_boosts(session, canonical, limit=limit)
    currency = items[0].currency if items else next(iter(PLANS.values())).currency
    return BoostListResponse(
        chain=canonical,
        count=len(items),
        currency=currency,
        items=[BoostOut.model_validate(item) for item in items],
    )


@router.post(
    "/expire",
    response_model=ExpireResponse,
    summary="Expire boosts whose window has closed (internal)",
    dependencies=[Depends(require_internal_key)],
)
async def expire(session: AsyncSession = Depends(get_db)) -> ExpireResponse:
    """Idempotent sweep — safe to run on a schedule."""
    return ExpireResponse(expired=await expire_due_boosts(session))
