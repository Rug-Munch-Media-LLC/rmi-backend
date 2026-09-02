"""
DataBus provider health checks.

Runs a lightweight ``health_check()`` probe against every registered
provider (across all fallback chains) and caches the result in Redis
with a 5-minute TTL so the ``/api/v1/health/databus`` endpoint stays
cheap and does not hammer external APIs on every request.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.logging import get_logger

logger = get_logger("databus.health")

# Redis cache key + TTL (5 minutes)
_HEALTH_CACHE_KEY = "databus:provider_health"
_HEALTH_CACHE_TTL = 300  # seconds


async def _redis_get(key: str) -> str | None:
    """Read a value from Redis (async). Returns None on any failure."""
    try:
        from app.core.redis import get_redis_async

        client = get_redis_async()
        if client is None:
            return None
        return await client.get(key)
    except Exception as exc:
        logger.debug("redis_get_failed: %s", exc)
        return None


async def _redis_set(key: str, value: str, ttl: int) -> None:
    """Write a value to Redis with TTL (async). Best-effort, never raises."""
    try:
        from app.core.redis import get_redis_async

        client = get_redis_async()
        if client is None:
            return
        await client.set(key, value, ex=ttl)
    except Exception as exc:
        logger.debug("redis_set_failed: %s", exc)


async def _collect_providers() -> list[tuple[str, str, Any]]:
    """Collect (chain_name, provider_name, provider) tuples from all chains.

    Ensures the DataBus singleton is initialized (chains built) first.
    Providers are de-duplicated by name so a provider shared across chains
    (e.g. dexscreener_price) is only probed once.
    """
    from app.databus.core import databus

    if not databus.chains:
        try:
            await databus.initialize()
        except Exception as exc:
            logger.warning("databus_initialize_failed: %s", exc)

    seen: set[str] = set()
    collected: list[tuple[str, str, Any]] = []
    for chain_name, chain in databus.chains.items():
        for provider in chain.providers:
            if provider.name in seen:
                continue
            seen.add(provider.name)
            collected.append((chain_name, provider.name, provider))
    return collected


async def check_all_providers(force: bool = False) -> dict[str, Any]:
    """Run health checks across all providers, cached in Redis (5 min TTL).

    Args:
        force: When True, bypass the Redis cache and re-probe every provider.

    Returns:
        {
            "timestamp": ISO-8601,
            "cached": bool,
            "summary": {"total", "healthy", "unhealthy", "unknown"},
            "providers": {name: {"healthy", "latency_ms", "chain", "tier"}},
        }
    """
    import datetime

    # ── Cache hit (unless forced) ──
    if not force:
        cached = await _redis_get(_HEALTH_CACHE_KEY)
        if cached:
            try:
                import json

                return json.loads(cached)
            except (ValueError, TypeError):
                pass  # corrupt cache — fall through and re-probe

    providers = await _collect_providers()

    # Probe all providers concurrently (each has its own short timeout).
    # Some legacy providers (app.databus.provider_core.Provider) do not expose
    # health_check() — fall back to a generic probe for those.
    async def _probe(provider: Any) -> dict[str, Any]:
        check = getattr(provider, "health_check", None)
        if callable(check):
            return await check()
        # Legacy provider without health_check — report unknown (no probe defined).
        return {"healthy": None, "latency_ms": None}

    results = await asyncio.gather(
        *[_probe(provider) for _chain, _name, provider in providers],
        return_exceptions=True,
    )

    provider_map: dict[str, Any] = {}
    for (chain_name, provider_name, provider), res in zip(providers, results, strict=False):
        if isinstance(res, BaseException):
            provider_map[provider_name] = {
                "healthy": False,
                "latency_ms": None,
                "chain": chain_name,
                "tier": provider.tier.value,
                "error": str(res)[:200],
            }
        else:
            provider_map[provider_name] = {
                "healthy": res.get("healthy"),
                "latency_ms": res.get("latency_ms"),
                "chain": chain_name,
                "tier": provider.tier.value,
            }

    total = len(provider_map)
    healthy = sum(1 for p in provider_map.values() if p["healthy"] is True)
    unhealthy = sum(1 for p in provider_map.values() if p["healthy"] is False)
    unknown = sum(1 for p in provider_map.values() if p["healthy"] is None)

    response = {
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        "cached": False,
        "summary": {"total": total, "healthy": healthy, "unhealthy": unhealthy, "unknown": unknown},
        "providers": provider_map,
    }

    # ── Cache the result (best-effort) ──
    try:
        import json

        await _redis_set(_HEALTH_CACHE_KEY, json.dumps(response), _HEALTH_CACHE_TTL)
    except Exception as exc:
        logger.debug("health_cache_write_failed: %s", exc)

    return response
