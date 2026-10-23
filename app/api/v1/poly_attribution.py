"""Polymarket partner attribution: event ingest + earnings estimate."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.poly_attribution_models import AttributionEventIn, Envelope
from app.core.database import get_db
from app.core.logging import get_logger
from app.domains.poly_attribution.allowlist import is_allowed, load_allowlist
from app.domains.poly_attribution.contract import EventInput
from app.domains.poly_attribution.earnings import estimate_earnings
from app.domains.poly_attribution.hashing import MissingSaltError, hash_identifier
from app.domains.poly_attribution.ledger import IdempotencyConflict, record_event
from app.domains.poly_attribution.rate_limit import limiter
from app.domains.poly_attribution.summary import summarize

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1/poly/attribution", tags=["poly-attribution"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


def _fail(status: int, code: str, message: str, request_id: str) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"code": code, "message": message, "request_id": request_id},
    )


def _partner_key_ok(partner: str, provided: str) -> bool:
    rule = load_allowlist().get(partner)
    if rule is None or rule.key_hash is None:
        return False
    return hash_identifier(provided) == rule.key_hash


def _to_event(payload: AttributionEventIn, idem_key: str, request_id: str) -> EventInput:
    if not is_allowed(payload.partner, payload.campaign):
        raise _fail(403, "NOT_ALLOWED", "partner/campaign not allowlisted", request_id)
    try:
        wallet_hash = hash_identifier(payload.wallet) if payload.wallet else None
        builder_hash = hash_identifier(payload.builder_code) if payload.builder_code else None
    except MissingSaltError as exc:
        raise _fail(503, "SALT_MISSING", str(exc), request_id) from exc
    return EventInput(
        partner=payload.partner,
        campaign=payload.campaign,
        surface=payload.surface,
        event=payload.event,
        market_id=payload.market_id,
        wallet_hash=wallet_hash,
        builder_code_hash=builder_hash,
        idem_key=idem_key,
        ts=payload.ts or datetime.now(UTC),
        value_usd=payload.value_usd,
        ts_from_client=payload.ts is not None,
    )


@router.post("/events", status_code=201, response_model=Envelope)
async def ingest_event(
    payload: AttributionEventIn,
    response: Response,
    db: DbSession,
    x_partner_key: Annotated[str, Header(alias="X-Partner-Key")],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> Envelope:
    """Record one attribution event. Replays return 200 with ``duplicate=true``."""
    request_id = uuid.uuid4().hex
    allowed, remaining, retry_after = limiter.check(payload.partner)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    if not allowed:
        response.headers["Retry-After"] = str(retry_after)
        raise _fail(429, "RATE_LIMITED", "too many attribution events", request_id)

    if not idempotency_key or len(idempotency_key) > 128:
        raise _fail(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header (<=128) required", request_id)
    if not _partner_key_ok(payload.partner, x_partner_key):
        raise _fail(403, "BAD_PARTNER_KEY", "partner key rejected", request_id)

    event = _to_event(payload, idempotency_key, request_id)
    try:
        row, status = await record_event(db, event)
    except IdempotencyConflict as exc:
        raise _fail(409, "IDEMPOTENCY_CONFLICT", str(exc), request_id) from exc

    data: dict[str, Any] = {
        "id": str(row.id),
        "partner": row.partner,
        "campaign": row.campaign,
        "surface": row.surface,
        "event": row.event,
        "market_id": row.market_id,
        "wallet_hash": row.wallet_hash,
        "builder_code_hash": row.builder_code_hash,
        "ts": row.ts.isoformat(),
        "value_usd": str(row.value_usd) if row.value_usd is not None else None,
        "duplicate": status == "duplicate",
    }
    if status == "duplicate":
        response.status_code = 200
    return Envelope(data=data, meta={"result": status}, request_id=request_id)


@router.get("/earnings/{partner}", response_model=Envelope)
async def get_earnings(
    partner: str,
    db: DbSession,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> Envelope:
    """Estimated earnings for the window; null + reason when no rate card exists."""
    request_id = uuid.uuid4().hex
    if partner not in load_allowlist():
        raise _fail(403, "NOT_ALLOWED", "partner not allowlisted", request_id)

    since = datetime.now(UTC) - timedelta(days=days)
    counts, gross, total = await summarize(db, partner, since)
    estimate = estimate_earnings(partner, counts, gross)
    data = {
        "partner": partner,
        "window_days": days,
        "events": total,
        "counts": counts,
        "gross_value_usd": str(gross.quantize(Decimal("0.000001"))),
        "earnings_usd": estimate["earnings_usd"],
        "reason": estimate["reason"],
        "basis": estimate["basis"],
    }
    return Envelope(data=data, meta=None, request_id=request_id)
