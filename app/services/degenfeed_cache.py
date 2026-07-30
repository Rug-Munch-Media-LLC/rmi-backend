"""DegenFeed Redis caching layer.

Provides shared caching for DegenFeed protocol operations:
  - Feed cache: unified feed data cached for 60s
  - Identity cache: identity resolution cached for 5min
  - Post cache: recent cross-protocol posts cached for 30s

Uses ``app.core.redis`` for Redis connectivity with automatic
degradation to in-memory LRU when Redis is unavailable.
"""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── TTL constants ───────────────────────────────────────────────
FEED_CACHE_TTL: int = 60  # 60 seconds
IDENTITY_CACHE_TTL: int = 300  # 5 minutes
POST_CACHE_TTL: int = 30  # 30 seconds

# ── Key prefixes ────────────────────────────────────────────────
_PREFIX_FEED = "degenfeed:feed:"
_PREFIX_IDENTITY = "degenfeed:identity:"
_PREFIX_POST = "degenfeed:post:"
_PREFIX_RATE = "degenfeed:ratelimit:"


def _make_feed_key(protocol: str, user_id: str, page: int = 1) -> str:
    """Generate cache key for a feed."""
    return f"{_PREFIX_FEED}{protocol}:{user_id}:{page}"


def _make_identity_key(protocol: str, handle: str) -> str:
    """Generate cache key for an identity resolution."""
    return f"{_PREFIX_IDENTITY}{protocol}:{handle.lower()}"


def _make_post_key(post_id: str) -> str:
    """Generate cache key for a post."""
    return f"{_PREFIX_POST}{post_id}"


# ── In-memory LRU fallback (used when Redis is down) ────────────


class LRUCache:
    """Simple thread-safe in-memory LRU cache with TTL."""

    def __init__(self, maxsize: int = 5000) -> None:
        self._cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._maxsize = maxsize

    def get(self, key: str) -> Any | None:
        if key not in self._cache:
            return None
        expires_at, value = self._cache[key]
        if time.monotonic() > expires_at:
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        return value

    def set(self, key: str, value: Any, ttl: int) -> None:
        expires_at = time.monotonic() + ttl
        self._cache[key] = (expires_at, value)
        self._cache.move_to_end(key)
        if len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)

    def invalidate(self, key: str) -> None:
        self._cache.pop(key, None)

    def clear(self) -> None:
        self._cache.clear()


# ── Singleton state ─────────────────────────────────────────────

_mem_cache = LRUCache()
_redis_available: bool | None = None


def _get_redis() -> Any | None:
    """Get async Redis client from core module."""
    global _redis_available
    try:
        from app.core.redis import get_redis_async

        client = get_redis_async()
        _redis_available = client is not None
        return client
    except Exception:
        _redis_available = False
        return None


# ── Public API ──────────────────────────────────────────────────


async def get_cached_feed(protocol: str, user_id: str, page: int = 1) -> Any | None:
    """Get cached feed data. Returns None on cache miss."""
    key = _make_feed_key(protocol, user_id, page)

    # Try Redis first
    redis = _get_redis()
    if redis:
        try:
            raw = await redis.get(key)
            if raw:
                return json.loads(raw)
        except Exception as exc:
            logger.debug("Redis feed cache miss (fallback to memory): %s", exc)

    # Memory fallback
    return _mem_cache.get(key)


async def set_cached_feed(protocol: str, user_id: str, data: Any, page: int = 1) -> None:
    """Cache feed data with FEED_CACHE_TTL."""
    key = _make_feed_key(protocol, user_id, page)
    raw = json.dumps(data, default=str)

    redis = _get_redis()
    if redis:
        try:
            await redis.setex(key, FEED_CACHE_TTL, raw)
            return
        except Exception as exc:
            logger.debug("Redis feed cache set failed (fallback to memory): %s", exc)

    _mem_cache.set(key, data, ttl=FEED_CACHE_TTL)


async def get_cached_identity(protocol: str, handle: str) -> Any | None:
    """Get cached identity resolution. Returns None on cache miss."""
    key = _make_identity_key(protocol, handle)

    redis = _get_redis()
    if redis:
        try:
            raw = await redis.get(key)
            if raw:
                return json.loads(raw)
        except Exception as exc:
            logger.debug("Redis identity cache miss (fallback to memory): %s", exc)

    return _mem_cache.get(key)


async def set_cached_identity(protocol: str, handle: str, data: Any) -> None:
    """Cache identity data with IDENTITY_CACHE_TTL."""
    key = _make_identity_key(protocol, handle)
    raw = json.dumps(data, default=str)

    redis = _get_redis()
    if redis:
        try:
            await redis.setex(key, IDENTITY_CACHE_TTL, raw)
            return
        except Exception as exc:
            logger.debug("Redis identity cache set failed (fallback to memory): %s", exc)

    _mem_cache.set(key, data, ttl=IDENTITY_CACHE_TTL)


async def get_cached_post(post_id: str) -> Any | None:
    """Get cached post data. Returns None on cache miss."""
    key = _make_post_key(post_id)

    redis = _get_redis()
    if redis:
        try:
            raw = await redis.get(key)
            if raw:
                return json.loads(raw)
        except Exception as exc:
            logger.debug("Redis post cache miss (fallback to memory): %s", exc)

    return _mem_cache.get(key)


async def set_cached_post(post_id: str, data: Any) -> None:
    """Cache post data with POST_CACHE_TTL."""
    key = _make_post_key(post_id)
    raw = json.dumps(data, default=str)

    redis = _get_redis()
    if redis:
        try:
            await redis.setex(key, POST_CACHE_TTL, raw)
            return
        except Exception as exc:
            logger.debug("Redis post cache set failed (fallback to memory): %s", exc)

    _mem_cache.set(key, data, ttl=POST_CACHE_TTL)


async def invalidate_feed(protocol: str, user_id: str) -> None:
    """Invalidate all cached pages for a user's feed."""
    redis = _get_redis()
    if redis:
        try:
            pattern = f"{_PREFIX_FEED}{protocol}:{user_id}:*"
            async for key in redis.scan_iter(match=pattern):
                await redis.delete(key)
            return
        except Exception as exc:
            logger.debug("Redis feed invalidation failed: %s", exc)

    # Memory: clear all matching keys
    keys_to_remove = [k for k in _mem_cache._cache if k.startswith(f"{_PREFIX_FEED}{protocol}:{user_id}:")]
    for k in keys_to_remove:
        _mem_cache.invalidate(k)


async def degenfeed_cache_stats() -> dict[str, Any]:
    """Get DegenFeed cache statistics for monitoring."""
    redis = _get_redis()
    stats = {
        "redis_available": _redis_available,
        "memory_cache_size": len(_mem_cache._cache),
    }

    if redis:
        try:
            feed_count = 0
            identity_count = 0
            post_count = 0
            async for _key in redis.scan_iter(match=f"{_PREFIX_FEED}*"):
                feed_count += 1
            async for _key in redis.scan_iter(match=f"{_PREFIX_IDENTITY}*"):
                identity_count += 1
            async for _key in redis.scan_iter(match=f"{_PREFIX_POST}*"):
                post_count += 1
            stats.update(
                {
                    "redis_feed_keys": feed_count,
                    "redis_identity_keys": identity_count,
                    "redis_post_keys": post_count,
                    "total_redis_keys": feed_count + identity_count + post_count,
                }
            )
        except Exception as exc:
            stats["redis_error"] = str(exc)

    return stats


async def health_check() -> dict[str, Any]:
    """Health check for DegenFeed cache layer."""
    redis = _get_redis()
    healthy = False
    if redis:
        try:
            await redis.ping()
            healthy = True
        except Exception:
            pass

    return {
        "healthy": healthy or _mem_cache is not None,
        "redis_up": healthy,
        "memory_cache_available": True,
        "feed_ttl_seconds": FEED_CACHE_TTL,
        "identity_ttl_seconds": IDENTITY_CACHE_TTL,
        "post_ttl_seconds": POST_CACHE_TTL,
    }
