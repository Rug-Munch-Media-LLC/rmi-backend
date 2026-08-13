"""Semantic LLM Cache for DataBus - caches identical + similar prompts. Redis-backed."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, cast

_redis_cache_url = None
_cache_ttl = None
try:
    from app.config import settings as _s

    _redis_cache_url = _s.redis_cache_url
    _cache_ttl = _s.llm_cache_ttl
except Exception:  # noqa: S110 — best-effort cache
    pass
REDIS_URL: str = cast("str", _redis_cache_url) or os.getenv("REDIS_CACHE_URL", "redis://localhost:6379/1")
CACHE_TTL: int = cast("int", _cache_ttl) or int(os.getenv("LLM_CACHE_TTL", "3600"))


def _cache_key(prompt: str, model: str) -> str:
    return f"llm_cache:{hashlib.sha256(f'{model}:{prompt}'.encode()).hexdigest()[:16]}"


def get_cached(prompt: str, model: str) -> dict[str, Any] | None:
    """Check if prompt+model result is cached."""
    import redis

    try:
        r = redis.from_url(REDIS_URL, decode_responses=True)
        data = r.get(_cache_key(prompt, model))
        if data:
            return json.loads(data)
    except (ConnectionError, OSError, json.JSONDecodeError):
        pass
    return None


def set_cached(prompt: str, model: str, result: dict[str, Any], ttl: int = CACHE_TTL) -> None:
    """Cache a prompt result."""
    import redis

    try:
        r = redis.from_url(REDIS_URL, decode_responses=True)
        r.setex(_cache_key(prompt, model), ttl, json.dumps(result))
    except (ConnectionError, OSError, json.JSONDecodeError):
        pass


def get_cache_stats() -> dict[str, Any]:
    """Get cache statistics."""
    import redis

    try:
        r = redis.from_url(REDIS_URL, decode_responses=True)
        keys = r.keys("llm_cache:*")
        return {"cached_prompts": len(keys)}
    except (ConnectionError, OSError, json.JSONDecodeError):
        return {"cached_prompts": 0, "error": "redis unavailable"}
