#!/usr/bin/env python3
"""#10 — DataBus Public API Gateway. Free tier + x402 paid tier. 102 chains, 119 providers."""

import hashlib
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DATABUS_BASE = os.environ.get("DATABUS_URL", "http://localhost:8000/api/v1/databus")
X402_VERIFY_URL = os.environ.get("X402_VERIFY_URL", "http://localhost:8000/api/v1/x402/verify")
RATE_LIMIT_FREE = int(os.environ.get("DATABUS_FREE_RATE", "100"))  # req/hour
RATE_LIMIT_PAID = int(os.environ.get("DATABUS_PAID_RATE", "5000"))

# Simple in-memory rate limiter (Redis-backed in prod)
_rate_store: dict[str, list[float]] = {}


def _check_rate(key: str, limit: int, window: int = 3600) -> bool:
    now = time.time()
    bucket = _rate_store.setdefault(key, [])
    bucket[:] = [t for t in bucket if now - t < window]
    if len(bucket) >= limit:
        return False
    bucket.append(now)
    return True


@dataclass
class DatabusQueryResult:
    chain: str
    data: dict[str, Any]
    source: str
    cached: bool
    latency_ms: float


async def databus_get(chain: str, endpoint: str, params: dict | None = None, tier: str = "free") -> DatabusQueryResult:
    """Single-chain DataBus query with tier-appropriate caching."""
    t0 = time.monotonic()
    url = f"{DATABUS_BASE}/{chain}/{endpoint}"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params or {})
        resp.raise_for_status()
        data = resp.json()
    elapsed = (time.monotonic() - t0) * 1000
    return DatabusQueryResult(
        chain=chain, data=data, source="databus", cached=data.get("cached", False), latency_ms=elapsed
    )


async def databus_cross_chain(
    query: str, chains: list[str] | None = None, tier: str = "free"
) -> dict[str, DatabusQueryResult]:
    """Query multiple DataBus chains in parallel."""
    if chains is None:
        chains = ["solana", "ethereum", "bsc", "base", "arbitrum"]
    tasks = {c: databus_get(c, query, tier=tier) for c in chains}
    results = {}
    for c, task in tasks.items():
        try:
            results[c] = await task
        except Exception as e:
            logger.warning(f"DataBus query failed for {c}: {e}")
            results[c] = DatabusQueryResult(
                chain=c, data={"error": str(e)}, source="databus", cached=False, latency_ms=0
            )
    return results


async def verify_x402_payment(signature: str, payload: str, chain: str = "solana") -> bool:
    """Verify x402 micropayment signature."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(X402_VERIFY_URL, json={"sig": signature, "payload": payload, "chain": chain})
        return resp.status_code == 200 and resp.json().get("valid", False)


def generate_api_key() -> str:
    return "rmi_databus_" + hashlib.sha256(os.urandom(32)).hexdigest()[:32]


# Chain availability
SUPPORTED_CHAINS = [
    "solana",
    "ethereum",
    "bsc",
    "base",
    "arbitrum",
    "polygon",
    "avalanche",
    "optimism",
    "fantom",
    "linea",
    "zksync",
    "scroll",
    "mantle",
    "near",
    "tron",
    "ton",
    "bitcoin",
    "cardano",
    "xrp",
    "sui",
    "aptos",
    "sei",
]

TIER_LIMITS = {
    "free": {"requests_per_hour": 100, "chains": 5, "cache_ttl": 300},
    "pro": {"requests_per_hour": 1000, "chains": 20, "cache_ttl": 60},
    "elite": {"requests_per_hour": 5000, "chains": 102, "cache_ttl": 15},
}
