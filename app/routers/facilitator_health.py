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

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import Any

import httpx

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


def _health_redis_key(name: str) -> str:
    return f"rmi:facilitator:health:{name}"


def _status_override(name: str) -> str | None:
    """Return status_override if the facilitator is pinned offline."""
    return FACILITATORS.get(name, {}).get("status_override")


def get_facilitator_health(name: str) -> dict[str, Any]:
    """Read the most recently recorded health snapshot for a facilitator.

    Returns a dict with status (healthy/degraded/dead/unknown), latency,
    success rate and last-seen timestamp. Facilitators pinned with
    ``status_override=dead`` always report dead.
    """
    override = _status_override(name)
    if override:
        return {
            "name": name,
            "status": override,
            "latency_ms": None,
            "success_rate": None,
            "last_seen": None,
            "checked_at": datetime.now(UTC).isoformat(),
            "note": "pinned offline",
        }

    r = get_redis()
    if not r:
        return {
            "name": name,
            "status": "unknown",
            "latency_ms": None,
            "success_rate": None,
            "last_seen": None,
            "checked_at": datetime.now(UTC).isoformat(),
        }

    raw = r.get(_health_redis_key(name))
    if not raw:
        return {
            "name": name,
            "status": "unknown",
            "latency_ms": None,
            "success_rate": None,
            "last_seen": None,
            "checked_at": datetime.now(UTC).isoformat(),
        }

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {
            "name": name,
            "status": "unknown",
            "latency_ms": None,
            "success_rate": None,
            "last_seen": None,
            "checked_at": datetime.now(UTC).isoformat(),
        }


def get_healthy_facilitators_for_chain(chain: str) -> list[str]:
    """List facilitator names that currently support a chain and are healthy."""
    healthy = []
    for name, config in FACILITATORS.items():
        if chain not in config.get("chains", []):
            continue
        health = get_facilitator_health(name)
        if health.get("status") == "healthy":
            healthy.append(name)
    return sorted(healthy, key=lambda n: FACILITATORS[n].get("priority", 99))


async def ping_facilitator(name: str, config: dict[str, Any]) -> dict[str, Any]:
    """Ping a facilitator's health endpoint and measure latency."""
    health_url = config.get("health_url")
    if not health_url:
        return {
            "name": name,
            "status": "unknown",
            "latency_ms": None,
            "success_rate": None,
            "last_seen": None,
            "checked_at": datetime.now(UTC).isoformat(),
            "note": "no public health endpoint",
        }

    timeout_ms = config.get("timeout_ms", 5000)
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout_ms / 1000) as client:
            resp = await client.get(health_url)
        latency_ms = round((time.monotonic() - started) * 1000, 2)
        status = "healthy" if resp.status_code == 200 else "degraded"
        if resp.status_code >= 500:
            status = "dead"
        return {
            "name": name,
            "status": status,
            "latency_ms": latency_ms,
            "success_rate": None,
            "last_seen": datetime.now(UTC).isoformat(),
            "checked_at": datetime.now(UTC).isoformat(),
        }
    except (httpx.HTTPError, OSError) as exc:
        return {
            "name": name,
            "status": "dead",
            "latency_ms": None,
            "success_rate": None,
            "last_seen": None,
            "checked_at": datetime.now(UTC).isoformat(),
            "error": str(exc)[:200],
        }


def record_health(name: str, result: dict[str, Any]) -> None:
    """Persist a health snapshot to Redis and refresh the summary key."""
    r = get_redis()
    if not r:
        return
    try:
        r.set(_health_redis_key(name), json.dumps(result), ex=86400)
        summary: dict[str, Any] = {}
        for n in FACILITATORS:
            summary[n] = get_facilitator_health(n)
        r.set("rmi:facilitator:summary", json.dumps(summary), ex=300)
    except (ConnectionError, OSError, TypeError) as exc:
        logger.warning("record_health_failed name=%s err=%s", name, exc)


async def health_check_loop() -> None:
    """Background loop: ping every facilitator every 60s and persist health."""
    logger.info("facilitator_health_loop_started")
    while True:
        for name, config in FACILITATORS.items():
            if config.get("status_override"):
                continue
            try:
                result = await ping_facilitator(name, config)
                record_health(name, result)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("facilitator_health_ping_failed name=%s err=%s", name, exc)
        await asyncio.sleep(60)


async def get_all_health() -> Any:
    """Get health status for all facilitators."""
    result = {}
    for name in FACILITATORS:
        result[name] = get_facilitator_health(name)

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
                "offline": sum(1 for v in FACILITATORS.values() if v.get("status_override") == "dead"),
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

    return JSONResponse(content=get_facilitator_health(name))


@router.get("/chains/{chain}")
async def get_facilitators_for_chain(chain: str) -> Any:
    """Get healthy facilitators for a specific chain."""
    facilitators = get_healthy_facilitators_for_chain(chain)
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
    result = await ping_facilitator(name, config)
    record_health(name, result)

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
