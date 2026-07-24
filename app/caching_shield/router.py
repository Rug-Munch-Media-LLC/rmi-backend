"""
Aggressive Caching Shield - FastAPI Router
Monitor and control the caching shield via API endpoints.

Mount in main.py:
    from app.caching_shield.router import router
    app.include_router(router)

Endpoints:
  GET  /api/v1/cache/health - All shield components health + stats
  GET  /api/v1/cache/stats  - Detailed cache statistics
  POST /api/v1/cache/clear  - Clear L1 cache (admin)
  GET  /api/v1/cache/rate-limits - Current rate limit bucket states
"""

from __future__ import annotations

import os
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.caching_shield.history_depth import get_history_controller
from app.caching_shield.rate_limiter import get_rate_limiter
from app.caching_shield.rpc_cache import get_rpc_cache
from app.caching_shield.ws_broadcaster import get_ws_manager
from app.core.logging import get_logger

logger = get_logger("caching_shield.router")
router = APIRouter(prefix="/api/v1/cache", tags=["caching-shield"])

def _verify_admin(request: Request) -> bool:
    """Verify admin key for destructive operations."""
    admin_key = os.getenv("ADMIN_API_KEY", "")
    if not admin_key:
        # CRITICAL: Do NOT fall open. Reject all admin requests.
        logger.critical("ADMIN_API_KEY not configured — admin endpoints disabled")
        return False
    provided = request.headers.get("X-Admin-Key", "")
    if provided != admin_key:
        logger.warning("admin_auth_failed: invalid X-Admin-Key")
        return False
    return True

@router.get("/health")
async def cache_health() -> dict[str, str]:
    """Aggregate health check for all caching shield components."""
    cache = get_rpc_cache()
    limiter = get_rate_limiter()
    ws = get_ws_manager()
    hdc = get_history_controller()

    cache_h = await cache.health()
    ws_s = await ws.stats()
    hdc_s = hdc.stats()

    return {
        "status": "ok",
        "timestamp": time.time(),
        "components": {
            "rpc_cache": cache_h,
            "rate_limiter": {"configured_providers": list(limiter.get_all_limits().keys())},
            "ws_broadcaster": ws_s,
            "history_depth": hdc_s,
        },
        "summary": {
            "cache_hit_rate_pct": cache_h.get("hit_rate_pct", 0),
            "redis_available": cache_h.get("redis_available", False),
            "total_ws_clients": ws_s.get("total_clients", 0),
            "broadcasts_sent": ws_s.get("broadcasts_sent", 0),
        },
    }

@router.get("/stats")
async def cache_stats() -> Any:
    """Detailed cache statistics."""
    cache = get_rpc_cache()
    return await cache.health()

@router.post("/clear")
async def cache_clear(request: Request) -> dict[str, str]:
    """Clear L1 in-memory cache. Requires admin key."""
    if not _verify_admin(request):
        raise HTTPException(status_code=401, detail="Invalid admin key")

    cache = get_rpc_cache()
    await cache.clear_l1()
    return {
        "status": "cleared",
        "timestamp": time.time(),
        "message": "L1 in-memory cache cleared. L2 (Redis) cache intact.",
    }

@router.get("/rate-limits")
async def rate_limits_status() -> dict:
    """Get current token bucket states for all providers."""
    limiter = get_rate_limiter()
    limits = limiter.get_all_limits()

    result = {}
    for provider, limit in limits.items():
        # Get current bucket state
        tokens, _last_refill, burst_used = await limiter.get_bucket_state(provider)
        result[provider] = {
            "tokens_per_sec": limit.tokens_per_sec,
            "burst_size": limit.burst_size,
            "current_tokens": round(tokens, 1),
            "available_pct": round(tokens / limit.burst_size * 100, 1) if limit.burst_size else 0,
            "burst_used": burst_used,
        }

    return {
        "timestamp": time.time(),
        "providers": result,
    }

@router.get("/solana-tracker")
async def solana_tracker_stats() -> dict:
    """Get Solana Tracker multi-key load balancing stats."""
    from app.caching_shield.solana_tracker import get_solana_tracker

    st = get_solana_tracker()
    return {
        "timestamp": time.time(),
        **st.stats(),
    }

@router.get("/capacity")
async def capacity_report() -> dict:
    """Full API capacity analysis - all providers, keys, free APIs, and backend sources."""
    from app.caching_shield.api_registry import list_all_sources

    return {
        "timestamp": time.time(),
        **list_all_sources(),
    }
