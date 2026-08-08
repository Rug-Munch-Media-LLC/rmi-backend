"""
Provider Health Dashboard + Credit Burn Tracking
=================================================
Live status of all 10 embedding providers + enterprise credit burn rate.
"""

from __future__ import annotations

import time
from typing import Any

from app.core.logging import get_logger
from app.rate_limiter import get_dispatcher

logger = get_logger("health.dashboard")


async def provider_health() -> dict[str, Any]:
    """Live health status of all embedding providers."""
    d = await get_dispatcher()
    rl = await d.tracker.stats()

    providers = []
    for p in rl["providers"]:
        # Determine health status
        day_pct = p["day_pct"]
        if not p["available"]:
            status = "exhausted" if day_pct >= 100 else "rate_limited"
        elif day_pct > 80:
            status = "warning"
        elif day_pct > 50:
            status = "active_warm"
        else:
            status = "healthy"

        providers.append(
            {
                "id": p["id"],
                "name": p["name"],
                "status": status,
                "day_used": p["day_used"],
                "day_limit": p["day_limit"],
                "day_pct": p["day_pct"],
                "minute_used": p["minute_used"],
                "minute_limit": p["minute_limit"],
                "total_calls": p["total"],
                "free": p["free"],
            }
        )

    healthy = sum(1 for p in providers if p["status"] == "healthy")
    warning = sum(1 for p in providers if p["status"] == "warning")
    degraded = sum(1 for p in providers if p["status"] in ("active_warm", "rate_limited"))
    exhausted = sum(1 for p in providers if p["status"] == "exhausted")

    return {
        "summary": {
            "total": len(providers),
            "healthy": healthy,
            "warning": warning,
            "degraded": degraded,
            "exhausted": exhausted,
        },
        "providers": providers,
        "cache": d.cache.stats(),
        "timestamp": time.time(),
    }


async def credit_burn() -> dict[str, Any]:
    """Enterprise credit burn tracking - daily rate, projected exhaustion."""
    d = await get_dispatcher()
    rl = await d.tracker.stats()

    # Calculate cost estimates
    # Gemini AI Studio: free up to 1,500/day per key
    # OpenRouter: free with credits
    # Mistral: free (1B tokens/month)

    total_calls_today = 0
    total_free_limit = 0
    provider_usage = []

    for p in rl["providers"]:
        total_calls_today += p["day_used"]
        total_free_limit += p["day_limit"] if p["free"] else 0

        cost_est = 0

        if p["day_used"] > 0:
            provider_usage.append(
                {
                    "name": p["name"],
                    "calls_today": p["day_used"],
                    "estimated_cost_usd": cost_est,
                    "free": p["free"],
                }
            )

    # $300 trial, assume 90 days started ~June 1, 2026
    trial_start = time.mktime(time.strptime("2026-06-01", "%Y-%m-%d"))
    trial_end = trial_start + 90 * 86400
    days_remaining = max(0, (trial_end - time.time()) / 86400)

    # Daily burn rate estimate
    daily_cost = sum(p["estimated_cost_usd"] for p in provider_usage)

    return {
        "trial": {
            "credits": 300,
            "days_remaining": round(days_remaining, 0),
            "exhaustion_date": "2026-08-30" if days_remaining > 0 else "now",
        },
        "today": {
            "total_calls": total_calls_today,
            "free_limit": total_free_limit,
            "utilization_pct": round(total_calls_today / max(total_free_limit, 1) * 100, 2),
            "estimated_cost_usd": daily_cost,
            "daily_burn_rate": f"${daily_cost:.6f}",
        },
        "provider_usage": provider_usage,
        "note": "Gemini, Mistral, NVIDIA, and OpenRouter are FREE. Only Vertex AI burns enterprise credits.",
    }
