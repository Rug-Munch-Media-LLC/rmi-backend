"""Referral attribution endpoints — allowlisted redirects, hashed ids, no PII.

Routes:
  POST /api/v1/referral/click      — record an attribution event, return target URL
  GET  /api/v1/referral/go/{partner} — record + 302 to the allowlisted partner URL
  GET  /api/v1/referral/stats      — funnel counts (internal key only)

The redirect target is built from a server-side allowlist keyed by partner; the
caller never supplies a URL, so this cannot become an open redirect.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse

from app.core.logging import get_logger
from app.core.rate_limiter import get_rate_limit_dependency
from app.domains.referral.allowlist import (
    InvalidAttributionError,
    UnknownPartnerError,
    resolve_partner_url,
    validate_code,
    validate_surface,
    validate_tier,
)
from app.domains.referral.deps import get_store, require_internal_key
from app.domains.referral.hashing import hash_identifier
from app.domains.referral.schemas import ClickRequest, ClickResponse
from app.domains.referral.store import ReferralStore  # noqa: TC001 — FastAPI resolves it at runtime

log = get_logger(__name__)
router = APIRouter(prefix="/api/v1/referral", tags=["referral"])


def _validated(payload: ClickRequest) -> tuple[str, str, str]:
    """Validate + normalise (partner_url is resolved separately, after this)."""
    try:
        code = validate_code(payload.code)
        surface = validate_surface(payload.surface)
        tier = validate_tier(payload.tier)
    except InvalidAttributionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return code, surface, tier


@router.post(
    "/click",
    response_model=ClickResponse,
    dependencies=[Depends(get_rate_limit_dependency("referral_click", rate=2.0, burst=5))],
)
async def record_click(payload: ClickRequest, store: ReferralStore = Depends(get_store)) -> ClickResponse:
    """Record an attribution event from a non-redirect surface (bot/content)."""
    code, surface, tier = _validated(payload)
    try:
        target = resolve_partner_url(payload.partner, code)
    except UnknownPartnerError as exc:
        raise HTTPException(status_code=404, detail="unknown partner") from exc

    click_id = await store.record(
        partner=payload.partner,
        code=code,
        surface=surface,
        tier=tier,
        referrer_hash=hash_identifier(payload.user_id),
        chat_hash=hash_identifier(payload.chat_id),
    )
    return ClickResponse(click_id=click_id, partner=payload.partner, target_url=target)


@router.get(
    "/go/{partner}",
    dependencies=[Depends(get_rate_limit_dependency("referral_go", rate=3.0, burst=10))],
)
async def go(
    partner: str,
    code: str = Query(..., min_length=1, max_length=64),
    surface: str = Query("frontend"),
    tier: str | None = Query(default=None, max_length=32),
    user_id: str | None = Query(default=None, max_length=256),
    store: ReferralStore = Depends(get_store),
) -> RedirectResponse:
    """Attribution redirect. Leaves for the partner even if the write fails."""
    payload = ClickRequest(partner=partner, code=code, surface=surface, tier=tier, user_id=user_id)
    safe_code, safe_surface, safe_tier = _validated(payload)
    try:
        target = resolve_partner_url(partner, safe_code)
    except UnknownPartnerError as exc:
        raise HTTPException(status_code=404, detail="unknown partner") from exc
    except InvalidAttributionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        await store.record(
            partner=partner,
            code=safe_code,
            surface=safe_surface,
            tier=safe_tier,
            referrer_hash=hash_identifier(user_id),
            chat_hash=None,
        )
    except Exception as exc:  # attribution must never block the click
        log.warning("referral_click_write_failed partner=%s: %s", partner, exc)

    return RedirectResponse(url=target, status_code=302, headers={"Cache-Control": "no-store"})


@router.get("/stats", dependencies=[Depends(require_internal_key)])
async def stats(
    partner: str | None = Query(default=None, max_length=64),
    days: int = Query(default=7, ge=1, le=90),
    store: ReferralStore = Depends(get_store),
) -> dict[str, Any]:
    """Aggregated funnel counts — internal callers only."""
    rows = await store.stats(partner=partner, days=days)
    return {"days": days, "partner": partner, "buckets": rows}
