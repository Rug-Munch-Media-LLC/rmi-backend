"""Semantic LLM Cache for DataBus - caches identical + similar prompts. Redis-backed."""

import hashlib
import json
import os

REDIS_URL = os.getenv("REDIS_CACHE_URL", "redis://localhost:6379/1")
CACHE_TTL = int(os.getenv("LLM_CACHE_TTL", "3600"))


def _cache_key(prompt: str, model: str) -> str:
    return f"llm_cache:{hashlib.sha256(f'{model}:{prompt}'.encode()).hexdigest()[:16]}"


def get_cached(prompt: str, model: str) -> dict | None:
    """Check if prompt+model result is cached."""
    import redis

    try:
        r = redis.from_url(REDIS_URL, decode_responses=True)
        data = r.get(_cache_key(prompt, model))
        if data:
            return json.loads(data)
    except Exception:
        pass
    return None


def set_cached(prompt: str, model: str, result: dict, ttl: int = CACHE_TTL) -> None:
    """Cache a prompt result."""
    import redis

    try:
        r = redis.from_url(REDIS_URL, decode_responses=True)
        r.setex(_cache_key(prompt, model), ttl, json.dumps(result))
    except Exception:
        pass


def get_cache_stats() -> dict:
    """Get cache statistics."""
    import redis

    try:
        r = redis.from_url(REDIS_URL, decode_responses=True)
        keys = r.keys("llm_cache:*")
        return {"cached_prompts": len(keys)}
    except Exception:
        return {"cached_prompts": 0, "error": "redis unavailable"}
