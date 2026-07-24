# DEPRECATED: Use app.core.rate_limiter (UnifiedRateLimiter) instead.
# Remove after migration verified. Consolidates 7 rate limiters into 1.
from __future__ import annotations

"""
RMI Freemium Scan Rate Limiter
===============================
- 5 free token/wallet scans per day per identity (IP for anonymous, user ID for auth)
- Premium users: unlimited or per-tier limits
- Internal/cron API key: unlimited bypass
- x402 paid calls: bypass (already paid)
- Returns clear upsell messaging when limit is hit
"""

import os
from datetime import UTC, datetime, timedelta
from typing import Any

from redis import asyncio as aioredis

from app.config import settings
from app.core.internal_key import INTERNAL_API_KEY
from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Configuration ──

# ── In-memory fallback (used when Redis is unavailable) ──
_mem_usage: dict[str, tuple[int, float]] = {}

def _check_memory_fallback(identity: str, daily_limit: int) -> dict:
    """In-memory rate limit check — never fails open."""
    from time import monotonic
    now = monotonic()
    count, win_start = _mem_usage.get(identity, (0, now))
    if now - win_start > 86400:
        count = 0
        win_start = now
    if count >= daily_limit:
        return {
            "allowed": False,
            "remaining": 0,
            "limit": daily_limit,
            "upsell": {
                "title": "Scan limit reached (in-memory fallback)",
                "message": f"Free limit of {daily_limit} scans/day reached.",
                "upgrade_url": "/api/v1/x402-tools/upgrade",
                "scans_used": count,
                "daily_limit": daily_limit,
            },
        }
    count += 1
    _mem_usage[identity] = (count, win_start)
    return {
        "allowed": True,
        "remaining": daily_limit - count,
        "limit": daily_limit,
        "upsell": None,
    }

FREE_DAILY_LIMIT = int(os.getenv("FREE_SCAN_LIMIT", "5"))
PRO_DAILY_LIMIT = int(os.getenv("PRO_SCAN_LIMIT", "100"))
ELITE_DAILY_LIMIT = int(os.getenv("ELITE_SCAN_LIMIT", "0"))  # 0 = unlimited

# Redis setup
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
REDIS_DB = os.getenv("REDIS_USAGE_DB", "2")  # Separate DB for usage tracking

# Human subscriptions (monthly, site access)
SCAN_PACKS = {
    "starter_monthly": {
        "scans": 50,
        "price_usd": 29.99,
        "price_atoms": "29990000",
        "description": "50 scans/day - Starter tier",
    },
    "pro_monthly": {
        "scans": 200,
        "price_usd": 49.99,
        "price_atoms": "49990000",
        "description": "200 scans/day - Pro tier",
    },
    "elite_monthly": {
        "scans": 1000,
        "price_usd": 89.99,
        "price_atoms": "89990000",
        "description": "1000 scans/day + API access - Elite tier",
    },
}

# Bot/API prepaid packs (one-time, no expiry, for AI agents and trading bots)
BOT_PACKS = {
    "bot_scout": {
        "calls": 200,
        "price_usd": 9.99,
        "price_atoms": "9990000",
        "description": "200 API calls - Scout pack for testing bots",
    },
    "bot_hunter": {
        "calls": 1500,
        "price_usd": 49.99,
        "price_atoms": "49990000",
        "description": "1,500 API calls - Hunter pack for active bots (33% savings)",
    },
    "bot_whale": {
        "calls": 10000,
        "price_usd": 199.99,
        "price_atoms": "199990000",
        "description": "10,000 API calls - Whale pack for production bots (60% savings)",
    },
    "bot_api_plan": {
        "calls": 50000,
        "price_usd": 499.00,
        "price_atoms": "499000000",
        "description": "50,000 calls/month - API Plan for SaaS/funds ($0.01/call)",
    },
}

# Trial abuse prevention
TRIAL_CONFIG = {
    "max_free_per_fingerprint": 3,  # 3 free calls per device fingerprint per tool
    "fingerprint_window_hours": 168,  # 7-day fingerprint window
    "max_free_per_ip": 15,  # 15 total free calls per IP across all tools
    "ip_window_hours": 24,  # 24-hour IP window
    "cooldown_after_exhaust": 72,  # 72-hour cooldown after exhausting all trials
}

PREMIUM_UPSELL = {
    "free_limit_reached": {
        "message": f"You've used your {FREE_DAILY_LIMIT} free scans for today. Unlock Starter (50/day), Pro (200/day), or Elite (1000/day + API). $CRM/$CRYPTORUGMUNCH holders get 50% off for 3 months.",
        "upgrade_url": settings.pricing_url,
        "tiers": {
            "free": {
                "daily_limit": FREE_DAILY_LIMIT,
                "price": "$0",
                "features": ["5 scans/day", "Basic safety score", "DEX market data"],
            },
            "starter": {
                "daily_limit": 50,
                "price": "$29.99/mo",
                "features": [
                    "50 scans/day",
                    "Full risk analysis",
                    "Mint/freeze authority",
                    "Honeypot detection",
                    "LP verification",
                ],
            },
            "pro": {
                "daily_limit": 200,
                "price": "$49.99/mo",
                "features": [
                    "200 scans/day",
                    "All Starter features",
                    "Bundle detection",
                    "MEV analysis",
                    "Whale tracking",
                    "Dev reputation",
                ],
            },
            "elite": {
                "daily_limit": 1000,
                "price": "$89.99/mo",
                "features": [
                    "1000 scans/day",
                    "All Pro features",
                    "Full API access",
                    "Priority support",
                    "SENTINEL deep modules",
                ],
            },
            "enterprise": {
                "daily_limit": "unlimited",
                "price": "Custom",
                "features": [
                    "Unlimited API access",
                    "Dedicated support",
                    "Custom integrations",
                    "SLA guarantee",
                    "Contact: biz@rugmunch.io",
                ],
            },
            "holder_discount": {
                "discount": "50%",
                "duration": "3 months",
                "eligibility": "$CRM and $CRYPTORUGMUNCH holders",
                "verification": "v2 airdrop checker",
                "note": "Verify token holdings via the airdrop checker to claim your 50% discount code",
            },
            "scan_packs": {
                k: {
                    "price": v["price_usd"],
                    "scans": v["scans"] or "unlimited",
                    "description": v["description"],
                }
                for k, v in SCAN_PACKS.items()
            },
        },
    }
}

def _get_redis() -> aioredis.Redis:  # type: ignore[type-arg]
    """Get async Redis connection for usage tracking."""
    return aioredis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        db=int(REDIS_DB),
        decode_responses=True,
    )

def _today_key() -> str:
    """Return today's date key for Redis."""
    return datetime.now(UTC).strftime("%Y-%m-%d")

def _identity_key(identity_type: str, identity: str) -> str:
    """Build Redis key for an identity's daily usage."""
    return f"rmi:scan_usage:{_today_key()}:{identity_type}:{identity}"

async def check_scan_limit(
    identity: str,
    identity_type: str = "ip",
    tier: str = "free",
    is_internal: bool = False,
    is_x402_paid: bool = False,
) -> dict[str, Any]:
    """
    Check if a scan request is within limits.

    Args:
        identity: IP address or user ID
        identity_type: "ip" or "user"
        tier: "free", "pro", "elite"
        is_internal: True for internal/cron calls (bypass all limits)
        is_x402_paid: True for x402 paid calls (bypass all limits)

    Returns:
        {"allowed": bool, "remaining": int, "limit": int, "upsell": dict|None}
    """
    # Internal calls and x402 paid calls always pass
    if is_internal or is_x402_paid:
        return {"allowed": True, "remaining": -1, "limit": -1, "upsell": None}

    # Determine daily limit
    if tier == "elite":
        daily_limit = ELITE_DAILY_LIMIT  # 0 = unlimited
    elif tier == "pro":
        daily_limit = PRO_DAILY_LIMIT  # 100
    else:
        daily_limit = FREE_DAILY_LIMIT  # 5

    # Unlimited (elite)
    if daily_limit == 0:
        return {"allowed": True, "remaining": -1, "limit": 0, "upsell": None}

    # Check Redis for today's usage
    try:
        r = _get_redis()
        key = _identity_key(identity_type, identity)
        current_val = await r.get(key)
        current = int(current_val or "0")

        if current >= daily_limit:
            upsell = PREMIUM_UPSELL["free_limit_reached"]
            # Add usage stats to upsell
            upsell_with_stats = {
                **upsell,
                "scans_used": current,
                "daily_limit": daily_limit,
                "resets_at": (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z"),
            }
            return {
                "allowed": False,
                "remaining": 0,
                "limit": daily_limit,
                "upsell": upsell_with_stats,
            }

        return {
            "allowed": True,
            "remaining": daily_limit - current,
            "limit": daily_limit,
            "upsell": None,
        }

    except Exception as e:
        logger.error("Rate limit check failed: %s, using in-memory fallback", e)
        # On Redis failure, fall back to in-memory counter instead of failing open
        return _check_memory_fallback(identity, daily_limit)

async def increment_scan_usage(
    identity: str,
    identity_type: str = "ip",
    tier: str = "free",
    is_internal: bool = False,
    is_x402_paid: bool = False,
) -> dict[str, Any]:
    """
    Record a scan usage. Call AFTER a successful scan.

    Returns:
        {"recorded": bool, "total_today": int, "remaining": int}
    """
    # Don't track internal or x402 paid calls
    if is_internal or is_x402_paid:
        return {"recorded": False, "total_today": 0, "remaining": -1}

    daily_limit = (
        ELITE_DAILY_LIMIT
        if tier == "elite"
        else (PRO_DAILY_LIMIT if tier == "pro" else FREE_DAILY_LIMIT)
    )

    try:
        r = _get_redis()
        key = _identity_key(identity_type, identity)
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, 86400 * 2)  # Expire after 2 days
        results = await pipe.execute()
        total = results[0]
        remaining = (daily_limit - total) if daily_limit > 0 else -1

        return {"recorded": True, "total_today": total, "remaining": max(0, remaining)}

    except Exception as e:
        logger.error("Usage recording failed: %s", e)
        return {"recorded": False, "total_today": 0, "remaining": -1}

async def get_usage_stats(identity: str, identity_type: str = "ip") -> dict[str, Any]:
    """Get today's usage stats for an identity."""
    try:
        r = _get_redis()
        key = _identity_key(identity_type, identity)
        current_val = await r.get(key)
        current = int(current_val or "0")
        return {
            "identity": identity,
            "identity_type": identity_type,
            "scans_today": current,
            "free_limit": FREE_DAILY_LIMIT,
            "pro_limit": PRO_DAILY_LIMIT,
            "elite_limit": "unlimited",
            "remaining_free": max(0, FREE_DAILY_LIMIT - current),
            "resets_at": (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z"),
        }
    except Exception as e:
        logger.error("Usage stats failed: %s", e)
        return {"identity": identity, "scans_today": 0, "error": str(e)}

def is_internal_request(request) -> bool:
    """Check if request is from internal/cron (API key header or internal IP)."""
    # Check internal API key header
    api_key = request.headers.get("X-RMI-Key", "") if hasattr(request, "headers") else ""
    if api_key == INTERNAL_API_KEY:
        return True

    # Check for x402 payment (already paid)
    x402_version = (
        request.headers.get("X-Payment-Version", "") if hasattr(request, "headers") else ""
    )
    if x402_version:
        return False  # x402 is not internal, but it's paid - handled separately

    # Check for authenticated user (Authorization header)
    auth = request.headers.get("Authorization", "") if hasattr(request, "headers") else ""
    if auth.startswith("Bearer "):
        return False  # Authenticated user - check their tier

    return False

def get_identity(request) -> tuple[str, str]:
    """
    Extract identity from request. Returns (identity, identity_type).

    Authenticated users: user ID from JWT
    Anonymous: IP address
    """
    # Try JWT auth first
    auth = request.headers.get("Authorization", "") if hasattr(request, "headers") else ""
    if auth.startswith("Bearer "):
        try:
            from app.auth import _verify_jwt

            payload = _verify_jwt(auth[7:])
            if payload:
                return payload.get("id", auth[7:][:32]), "user"
        except (ValueError, TypeError):
            pass

    # Fall back to IP
    client_ip = request.client.host if hasattr(request, "client") and request.client else "unknown"
    forwarded = request.headers.get("X-Forwarded-For", "") if hasattr(request, "headers") else ""
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()

    return client_ip, "ip"

def is_x402_paid_request(request) -> bool:
    """Check if request has a valid x402 payment (handled by enforcement middleware)."""
    # x402 middleware sets this header after successful payment verification
    x402_paid = request.headers.get("X-Payment-Verified", "") if hasattr(request, "headers") else ""
    return x402_paid.lower() == "true"
