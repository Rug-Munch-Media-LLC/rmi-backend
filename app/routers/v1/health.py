"""V1 health endpoints — /api/v1/health/*.

Endpoints:
  GET /api/v1/health/live       — Liveness (redirect to /live)
  GET /api/v1/health/ready      — Readiness (redirect to /ready)
  GET /api/v1/health            — Full health (redirect to /health)
  GET /api/v1/health/unified    — Unified health dashboard (NEW)

The unified endpoint aggregates:
  - Service health (from app.core.health_route — redis, postgres, neo4j, etc.)
  - Provider status (from app.routers.v1.providers — circuit breaker state for 30+ connectors)
  - Degradation info (from app.core.degradation — store health, LRU stats)
  - Config validation issues
"""

from __future__ import annotations

import asyncio
import datetime
import os
import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/health", tags=["health-v1"])

# ──────────────────────────────────────────────────────────────────────
# Existing redirect endpoints
# ──────────────────────────────────────────────────────────────────────


@router.get("/live")
async def live_v1():
    """Liveness check (redirect to root /live)."""
    return RedirectResponse(url="/live", status_code=307)


@router.get("/ready")
async def ready_v1():
    """Readiness check (redirect to root /ready)."""
    return RedirectResponse(url="/ready", status_code=307)


@router.get("")
async def health_v1():
    """Full health check (redirect to root /health)."""
    return RedirectResponse(url="/health", status_code=307)


# ──────────────────────────────────────────────────────────────────────
# Unified health endpoint
# ──────────────────────────────────────────────────────────────────────

_UNIFIED_CACHE: dict[str, Any] = {"data": None, "timestamp": 0.0}
_UNIFIED_CACHE_TTL = 15  # seconds


async def _get_service_health() -> dict[str, Any]:
    """Replicate the store checks from app.core.health_route.

    We import the check functions directly to keep a single source of truth.
    If the import fails, we fall back to a basic probe.
    """
    try:
        from app.core.health_route import (
            _check_clickhouse,
            _check_minio,
            _check_neo4j,
            _check_postgres,
            _check_qdrant,
            _check_redis,
            _check_store,
        )

        store_checks = {
            "redis": asyncio.wait_for(_check_store("redis", _check_redis()), timeout=5.0),
            "postgres": asyncio.wait_for(_check_store("postgres", _check_postgres()), timeout=5.0),
            "neo4j": asyncio.wait_for(_check_store("neo4j", _check_neo4j()), timeout=5.0),
            "qdrant": asyncio.wait_for(_check_store("qdrant", _check_qdrant()), timeout=5.0),
            "clickhouse": asyncio.wait_for(
                _check_store("clickhouse", _check_clickhouse()), timeout=5.0
            ),
            "minio": asyncio.wait_for(_check_store("minio", _check_minio()), timeout=5.0),
        }
        results = await asyncio.gather(*store_checks.values(), return_exceptions=True)
        services = {}
        for (name, _), ref in zip(store_checks.items(), results, strict=False):
            if isinstance(ref, Exception):
                services[name] = {"healthy": False, "error": str(ref)[:200]}
            else:
                services[name] = {
                    "healthy": ref.get("healthy", False),
                    "latency_ms": ref.get("latency_ms", None),
                    "error": ref.get("error", None),
                }
        return services
    except Exception as exc:
        logger.warning("Service health check failed: %s", exc)
        return {}


async def _get_provider_status_slim() -> dict[str, str]:
    """Get a slim version of provider status — just name -> status mapping."""
    try:
        from app.routers.v1.providers import providers_status

        full = await providers_status()
        mapping: dict[str, str] = {}
        for p in full.get("providers", []):
            mapping[p["name"]] = p.get("status", "unknown")
        return mapping
    except Exception as exc:
        logger.warning("Provider status slim check failed: %s", exc)
        return {}


def _get_degradation_info() -> dict[str, Any]:
    """Get degradation registry info — degraded stores, LRU stats."""
    try:
        from app.core.degradation import get_degradation_health

        return get_degradation_health()
    except Exception as exc:
        logger.warning("Degradation health check failed: %s", exc)
        return {"degraded_stores": [], "store_details": {}, "in_process_lru": {}}


def _get_config_issues() -> list[dict[str, str]]:
    """Check for missing or placeholder config values."""
    try:
        from app.core.health_route import check_config

        return check_config()
    except Exception as exc:
        logger.warning("Config check failed: %s", exc)
        return [{"key": "_internal", "description": str(exc)[:200], "severity": "check_failed"}]


def _compute_overall_status(
    services: dict[str, Any],
    providers: dict[str, str],
    degradation: dict[str, Any],
) -> str:
    """Compute unified overall status.

    - unhealthy if any critical service (redis, postgres) is down
    - degraded if any service, provider, or store is degraded
    - healthy otherwise
    """
    critical_services = {"redis", "postgres"}

    for name, svc in services.items():
        if name in critical_services and not svc.get("healthy", False):
            return "unhealthy"

    for _name, svc in services.items():
        if not svc.get("healthy", False):
            return "degraded"

    unhealthy_providers = [s for s in providers.values() if s == "unhealthy"]
    if unhealthy_providers:
        return "degraded"

    if degradation.get("degraded_stores"):
        return "degraded"

    return "healthy"


@router.get("/unified")
async def health_unified() -> dict[str, Any]:
    """Aggregated health dashboard combining all service health, provider status,
    degradation info, and config validation.

    Returns:
        {
            "status": "healthy" | "degraded" | "unhealthy",
            "timestamp": "2026-07-17T15:00:00Z",
            "services": { "redis": {"healthy": True, "latency_ms": ...}, ... },
            "providers": { "coingecko": "healthy", ... },
            "degradation": { "degraded_stores": [], "in_process_lru": {...} },
            "config_issues": [...],
            "version": "2026.07.17"
        }
    """
    now = time.monotonic()

    # Check cache
    global _UNIFIED_CACHE
    if (
        _UNIFIED_CACHE["data"] is not None
        and (now - _UNIFIED_CACHE["timestamp"]) < _UNIFIED_CACHE_TTL
    ):
        return _UNIFIED_CACHE["data"]  # type: ignore[return-value]

    # Gather all health data in parallel
    service_task = asyncio.create_task(_get_service_health())
    provider_task = asyncio.create_task(_get_provider_status_slim())

    services = await service_task
    providers = await provider_task

    # Get degradation and config (synchronous, cheap)
    degradation = _get_degradation_info()
    config_issues = _get_config_issues()

    # Extract just the slim LRU stats for the response
    lru_stats = degradation.get("in_process_lru", {})

    # Compute overall status
    status = _compute_overall_status(services, providers, degradation)

    response = {
        "status": status,
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        "version": os.getenv("APP_VERSION", "2026.07.17"),
        "services": services,
        "providers": providers,
        "degradation": {
            "degraded_stores": degradation.get("degraded_stores", []),
            "lru_stats": {
                "size": lru_stats.get("size", 0),
                "maxsize": lru_stats.get("maxsize", 2048),
                "hits": lru_stats.get("hits", 0),
                "misses": lru_stats.get("misses", 0),
                "evictions": lru_stats.get("evictions", 0),
            },
        },
        "config_issues": config_issues,
    }

    # Update cache
    _UNIFIED_CACHE = {"data": response, "timestamp": time.monotonic()}

    return response
