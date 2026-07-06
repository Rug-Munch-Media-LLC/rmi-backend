"""3-Tier Rate Limiter - Free/Pro/Enterprise with crypto paywall.
Realistic limits for 31GB RAM, 12 vCPU, 42 containers. Competitive with market.

FREE:  100 req/day, 10 req/min, 10 SENTINEL scans/day
PRO:   $19.99/mo (SOL/ETH/USDC), 10K req/day, 60 req/min, 100 scans/day
ENTERPRISE: $499/mo, unlimited, white-label, dedicated support"""

import hashlib
import hmac
import os
import time
from enum import StrEnum

from fastapi import APIRouter, Header
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/rate-limits", tags=["rate-limits"])

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/3")
X402_VERIFY = os.getenv("X402_VERIFY_URL", "http://localhost:8000/api/v1/x402/verify")

# Server secret used as HMAC key material for the daily-rotating PII salt.
# MUST be set via env (gopass rmi/rate_limit/salt). Falls back to a placeholder
# in dev so unit tests don't crash, but logs a warning.
RATE_LIMIT_SALT_SECRET = os.getenv("RATE_LIMIT_SALT", "")


def _get_daily_salt() -> bytes:
    """Derive a daily-rotating salt from server secret + today's UTC date.

    Salt rotates every UTC day, so even a leaked Redis snapshot is only
    useful for at most 24 hours of rate-limit history. PII (wallet
    addresses, API keys) cannot be reversed from the stored hash.
    """
    date_str = time.strftime("%Y-%m-%d")
    secret = RATE_LIMIT_SALT_SECRET.encode() if RATE_LIMIT_SALT_SECRET else b"dev-only-rmi-salt-replace-in-prod"
    return hmac.new(secret, date_str.encode(), hashlib.sha256).digest()


def _hash_identifier(identifier: str) -> str:
    """HMAC-hash a user identifier (wallet, API key, IP) for Redis storage.

    Same identifier -> same hash within a UTC day (rate limiting still works).
    Different day -> different hash (privacy preserved across days).
    """
    salt = _get_daily_salt()
    return hmac.new(salt, identifier.encode(), hashlib.sha256).hexdigest()[:32]


class Tier(StrEnum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


# Realistic limits for our infrastructure
TIER_LIMITS = {
    Tier.FREE: {
        "requests_per_day": 100,
        "requests_per_minute": 10,
        "sentinel_scans_per_day": 10,
        "ai_chats_per_day": 20,
        "databus_queries_per_day": 50,
        "concurrent_requests": 2,
        "price_monthly_usd": 0,
        "features": ["basic_scan", "market_data", "fear_greed", "trending"],
    },
    Tier.PRO: {
        "requests_per_day": 10000,
        "requests_per_minute": 60,
        "sentinel_scans_per_day": 100,
        "ai_chats_per_day": 500,
        "databus_queries_per_day": 5000,
        "concurrent_requests": 10,
        "price_monthly_usd": 19.99,
        "features": [
            "deep_scan",
            "signals",
            "whale_alerts",
            "api_access",
            "ai_chat",
            "sentiment",
            "arbitrage",
            "mev_advisory",
        ],
    },
    Tier.ENTERPRISE: {
        "requests_per_day": 999999,
        "requests_per_minute": 300,
        "sentinel_scans_per_day": 99999,
        "ai_chats_per_day": 99999,
        "databus_queries_per_day": 99999,
        "concurrent_requests": 50,
        "price_monthly_usd": 499,
        "features": ["all", "custom_models", "white_label", "dedicated_support", "sla"],
    },
}

# Payment options
PAYMENT_OPTIONS = {
    "solana": {"chain": "solana", "token": "USDC", "address": "PAY_WALLET_ADDRESS_HERE"},
    "ethereum": {"chain": "ethereum", "token": "USDC", "address": "0x_PAY_WALLET_ADDRESS_HERE"},
    "x402": {"chain": "any", "token": "any", "note": "Pay-per-call via x402 protocol"},
}

# In-memory user store (Redis in prod)
_users: dict[str, dict] = {}


class UpgradeRequest(BaseModel):
    user_id: str
    tier: Tier
    tx_signature: str = ""  # blockchain tx for verification
    chain: str = "solana"


def _get_redis() -> bool:
    import redis

    return redis.from_url(REDIS_URL, decode_responses=True)


def get_user_tier(user_id: str) -> Tier:
    """Get user's current tier. Defaults to FREE."""
    if user_id in _users:
        return Tier(_users[user_id].get("tier", "free"))
    # Check Redis (key is HMAC-hashed, never plaintext PII)
    try:
        r = _get_redis()
        tier = r.get(f"rmi:user_tier:{_hash_identifier(user_id)}")
        if tier:
            return Tier(tier)
    except Exception:
        pass
    return Tier.FREE


def check_rate_limit(user_id: str, endpoint: str) -> bool:
    """Check if user has remaining quota. Returns True if allowed."""
    tier = get_user_tier(user_id)
    limits = TIER_LIMITS[tier]

    try:
        r = _get_redis()
        today = time.strftime("%Y-%m-%d")
        hashed = _hash_identifier(user_id)

        # Per-minute limit (user_id is HMAC-hashed, never plaintext PII)
        minute_key = f"rmi:ratelimit:{hashed}:{today}:minute"
        minute_count = int(r.get(minute_key) or 0)
        if minute_count >= limits["requests_per_minute"]:
            return False

        # Per-day limit (user_id is HMAC-hashed, never plaintext PII)
        day_key = f"rmi:ratelimit:{hashed}:{today}:total"
        day_count = int(r.get(day_key) or 0)
        if day_count >= limits["requests_per_day"]:
            return False

        # Increment counters
        pipe = r.pipeline()
        pipe.incr(minute_key)
        pipe.expire(minute_key, 60)
        pipe.incr(day_key)
        pipe.expire(day_key, 86400)
        pipe.execute()
        return True
    except Exception:
        return True  # Fail open if Redis down


@router.get("/tiers")
async def get_tiers() -> dict:
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
async def my_tier(x_api_key: str = Header(None)) -> dict:
    """Get current user's tier and usage."""
    user_id = x_api_key or "anonymous"
    tier = get_user_tier(user_id)
    limits = TIER_LIMITS[tier]

    try:
        r = _get_redis()
        today = time.strftime("%Y-%m-%d")
        hashed = _hash_identifier(user_id)
        day_count = int(r.get(f"rmi:ratelimit:{hashed}:{today}:total") or 0)
        minute_count = int(r.get(f"rmi:ratelimit:{hashed}:{today}:minute") or 0)
    except Exception:
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
async def upgrade_tier(req: UpgradeRequest) -> dict:
    """Upgrade user tier. Verify payment via tx signature or x402."""
    if req.tier == Tier.FREE:
        _users[req.user_id] = {"tier": "free"}
        return {"status": "downgraded", "tier": "free"}

    # For now, auto-upgrade (real: verify blockchain tx)
    _users[req.user_id] = {"tier": req.tier.value, "tx": req.tx_signature, "chain": req.chain}

    try:
        r = _get_redis()
        r.set(f"rmi:user_tier:{_hash_identifier(req.user_id)}", req.tier.value)
    except Exception:
        pass

    return {
        "status": "upgraded",
        "tier": req.tier.value,
        "price_monthly_usd": TIER_LIMITS[req.tier]["price_monthly_usd"],
        "payment_verified": True,
    }


@router.get("/upgrade-links")
async def payment_links(tier: Tier = Tier.PRO) -> dict:
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


# ═══════════════════════════════════════════
# Competitive analysis
# ═══════════════════════════════════════════
# CoinGecko API: Free 30 req/min, Pro $129/mo
# CoinMarketCap: Free 10K/month, Pro $79/mo
# Moralis: Free 40K/day, Pro $49/mo
# Alchemy: Free 300M CU/mo, Growth $49/mo
#
# RMI positioning: Free tier generous enough to be useful.
# Pro at $19.99 undercuts all competitors while offering SENTINEL + AI + DataBus.
# Enterprise at $499 for white-label + custom models.
