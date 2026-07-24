"""
Layer 1: Response Caching — Redis-backed cache for tool results.

Provides:
  - get_cached / set_cached for transparent read/write
  - invalidate_cache for targeted or full invalidation
  - Internal _redis_conn and _cache_key helpers
"""

from __future__ import annotations
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger

logger = get_logger("x402.cache")

CACHE_TTL = 60  # seconds
CACHEABLE_TOOLS = {
    "audit",
    "wallet",
    "reputation_score",
    "honeypot_check",
    "rugshield",
    "forensics",
    "whale",
    "token_deep_dive",
    "market_overview",
    "chain_health",
    "sentiment",
    "rug_pull_predictor",
    "rug_probability",
    "narrative",
}

def _redis_conn() -> Any:
    import redis as _redis

    return _redis.Redis(
        host=os.getenv("REDIS_HOST", "rmi-redis"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        password=os.getenv("REDIS_PASSWORD", ""),
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )

def _cache_key(tool: str, params: dict) -> str:
    raw = f"{tool}:{json.dumps(params, sort_keys=True)}"
    return f"x402:cache:{hashlib.sha256(raw.encode()).hexdigest()[:16]}"

def get_cached(tool: str, params: dict) -> dict | None:
    """Check Redis cache for a tool result."""
    try:
        r = _redis_conn()
        key = _cache_key(tool, params)
        data = r.get(key)
        if data:
            return json.loads(data)
    except (ConnectionError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        pass
    return None

def set_cached(tool: str, params: dict, result: dict, ttl: int = CACHE_TTL) -> None:
    """Store tool result in Redis cache."""
    try:
        r = _redis_conn()
        key = _cache_key(tool, params)
        result["_cached"] = True
        result["_cached_at"] = datetime.now(UTC).isoformat()
        r.setex(key, ttl, json.dumps(result))
    except (ConnectionError, TimeoutError, OSError):
        pass

def invalidate_cache(tool: str | None = None) -> bool:
    """Clear cache for a tool or all cached results."""
    try:
        r = _redis_conn()
        for key in r.scan_iter("x402:cache:*"):
            r.delete(key)
        return True
    except (ConnectionError, TimeoutError, OSError) as e:
        logger.warning(f"Cache clear failed (Redis): {e}")
        return False

__all__ = [
    "CACHE_TTL",
    "CACHEABLE_TOOLS",
    "get_cached",
    "set_cached",
    "invalidate_cache",
    "_redis_conn",
    "_cache_key",
]
