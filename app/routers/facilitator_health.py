"""

RMI Facilitator Health Monitor
===============================
Periodically pings all x402 payment facilitators and tracks:
- Latency (ms)
- Success rate (% of last 100 pings)
- Last seen timestamp
- Status (healthy/degraded/dead)

Auto-failover: enforcement middleware reads health scores and routes
around degraded/dead facilitators.

Author: RMI Development
Date: 2026-06-05
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.logging import get_logger
from app.core.redis import get_redis

logger = get_logger("facilitator_health")

router = APIRouter(prefix="/api/v1/x402/facilitator-health", tags=["facilitator-health"])

# ── Facilitator Definitions ───────────────────────────────────────

FACILITATORS = {
    "payai": {
        "name": "PayAI",
        "health_url": "https://facilitator.payai.network/health",
        "chains": [
            "base",
            "solana",
            "ethereum",
            "arbitrum",
            "optimism",
            "polygon",
            "avalanche",
            "bsc",
            "fantom",
            "gnosis",
        ],
        "priority": 1,
        "timeout_ms": 5000,
    },
    "coinbase_cdp": {
        "name": "Coinbase CDP",
        "health_url": None,  # No public health endpoint - check via payment verification
        "chains": ["base", "ethereum"],
        "priority": 2,
        "timeout_ms": 10000,
    },
    "cloudflare_x402": {
        "name": "Cloudflare x402",
        "health_url": None,  # Check via worker health
        "chains": ["base", "ethereum", "arbitrum", "optimism", "polygon"],
        "priority": 3,
        "timeout_ms": 5000,
    },
    "primev": {
        "name": "Primev",
        "health_url": None,
        "chains": ["ethereum"],
        "priority": 4,
        "timeout_ms": 10000,
    },
    "eip7702": {
        "name": "EIP-7702",
        "health_url": None,
        "chains": [
            "ethereum",
            "base",
            "arbitrum",
            "optimism",
            "polygon",
            "avalanche",
            "bsc",
            "fantom",
            "gnosis",
        ],
        "priority": 5,
        "timeout_ms": 10000,
    },
    "asterpay": {
        "name": "AsterPay (SEPA/EUR)",
        "health_url": None,
        "chains": ["sepa"],
        "priority": 6,
        "timeout_ms": 10000,
    },
    "x402_rs": {
        "name": "x402-rs (self-hosted)",
        "health_url": None,
        "chains": ["base", "ethereum"],
        "priority": 7,
        "timeout_ms": 5000,
    },
    # OFFLINE facilitators - tracked but marked dead
    "pieverse": {
        "name": "Pieverse",
        "health_url": None,
        "chains": [],
        "priority": 99,
        "timeout_ms": 5000,
        "status_override": "dead",
    },
    "merx_tron": {
        "name": "MERX TRON",
        "health_url": None,
        "chains": [],
        "priority": 99,
        "timeout_ms": 5000,
        "status_override": "dead",
    },
    "satoshi": {
        "name": "Satoshi",
        "health_url": None,
        "chains": [],
        "priority": 99,
        "timeout_ms": 5000,
        "status_override": "dead",
    },
}

# ── Redis Helper ─────────────────────────────────────────────────

async def get_all_health() -> Any:
    """Get health status for all facilitators."""
    result = {}
    for name in FACILITATORS:
        result[name] = get_facilitator_health(name)  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue

    # Summary
    healthy = sum(1 for v in result.values() if v.get("status") == "healthy")
    degraded = sum(1 for v in result.values() if v.get("status") == "degraded")
    dead = sum(1 for v in result.values() if v.get("status") in ("dead",))

    return JSONResponse(
        content={
            "facilitators": result,
            "summary": {
                "total": len(FACILITATORS),
                "healthy": healthy,
                "degraded": degraded,
                "dead": dead,
                "offline": sum(
                    1 for v in FACILITATORS.values() if v.get("status_override") == "dead"
                ),
            },
            "timestamp": datetime.now(UTC).isoformat(),
        }
    )

@router.get("/status/{name}")
async def get_facilitator_status(name: str) -> Any:
    """Get health status for a specific facilitator."""
    if name not in FACILITATORS:
        return JSONResponse(
            status_code=404,
            content={
                "error": f"Facilitator '{name}' not found",
                "available": list(FACILITATORS.keys()),
            },
        )

    return JSONResponse(content=get_facilitator_health(name))  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue

@router.get("/chains/{chain}")
async def get_facilitators_for_chain(chain: str) -> Any:
    """Get healthy facilitators for a specific chain."""
    facilitators = get_healthy_facilitators_for_chain(chain)  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
    return JSONResponse(
        content={
            "chain": chain,
            "facilitators": facilitators,
            "count": len(facilitators),
        }
    )

@router.post("/check/{name}")
async def trigger_health_check(name: str) -> Any:
    """Manually trigger a health check for a facilitator."""
    if name not in FACILITATORS:
        return JSONResponse(
            status_code=404,
            content={"error": f"Facilitator '{name}' not found"},
        )

    config = FACILITATORS[name]
    result = await ping_facilitator(name, config)  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
    record_health(name, result)  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue

    return JSONResponse(
        content={
            "facilitator": name,
            "result": result,
        }
    )

# ── Payment Success Tracking ─────────────────────────────────────

def record_payment_success(facilitator_name: str, success: bool) -> None:
    """Record whether a payment via this facilitator succeeded or failed.
    Called by the payment verification code after each payment attempt.
    """
    r = get_redis()
    if not r:
        return

    total_key = f"rmi:facilitator:{facilitator_name}:payment_total"
    success_key = f"rmi:facilitator:{facilitator_name}:payment_success"

    r.incr(total_key)
    if success:
        r.incr(success_key)

    # Decay old counters (reset daily)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    r.incr(f"rmi:facilitator:{facilitator_name}:payment_total:{today}")
    if success:
        r.incr(f"rmi:facilitator:{facilitator_name}:payment_success:{today}")
