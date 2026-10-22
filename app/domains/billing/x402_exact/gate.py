"""x402 exact — the payment gate.

Order of operations (each step fails closed):

1. resolve the resource from the catalog (unknown -> 404);
2. per-caller cadence guard (abuse -> 429);
3. free tier: under the daily allowance the call is served unpaid;
4. no X-PAYMENT -> 402 challenge (always a way forward, never a bare 429);
5. decode + validate the proof, claim the payment id (replay -> 409);
6. facilitator verify -> 402 when unverified;
7. claim the tx hash (replay -> 409);
8. facilitator settle -> 402 when unsettled;
9. ledger the settled call and return the payment context.

A resource is served only after step 8 succeeds.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from fastapi import HTTPException, Request

from app.core.logging import get_logger
from app.domains.billing.x402_exact import facilitator, idempotency, ledger, ratelimit, replay
from app.domains.billing.x402_exact.catalog import NETWORK_BASE, USDC_BASE, PaidEndpoint, resolve
from app.domains.billing.x402_exact.challenge import build_challenge

log = get_logger(__name__)

_HEADER = "X-PAYMENT"


def _identity(request: Request, payment: dict[str, Any] | None = None) -> str:
    """Caller identity: declared wallet, else client IP."""
    wallet = (request.headers.get("X-WALLET") or "").strip()
    if not wallet and payment:
        wallet = str(payment.get("payer") or payment.get("from") or "")
    if wallet:
        return f"wallet:{wallet.lower()}"
    client = request.client
    return f"ip:{client.host}" if client else "ip:unknown"


def _decode(header: str) -> dict[str, Any] | None:
    """Decode the base64 JSON X-PAYMENT envelope."""
    try:
        raw = base64.b64decode(header, validate=True)
        parsed = json.loads(raw)
    except (binascii.Error, ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _reject(endpoint: PaidEndpoint, error: str, detail: str, status: int = 402) -> HTTPException:
    """Build an HTTPException carrying a fresh challenge."""
    body = build_challenge(endpoint, error=error, detail=detail) if status == 402 else {
        "x402Version": 1,
        "error": error,
        "detail": detail,
        "resource": endpoint.path,
    }
    return HTTPException(status_code=status, detail=body)


def _validate_proof(endpoint: PaidEndpoint, proof: dict[str, Any]) -> tuple[str, str, float] | None:
    """Return (payment_id, tx_hash, amount_usd) when the proof is well formed."""
    payment_id = str(proof.get("paymentId") or proof.get("payment_id") or "")
    tx_hash = str(proof.get("txHash") or proof.get("tx_hash") or "")
    try:
        amount_usd = float(proof.get("amountUsd") or proof.get("amount_usd") or 0)
    except (TypeError, ValueError):
        return None
    if not payment_id or not tx_hash or amount_usd <= 0:
        return None
    if amount_usd + 1e-9 < endpoint.usd:
        return None
    network = str(proof.get("network") or NETWORK_BASE)
    if network != NETWORK_BASE:
        return None
    return payment_id, tx_hash, amount_usd


async def _settle(endpoint: PaidEndpoint, proof: dict[str, Any], payer: str) -> dict[str, Any]:
    """Verify, claim and settle. Returns the payment context or raises."""
    validated = _validate_proof(endpoint, proof)
    if validated is None:
        raise _reject(endpoint, "invalid_payment_proof", "malformed, under-priced or wrong-network proof")
    payment_id, tx_hash, amount_usd = validated

    if not await replay.claim("payment", payment_id):
        raise _reject(endpoint, "payment_already_used", "paymentId already consumed", status=409)

    verified = await facilitator.verify(payment_id, tx_hash, NETWORK_BASE, USDC_BASE, amount_usd)
    if not verified["ok"]:
        await replay.release("payment", payment_id)
        raise _reject(endpoint, "payment_verification_failed", verified["reason"] or "unverified")

    if not await replay.claim("tx", tx_hash.lower()):
        await replay.release("payment", payment_id)
        raise _reject(endpoint, "payment_already_used", "txHash already settled", status=409)

    settled = await facilitator.settle(payment_id, tx_hash, NETWORK_BASE, USDC_BASE, amount_usd)
    if not settled["ok"]:
        await replay.release("payment", payment_id)
        await replay.release("tx", tx_hash.lower())
        raise _reject(endpoint, "settlement_failed", settled["reason"] or "unsettled")

    await ledger.record(
        {
            "payer": payer,
            "resource": endpoint.path,
            "handle": endpoint.handle,
            "amount_atoms": endpoint.price_atoms,
            "amount_usd": amount_usd,
            "payment_id": payment_id,
            "tx_hash": settled["tx_hash"] or tx_hash,
            "network": NETWORK_BASE,
            "asset": USDC_BASE,
            "status": "settled",
        }
    )
    return {
        "handle": endpoint.handle,
        "payer": payer,
        "paid": True,
        "tier": endpoint.tier,
        "payment_id": payment_id,
        "tx_hash": settled["tx_hash"] or tx_hash,
        "amount_usd": amount_usd,
    }


async def require_paid(handle: str, request: Request) -> dict[str, Any]:
    """FastAPI dependency: serve only when free-tier eligible or settled."""
    endpoint = resolve(handle)
    if endpoint is None:
        raise HTTPException(status_code=404, detail={"error": "unknown_resource", "handle": handle})

    proof = _decode(request.headers.get(_HEADER, "")) if request.headers.get(_HEADER) else None
    payer = _identity(request, proof)

    if not await ratelimit.permit_cadence(payer):
        raise _reject(endpoint, "rate_limit_exceeded", "too many requests per minute", status=429)

    idem_key = request.headers.get("Idempotency-Key", "")
    if idem_key:
        cached = await idempotency.lookup(idem_key, handle, payer)
        if cached is not None:
            return {"handle": handle, "payer": payer, "paid": False, "tier": "idempotent", "replayed": cached}

    if proof is None:
        allowance = await ratelimit.free_tier(payer, handle, endpoint.free_daily)
        if allowance["allowed"]:
            return {
                "handle": handle,
                "payer": payer,
                "paid": False,
                "tier": "free",
                "free_used": allowance["used"],
                "free_limit": allowance["limit"],
            }
        raise _reject(endpoint, "payment_required", "free tier exhausted")

    return await _settle(endpoint, proof, payer)
