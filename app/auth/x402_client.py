"""x402 coffer facilitator client — premium-tier payment verification.

RMI backend never verifies premium payments itself: it delegates to the coffer
x402 facilitator, which holds the verified-payment store (``caller_key``-bound,
TTL'd) and the server-authoritative pricing for each operation.

``verify_payment`` implements the coffer wire contract::

    POST {X402_FACILITATOR_URL}/v1/x402/verify
    {"payment_id": ..., "caller_key": ..., "op": ...}

FAIL-CLOSED BY DESIGN (revenue-leak audit #8): any transport error, timeout,
retry exhaustion, non-200 status or missing ``verified: true`` yields
``{"verified": False, ...}`` — premium tier is NEVER granted on error. The
router maps that to an HTTP 402 PaymentRequiredError.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Path appended to ``settings.x402_facilitator_url`` for payment verification.
VERIFY_PATH = "/v1/x402/verify"

#: Transport timeout for a single facilitator round-trip (seconds).
TIMEOUT_SECONDS = 5.0
#: Bounded retry: 1 initial attempt + ``MAX_RETRIES`` retries on transport
#: errors and 5xx responses. Payment rejections (4xx) are final — never retried.
MAX_RETRIES = 2
#: Base of the exponential backoff between retries (seconds).
RETRY_BASE_DELAY = 0.05

#: Statuses worth retrying — the facilitator may be mid-restart / overloaded.
_RETRYABLE_STATUS = {500, 502, 503, 504}


def _build_url() -> str:
    """Join the configured facilitator base URL with the verify path."""
    return f"{settings.x402_facilitator_url.rstrip('/')}{VERIFY_PATH}"


def _denied(payment_id: str, reason: str, **extra: Any) -> dict[str, Any]:
    """Fail-closed result — never carries ``verified: True``."""
    return {"verified": False, "payment_id": payment_id, "reason": reason, **extra}


async def verify_payment(payment_id: str, caller_key: str, op: str) -> dict[str, Any]:
    """Ask the coffer facilitator to verify ``payment_id`` for ``caller_key``/``op``.

    Args:
        payment_id: Verified payment id presented by the caller (``X-Payment-ID``).
        caller_key: Caller identity the payment was recorded against.
        op: Server-authoritative operation being paid for (e.g. ``premium:pro``).

    Returns:
        ``{"verified": True, "payment_id": ..., "reason": ...}`` only when the
        facilitator confirms the payment. Every failure path returns
        ``{"verified": False, "payment_id": ..., "reason": ...}`` — premium
        access is denied whenever the facilitator is unreachable or errors.
    """
    url = _build_url()
    payload = {"payment_id": payment_id, "caller_key": caller_key, "op": op}

    for attempt in range(MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                response = await client.post(url, json=payload)
        except httpx.RequestError as exc:
            # Transport failure (connect / DNS / timeout / read) — bounded retry.
            reason = f"facilitator_unreachable: {exc.__class__.__name__}"
            logger.warning("x402_verify_transport_err payment_id=%s attempt=%d err=%s", payment_id, attempt, exc)
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_BASE_DELAY * (2**attempt))
                continue
            return _denied(payment_id, reason)

        if response.status_code in _RETRYABLE_STATUS and attempt < MAX_RETRIES:
            logger.warning(
                "x402_verify_retryable payment_id=%s status=%d attempt=%d",
                payment_id,
                response.status_code,
                attempt,
            )
            await asyncio.sleep(RETRY_BASE_DELAY * (2**attempt))
            continue

        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError:
                return _denied(payment_id, "invalid_facilitator_response")
            if data.get("verified") is True:
                logger.info("x402_verify_ok payment_id=%s op=%s caller_key=%s", payment_id, op, caller_key)
                return {
                    "verified": True,
                    "payment_id": payment_id,
                    "reason": data.get("reason", "") or "payment verified",
                }
            return _denied(
                payment_id,
                data.get("reason") or data.get("error") or "payment_not_verified",
            )

        # Non-retryable (4xx payment rejection) or exhausted retries -> deny.
        return _denied(payment_id, f"facilitator_http_{response.status_code}")

    # Unreachable — the loop above always returns.
    return _denied(payment_id, "facilitator_unreachable")
