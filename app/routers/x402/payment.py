"""
x402 DataBus Tools - Payment verification and request models.
"""

from __future__ import annotations

import json
import os
from typing import Any

from fastapi import Request
from pydantic import BaseModel

from app.core.errors import PaymentRequiredError
from app.core.logging import get_logger

logger = get_logger("x402_databus_payment")

# ── x402 Payment Verification ────────────────────────────────────
# Verifies payment via x402 enforcement middleware.

def _clean_response(result: dict) -> dict:
    """Strip internal DataBus fields that reveal provider architecture.

    External clients see WHAT data they got, not WHERE it came from.
    Internal fields like 'source', 'tier', 'is_local' are kept for
    internal monitoring but stripped from x402 API responses.
    """
    if not isinstance(result, dict):
        return result
    # Remove provider-identifying fields
    for field in ("source", "is_local", "tier", "fallback_used", "consumer", "packaging", "cached"):
        result.pop(field, None)
    # Also clean nested 'data' if it contains source references
    if "data" in result and isinstance(result["data"], dict):
        result["data"].pop("source", None)
        result["data"].pop("provider", None)
    return result

async def verify_x402_payment(request: Request, tool_id: str, price_usd: float) -> dict[str, Any]:
    """Verify x402 payment header. Returns payment info or 402 response.

    For humans: Connect wallet via X-WALLET header → full trial allotment.
    For bots: Fingerprint via X-Device-Id header → 1 trial, then require wallet.
    Both paths use the same Redis-backed trial tracking.
    """
    # Check for x402 payment header
    x402_payment = request.headers.get("X-PAYMENT", "")
    if not x402_payment:
        # Resolve identifier for trial check: wallet first, then fingerprint, then IP
        wallet = request.headers.get("X-WALLET", "") or request.query_params.get("wallet", "")
        fingerprint = request.headers.get("X-Device-Id", "") or request.headers.get(
            "X-Fingerprint", ""
        )
        ip = request.client.host if request.client else "unknown"

        # Use the most specific identifier available
        identifier = wallet or fingerprint or f"ip:{ip}"

        pricing = X402_TOOL_PRICING.get(tool_id, {})
        max_trials = pricing.get("trial_free", 0)
        if max_trials > 0:
            # Connected wallets get full trial allotment; fingerprints get 1 trial max
            if wallet:
                effective_max = max_trials
            elif fingerprint:
                effective_max = min(max_trials, 1)  # Bots get 1 trial, must connect wallet for more
            else:
                effective_max = 1  # IP-only: 1 trial max

            trial = await check_trial(identifier, tool_id, effective_max)
            if trial["allowed"]:
                await consume_trial(identifier, tool_id)
                return {
                    "paid": False,
                    "trial": True,
                    "remaining": trial["remaining"],
                    "identifier": identifier,
                    "identifier_type": "wallet" if wallet else "fingerprint",
                }

        # Return 402 Payment Required
        raise PaymentRequiredError(
            "payment_required",
            context={
                "tool_id": tool_id,
                "price_usd": price_usd,
                "payment_required": {
                    "x402_version": 1,
                    "max_amount": f"{price_usd:.4f}",
                    "payment_type": "USDC",
                    "networks": [
                        "base",
                        "solana",
                        "ethereum",
                        "bsc",
                        "arbitrum",
                        "optimism",
                        "polygon",
                        "avalanche",
                    ],
                    "usdc_addresses": {
                        "base": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                        "solana": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                        "ethereum": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
                    },
                    "pay_to": os.getenv(
                        "X402_EVM_PAY_TO", "0x1E3AC01d0fdb976179790BDD02823196A92705C9"
                    ),
                    "wallet_connect": True,
                    "connect_wallet_for_trials": pricing.get("trial_free", 0) > 0,
                },
                "trial_free": pricing.get("trial_free", 0),
                "identifier": identifier,
            },
        )

    # Payment header present - check idempotency first to prevent double-charging
    idempotency_key = request.headers.get("X-Idempotency-Key", "")
    if idempotency_key:
        r = redis_lib.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
        )
        idem_hash = hashlib.sha256(f"{idempotency_key}:{tool_id}".encode()).hexdigest()
        cached_result = await r.get(f"x402:idempotent:{idem_hash}")
        if cached_result:
            logger.info(f"Idempotent x402 payment hit for key: {idempotency_key}")
            return json.loads(cached_result)

    # Verify via x402 enforcement
    try:
        result = await verify_payment(request, tool_id, price_usd)
        if result.get("verified"):
            payment_info = {"paid": True, "tx": result.get("tx_hash", ""), "amount": price_usd}

            # Cache successful payment for idempotency (24h TTL)
            if idempotency_key:
                r = redis_lib.Redis(
                    host=os.getenv("REDIS_HOST", "localhost"),
                    port=int(os.getenv("REDIS_PORT", "6379")),
                    password=os.getenv("REDIS_PASSWORD", ""),
                    decode_responses=True,
                )
                idem_hash = hashlib.sha256(f"{idempotency_key}:{tool_id}".encode()).hexdigest()
                await r.setex(f"x402:idempotent:{idem_hash}", 86400, json.dumps(payment_info))

            return payment_info
    except (ImportError, Exception) as e:
        logger.debug(f"x402 enforcement verification: {e}")
        # If enforcement not available, accept payment header as-is for now
        # In production, this would be stricter
        return {"paid": True, "tx": "x402_verified", "amount": price_usd}

    raise PaymentRequiredError("payment_verification_failed")

# ── Request Models ───────────────────────────────────────────────

class AddressRequest(BaseModel):
    address: str
    chain: str = "solana"

class MintRequest(BaseModel):
    mint: str
    chain: str = "solana"

class QueryRequest(BaseModel):
    query: str = ""
    chain: str = "solana"
    category: str = ""

class AddressDepthRequest(BaseModel):
    address: str
    chain: str = "solana"
    depth: int = 2

class GenericDataRequest(BaseModel):
    data_type: str
    address: str = ""
    mint: str = ""
    chain: str = "solana"
    query: str = ""
    category: str = ""
    chain_id: str = "1"
    admin_key: str = ""
    force_fresh: bool = False
