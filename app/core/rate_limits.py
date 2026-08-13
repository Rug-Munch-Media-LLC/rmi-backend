"""
Central Rate Limiting Configuration
=====================================

Single source of truth for ALL rate limits across the backend.

Architecture:
  Tier-based → per-user limits (FREE/PRO/ENTERPRISE)
  Category-based → per-endpoint path limits (scan, ai, x402, etc.)
  Override → specific endpoint exceptions
  x402 bypass → paid calls skip rate limits entirely

Migration status:
  - UnifiedRateLimiter (app.core.rate_limiter) → primary token-bucket implementation
  - slowapi (app.core.slowrate) → auth brute-force protection only
  - Old files (app/middleware/rate_limit.py, app/scan_rate_limiter.py) → REMOVED
  - THIS MODULE → the config both implementations reference

Usage:
    from app.core.rate_limits import (
        TIER_LIMITS, CATEGORY_LIMITS, PATH_CATEGORIES,
        resolve_category, resolve_tier_limit, TIER
    )

    tier = get_user_tier(user_id)
    limits = resolve_tier_limit(tier, "/api/v1/scanner/token")
    # Returns: {"requests_per_minute": 10, "requests_per_day": 100, "burst": 2, ...}
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

# ═══════════════════════════════════════════════════════════════════════════
# TIERS
# ═══════════════════════════════════════════════════════════════════════════


class Tier(StrEnum):
    """Usage tiers with increasing rate limits."""

    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


# ═══════════════════════════════════════════════════════════════════════════
# TIER LIMITS — per-user daily/minute quotas
# ═══════════════════════════════════════════════════════════════════════════

TIER_LIMITS: dict[Tier, dict[str, int | float | list[str]]] = {
    Tier.FREE: {
        "requests_per_day": 100,
        "requests_per_minute": 10,
        "sentinel_scans_per_day": 10,
        "ai_chats_per_day": 20,
        "databus_queries_per_day": 50,
        "concurrent_requests": 2,
        "burst_multiplier": 0.4,  # burst = max(1, rpm * multiplier)
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
        "burst_multiplier": 0.25,
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
        "burst_multiplier": 0.2,
        "price_monthly_usd": 499,
        "features": ["all", "custom_models", "white_label", "dedicated_support", "sla"],
    },
}

DEFAULT_TIER = Tier.FREE

# ═══════════════════════════════════════════════════════════════════════════
# CATEGORY LIMITS — per-endpoint-category rate limits (token-bucket)
# ═══════════════════════════════════════════════════════════════════════════

# (requests_per_minute, burst_multiplier)
# burst = max(1, requests_per_minute * burst_multiplier)
CATEGORY_LIMITS: dict[str, tuple[int, float]] = {
    "health": (1000, 0.2),  # 1000/min,  burst 200
    "auth": (5, 0.4),  # 5/min,     burst 2
    "x402": (1000, 0.2),  # 1000/min,  burst 200 — paid calls are cheap
    "scan": (20, 0.25),  # 20/min,    burst 5
    "ai": (10, 0.3),  # 10/min,    burst 3
    "market": (60, 0.25),  # 60/min,    burst 15
    "public": (100, 0.25),  # 100/min,   burst 25
    "mcp": (30, 0.25),  # 30/min,    burst 7
    "admin": (10, 0.2),  # 10/min,    burst 2
    "protection": (60, 0.25),  # 60/min,    burst 15
    "default": (60, 0.25),  # 60/min,    burst 15
}

# ═══════════════════════════════════════════════════════════════════════════
# PATH → CATEGORY MAPPING
# ═══════════════════════════════════════════════════════════════════════════

PATH_CATEGORIES: dict[str, str] = {
    # Health / infrastructure
    "/health": "health",
    "/live": "health",
    "/ready": "health",
    "/metrics": "health",
    # Auth
    "/register": "auth",
    "/login": "auth",
    "/2fa": "auth",
    "/forgot-password": "auth",
    "/auth": "auth",
    # x402 / payments — always bypass normal rate limits
    "/api/v1/x402": "x402",
    "/pay": "x402",
    "/affiliates": "x402",
    "/usage": "x402",
    # Scanner / analysis endpoints
    "/api/v1/scanner": "scan",
    "/api/v1/contract-analyzer": "scan",
    "/api/v1/death-clock": "scan",
    "/api/v1/rugmaps": "scan",
    "/api/v1/honeypot-map": "scan",
    "/api/v1/tvl-verify": "scan",
    "/api/v1/whale-alerts": "scan",
    "/api/v1/criminal-clusters": "scan",
    "/api/v1/address-profiler": "scan",
    "/api/v1/mev-advisory": "scan",
    "/api/v1/wallet-clusters": "scan",
    # AI / chat
    "/api/v1/ai": "ai",
    "/api/v1/chat": "ai",
    # Market / news
    "/api/v1/market": "market",
    "/api/v1/news": "market",
    "/api/v1/bulletin": "market",
    "/api/v1/bulletin-board": "market",
    "/api/v1/catalog": "market",
    # Public data
    "/api/v1/token": "public",
    "/api/v1/wallet": "public",
    "/api/v1/labels": "public",
    # Protection suite
    "/api/v1/protect": "protection",
    # MCP protocol
    "/mcp": "mcp",
    # Admin
    "/api/v1/admin": "admin",
    "/api/v1/_test": "admin",
}

# ═══════════════════════════════════════════════════════════════════════════
# ENDPOINT OVERRIDES — specific routes that differ from category defaults
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class EndpointOverride:
    """Override rate limits for a specific endpoint path."""

    path: str
    requests_per_minute: int
    burst: int
    reason: str  # Why this override exists


ENDPOINT_OVERRIDES: list[EndpointOverride] = [
    # Heavy scan endpoints — lower rate to protect upstream APIs
    EndpointOverride(
        path="/api/v1/scanner/deep",
        requests_per_minute=5,
        burst=2,
        reason="Deep scan is resource-intensive; limit to 5/min",
    ),
    # Webhook receivers — need higher burst for batch arrivals
    EndpointOverride(
        path="/api/v1/webhook",
        requests_per_minute=200,
        burst=50,
        reason="Webhook receivers need high burst for batch events",
    ),
    # x402 sandbox — unrestricted for testing
    EndpointOverride(
        path="/x402/sandbox",
        requests_per_minute=10000,
        burst=5000,
        reason="Sandbox environment — no effective limit",
    ),
]

# ═══════════════════════════════════════════════════════════════════════════
# x402 CONFIG — paid calls bypass rate limits
# ═══════════════════════════════════════════════════════════════════════════

X402_BYPASS_LIMITS = True  # x402 paid calls skip all rate limiting
X402_VERIFY_URL = os.getenv(
    "X402_VERIFY_URL",
    "http://localhost:8000/api/v1/x402/verify",
)

# ═══════════════════════════════════════════════════════════════════════════
# RESOLVERS
# ═══════════════════════════════════════════════════════════════════════════


def resolve_category(path: str) -> str:
    """Resolve a request path to a rate limit category.

    Uses longest-prefix match so more specific paths win.
    Falls back to 'default'.
    """
    # Sort by descending length so e.g. /api/v1/scanner/deep matches before /api/v1/scanner
    for prefix, category in sorted(PATH_CATEGORIES.items(), key=lambda x: -len(x[0])):
        if path.startswith(prefix):
            return category
    return "default"


def resolve_category_limit(category: str) -> tuple[int, int]:
    """Resolve (requests_per_minute, burst) for a category.

    Falls back to 'default' if category not found.
    """
    if category in CATEGORY_LIMITS:
        rpm, burst_factor = CATEGORY_LIMITS[category]
        burst = max(1, int(rpm * burst_factor))
        return rpm, burst
    rpm, burst_factor = CATEGORY_LIMITS["default"]
    burst = max(1, int(rpm * burst_factor))
    return rpm, burst


def resolve_path_limit(path: str) -> tuple[int, int]:
    """Resolve (requests_per_minute, burst) for a full request path.

    Checks endpoint overrides first, then falls back to category resolution.
    """
    # Check overrides
    for override in ENDPOINT_OVERRIDES:
        if path.startswith(override.path):
            return override.requests_per_minute, override.burst

    # Fall back to category
    category = resolve_category(path)
    return resolve_category_limit(category)


def resolve_tier_limit(tier: Tier, path: str) -> dict[str, Any]:
    """Resolve the effective rate limit for a (tier, path) combination.

    Returns a dict with:
        requests_per_minute, requests_per_day, burst, concurrent_requests,
        features, tier_name
    """
    tier_config = TIER_LIMITS.get(tier, TIER_LIMITS[DEFAULT_TIER])
    path_rpm, path_burst = resolve_path_limit(path)

    # The effective RPM is the MIN of tier RPM and path RPM
    effective_rpm = min(
        int(cast("int | float", tier_config["requests_per_minute"])),
        path_rpm,
    )

    return {
        "tier": tier.value,
        "requests_per_minute": effective_rpm,
        "requests_per_day": tier_config["requests_per_day"],
        "burst": path_burst,
        "concurrent_requests": tier_config["concurrent_requests"],
        "features": list(cast("list[str]", tier_config["features"])),
        "price_monthly_usd": tier_config["price_monthly_usd"],
    }


def is_x402_endpoint(path: str) -> bool:
    """Check if a path is an x402 payment endpoint (bypasses rate limits)."""
    return resolve_category(path) == "x402"
