"""Tier / pricing router — UNMOUNTED (preserved, not wired into the app).

Extracted from ``app/core/rate_limiter.py`` (degodding 2026-08-07).

⚠️ UNMOUNTED DEAD CODE ⚠️
This router (``/api/v1/rate-limits``: ``/tiers``, ``/my-tier``, ``/upgrade``,
``/upgrade-links``) is NOT included in ``app/mount.py`` and nothing imports
``router`` from this module. It is preserved from the old rate-limiter file
for reference / potential future billing work. Do NOT mount it without a
payments + security review — ``upgrade_tier`` does not verify tx signatures.
"""

from __future__ import annotations

import os
import time as _time
from typing import Any

from fastapi import APIRouter, Header
from pydantic import BaseModel

from app.core.logging import get_logger
from app.core.rate_limiter import (
    TIER_LIMITS,
    Tier,
    _get_tier_redis,
    _hash_identifier,
    _users,
    get_user_tier,
)

logger = get_logger("rate_limit_tiers")

router = APIRouter(prefix="/api/v1/rate-limits", tags=["rate-limits"])
X402_VERIFY = os.getenv("X402_VERIFY_URL", "http://localhost:8000/api/v1/x402/verify")

PAYMENT_OPTIONS = {
    "solana": {"chain": "solana", "token": "USDC", "address": "PAY_WALLET_ADDRESS_HERE"},
    "ethereum": {"chain": "ethereum", "token": "USDC", "address": "0x_PAY_WALLET_ADDRESS_HERE"},
    "x402": {"chain": "any", "token": "any", "note": "Pay-per-call via x402 protocol"},
}


class UpgradeRequest(BaseModel):
    user_id: str
    tier: Tier
    tx_signature: str = ""
    chain: str = "solana"


@router.get("/tiers")
async def get_tiers() -> dict[str, Any]:
    """Get all pricing tiers and features."""
    return {
        "tiers": {
            t.value: {
                "price_monthly_usd": TIER_LIMITS[t]["price_monthly_usd"],
                "requests_per_day": TIER_LIMITS[t]["requests_per_day"],
                "features": TIER_LIMITS[t]["features"],
            }
            for t in Tier
        },
        "payment_methods": list(PAYMENT_OPTIONS.keys()),
        "note": "Crypto payments only. Solana USDC, Ethereum USDC, or x402 pay-per-call.",
    }


@router.get("/my-tier")
async def my_tier(x_api_key: str = Header(None)) -> dict[str, Any]:
    """Get current user's tier and usage."""
    user_id = x_api_key or "anonymous"
    tier = get_user_tier(user_id)
    limits = TIER_LIMITS[tier]

    try:
        r = _get_tier_redis()
        today = _time.strftime("%Y-%m-%d")
        hashed = _hash_identifier(user_id)
        day_count = int(r.get(f"rmi:ratelimit:{hashed}:{today}:total") or 0)
        minute_count = int(r.get(f"rmi:ratelimit:{hashed}:{today}:minute") or 0)
    except (ConnectionError, OSError, TimeoutError):
        day_count = 0
        minute_count = 0

    return {
        "user_id": user_id,
        "tier": tier.value,
        "usage": {
            "today": day_count,
            "limit_per_day": limits["requests_per_day"],
            "remaining": max(0, limits["requests_per_day"] - day_count),
            "current_minute": minute_count,
            "limit_per_minute": limits["requests_per_minute"],
        },
        "upgrade_url": "/api/v1/rate-limits/upgrade",
    }


@router.post("/upgrade")
async def upgrade_tier(req: UpgradeRequest) -> dict[str, Any]:
    """Upgrade user tier. Verify payment via tx signature or x402."""
    if req.tier == Tier.FREE:
        _users[req.user_id] = {"tier": "free"}
        return {"status": "downgraded", "tier": "free"}

    _users[req.user_id] = {"tier": req.tier.value, "tx": req.tx_signature, "chain": req.chain}

    try:
        r = _get_tier_redis()
        r.set(f"rmi:user_tier:{_hash_identifier(req.user_id)}", req.tier.value)
    except (ConnectionError, OSError, TimeoutError):
        pass

    return {
        "status": "upgraded",
        "tier": req.tier.value,
        "price_monthly_usd": TIER_LIMITS[req.tier]["price_monthly_usd"],
        "payment_verified": True,
    }


@router.get("/upgrade-links")
async def payment_links(tier: Tier = Tier.PRO) -> dict[str, Any]:
    """Get payment links for upgrading via crypto."""
    price = TIER_LIMITS[tier]["price_monthly_usd"]
    return {
        "tier": tier.value,
        "price_usd": price,
        "payment_options": {
            "solana_usdc": {
                "chain": "solana",
                "token": "USDC",
                "amount": price,
                "note": "Send to RMI wallet. Include your user_id in memo.",
            },
            "ethereum_usdc": {
                "chain": "ethereum",
                "token": "USDC",
                "amount": price,
                "note": "Send to RMI wallet. Include your user_id in memo.",
            },
            "x402": {"note": "Pay per API call automatically. No upfront cost."},
        },
    }
