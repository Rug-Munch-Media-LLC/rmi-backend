"""DegenFeed protocol health routes.

Provides per-protocol health status for the DegenFeed infrastructure.
Mounted at /api/v1/degenfeed/status for internal health aggregation.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.core.logging import get_logger
from app.services.degenfeed_cache import health_check as cache_health

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/degenfeed/status", tags=["degenfeed"])

# ── Known DegenFeed protocol services and their health endpoints ──
# In production, these would be discovered via service registry.
# For now, they are statically configured.
PROTOCOL_SERVICES: dict[str, str] = {
    "farcaster": "http://degenfeed-farcaster:8200/health",
    "lens": "http://degenfeed-lens:8201/health",
    "twitter": "http://degenfeed-twitter:8202/health",
    "bluesky": "http://degenfeed-bluesky:8203/health",
    "mastodon": "http://degenfeed-mastodon:8204/health",
    "nostr": "http://degenfeed-nostr:8205/health",
}


async def _probe_service(url: str) -> dict[str, Any]:
    """Probe a single protocol service health endpoint."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            data = resp.json() if resp.status_code == 200 else {}
            return {
                "healthy": resp.status_code == 200,
                "status_code": resp.status_code,
                "response": data,
            }
    except Exception as exc:
        return {"healthy": False, "error": str(exc)[:200]}


@router.get("", summary="DegenFeed protocol health")
async def protocol_health() -> dict[str, Any]:
    """Health status for all DegenFeed protocol services."""
    import asyncio

    cache = await cache_health()

    # Probe all protocol services in parallel
    probes = {name: _probe_service(url) for name, url in PROTOCOL_SERVICES.items()}
    results = await asyncio.gather(*probes.values(), return_exceptions=True)

    protocols: dict[str, Any] = {}
    all_healthy = True
    for (name, _url), result in zip(probes.items(), results):
        if isinstance(result, Exception):
            protocols[name] = {"healthy": False, "error": str(result)[:200]}
            all_healthy = False
        else:
            protocols[name] = result
            if not result.get("healthy", False):
                all_healthy = False

    cache_healthy = cache.get("healthy", False)

    return {
        "service": "degenfeed",
        "healthy": all_healthy and cache_healthy,
        "cache_healthy": cache_healthy,
        "all_protocols_healthy": all_healthy,
        "protocol_count": len(protocols),
        "protocols": protocols,
        "cache": cache,
    }
