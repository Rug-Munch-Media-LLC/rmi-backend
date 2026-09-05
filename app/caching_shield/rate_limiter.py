# DEPRECATED: Use app.core.rate_limiter (UnifiedRateLimiter) instead.
# The new UnifiedRateLimiter supports acquire_provider() for backward compat.
# Remove after migration verified. Consolidates 7 rate limiters into 1.
from __future__ import annotations

"""
Aggressive Caching Shield - Token Bucket Rate Limiter
Redis-backed rate limiting to stay under free tier RPC limits.

Free tier limits (per second):
  Helius:     25 RPS (free), 50 RPS (starter)
  QuickNode:  25 RPS (free)
  Alchemy:    25 RPS (free), 330 RPS (growth)
  dRPC:       15 RPS (free, shared)
  PublicNode: 10 RPS (implicit fair use)

Strategy:
  - Default bucket: 15 tokens/sec, burst of 25 (safe for all free tiers)
  - Per-method buckets for expensive calls (getProgramAccounts: 5/s)
  - Blocks requests when bucket is empty instead of queueing
  - Rate limit headers returned to callers for backpressure
  - Falls back to in-memory if Redis unavailable
"""

import asyncio  # noqa: E402 — module docstring precedes import block
import os  # noqa: E402 — module docstring precedes import block
import time  # noqa: E402 — module docstring precedes import block
from dataclasses import dataclass, field  # noqa: E402 — module docstring precedes import block
from typing import Any  # noqa: E402 — module docstring precedes import block

import redis.asyncio as aioredis  # noqa: E402 — module docstring precedes import block

from app.core.logging import get_logger  # noqa: E402 — module docstring precedes import block

logger = get_logger("rpc_rate_limiter")

# ── Provider Rate Limits ───────────────────────────────────────────────────


@dataclass
class ProviderLimit:
    name: str
    tokens_per_sec: float
    burst_size: int
    # Additional per-method constraints
    method_limits: dict[str, tuple[float, int]] = field(default_factory=dict)


PROVIDER_LIMITS: dict[str, ProviderLimit] = {
    "helius": ProviderLimit(
        "helius",
        20.0,
        25,
        {
            "getProgramAccounts": (5.0, 5),
            "getSignaturesForAddress": (10.0, 15),
        },
    ),
    "quicknode": ProviderLimit("quicknode", 20.0, 25),
    "alchemy": ProviderLimit(
        "alchemy",
        20.0,
        25,
        {
            "getProgramAccounts": (5.0, 5),
        },
    ),
    "drpc": ProviderLimit("drpc", 12.0, 15),
    "publicnode": ProviderLimit("publicnode", 8.0, 10),
    "anvil": ProviderLimit("anvil", 5.0, 8),
    "1rpc": ProviderLimit("1rpc", 15.0, 20),
    "llama_rpc": ProviderLimit("llama_rpc", 15.0, 20),
    "blastapi": ProviderLimit("blastapi", 15.0, 20),
    "_default": ProviderLimit("_default", 10.0, 15),
}

# Bucket prefix for Redis keys
BUCKET_PREFIX = "rmi:ratelimit:"
BURST_PREFIX = "rmi:ratelimit_burst:"

# In-memory fallback
MEM_BUCKET_CLEANUP_INTERVAL = 60  # seconds


class RpcRateLimiter:
    """Token bucket rate limiter using Redis (with in-memory fallback).

    Usage:
        limiter = RpcRateLimiter()
        allowed, wait_time = await limiter.acquire("helius", "getBalance")
        if not allowed:
            raise RateLimitExceeded(f"Try again in {wait_time:.1f}s")
    """

    def __init__(self, redis_url=None, redis_password=None) -> None:
        self._redis: Any | None = None
        self._redis_url = redis_url
        self._redis_password = redis_password
        self._redis_failed = False
        self._init_lock = asyncio.Lock()
        # In-memory fallback: provider -> (tokens, last_refill_ts, burst_used)
        self._mem_buckets: dict[str, tuple[float, float, int]] = {}
        self._mem_lock = asyncio.Lock()

    async def _get_redis(self) -> Any | None:
        if self._redis is not None:
            return self._redis
        if self._redis_failed:
            return None
        async with self._init_lock:
            if self._redis is not None:
                return self._redis
            if self._redis_failed:
                return None
            try:
                host = self._redis_url or os.getenv("REDIS_HOST", "rmi-redis")
                port = int(os.getenv("REDIS_PORT", "6379"))
                password = self._redis_password or os.getenv("REDIS_PASSWORD", "")
                url = f"redis://:{password}@{host}:{port}" if password else f"redis://{host}:{port}"
                self._redis = aioredis.from_url(url, socket_connect_timeout=2, decode_responses=False)
                await self._redis.ping()
                logger.info("RpcRateLimiter: Redis connected OK")
                return self._redis
            except Exception as e:
                logger.warning(f"RpcRateLimiter: Redis unavailable ({e}), using in-memory")
                self._redis_failed = True
                return None

    def _get_limit(self, provider: str, method: str) -> tuple[float, int]:
        """Get the effective rate limit for a provider/method combo."""
        pl = PROVIDER_LIMITS.get(provider, PROVIDER_LIMITS["_default"])
        if method in pl.method_limits:
            return pl.method_limits[method]
        return (pl.tokens_per_sec, pl.burst_size)

    async def acquire(self, provider: str, method: str = "", tokens: int = 1) -> tuple[bool, float]:
        """Try to acquire tokens. Returns (allowed, wait_seconds)."""
        rate, burst = self._get_limit(provider, method)
        redis = await self._get_redis()

        if redis:
            return await self._acquire_redis(redis, provider, method, rate, burst, tokens)
        else:
            return await self._acquire_memory(provider, method, rate, burst, tokens)

    async def _acquire_redis(self, redis, provider, method, rate, burst, tokens) -> tuple[Any, Any] | Any:
        """Redis-based token bucket using Lua script for atomicity."""
        bucket_key = f"{BUCKET_PREFIX}{provider}"
        burst_key = f"{BURST_PREFIX}{provider}"

        # Lua script for atomic token bucket check + consume
        lua = """
        local bucket_key = KEYS[1]
        local burst_key = KEYS[2]
        local rate = tonumber(ARGV[1])
        local burst = tonumber(ARGV[2])
        local tokens = tonumber(ARGV[3])
        local now = tonumber(ARGV[4])

        -- Read current state
        local tokens_val = redis.call('GET', bucket_key)
        local last_refill = redis.call('GET', burst_key)
        local current_tokens = burst

        if tokens_val and last_refill then
            current_tokens = tonumber(tokens_val)
            local elapsed = now - tonumber(last_refill)
            local refill = elapsed * rate
            current_tokens = math.min(burst, current_tokens + refill)
        end

        -- Can we consume?
        if current_tokens >= tokens then
            current_tokens = current_tokens - tokens
            redis.call('SETEX', bucket_key, 60, current_tokens)
            redis.call('SET', burst_key, now)
            return {1, 0}
        else
            -- How long until we have enough?
            local needed = tokens - current_tokens
            local wait = needed / rate
            return {0, math.ceil(wait * 100) / 100}
        end
        """

        try:
            result = await redis.eval(
                lua, 2, bucket_key, burst_key, str(rate), str(burst), str(tokens), str(time.time())
            )
            allowed = bool(result[0])
            wait = float(result[1])
            return (allowed, wait)
        except Exception as e:
            logger.debug(f"Redis rate limiter error: {e}, falling back to memory")
            return await self._acquire_memory(provider, method, rate, burst, tokens)

    async def _acquire_memory(self, provider, method, rate, burst, tokens) -> tuple[bool, float] | tuple[bool, Any]:
        """In-memory fallback token bucket."""
        async with self._mem_lock:
            now = time.monotonic()
            bucket = self._mem_buckets.get(provider)

            if bucket:
                current, last_refill, _used = bucket
                elapsed = now - last_refill
                current = min(burst, current + elapsed * rate)
            else:
                current = burst

            if current >= tokens:
                current -= tokens
                self._mem_buckets[provider] = (current, now, 0)
                return (True, 0.0)
            else:
                needed = tokens - current
                wait = needed / rate
                return (False, wait)

    async def stats(self, provider: str | None = None) -> dict[str, Any]:
        """Return rate limiter stats."""
        result = {}
        providers = [provider] if provider else list(PROVIDER_LIMITS.keys())

        redis = await self._get_redis()
        for p in providers:
            pl = self._get_limit(p, "")
            if redis:
                try:
                    tokens_b = await redis.get(f"{BUCKET_PREFIX}{p}")
                    last_b = await redis.get(f"{BURST_PREFIX}{p}")
                    tokens = float(tokens_b) if tokens_b else pl[1]
                    last = float(last_b or 0)
                except (ConnectionError, OSError):
                    tokens = pl[1]
                    last = 0
            else:
                async with self._mem_lock:
                    bucket = self._mem_buckets.get(p)
                    if bucket:
                        tokens, last, _used = bucket
                    else:
                        tokens = pl[1]
                        last = 0

            result[p] = {
                "available_tokens": round(tokens, 1),
                "burst_limit": pl[1],
                "rate": pl[0],
                "seconds_since_refill": round(time.time() - last, 1) if last else 0,
            }
        return result

    async def get_bucket_state(self, provider: str) -> tuple[float, float, int]:
        """Get current state of a provider's token bucket.
        Returns (tokens, last_refill_ts, burst_used).
        """
        pl = self._get_limit(provider, "")
        redis = await self._get_redis()
        if redis:
            try:
                tokens = await redis.get(f"{BUCKET_PREFIX}{provider}")
                last = await redis.get(f"{BURST_PREFIX}{provider}")
                if tokens is not None and last is not None:
                    return (float(tokens), float(last), 0)
            except (ConnectionError, OSError) as e:
                logger.warning(f"get_bucket_state failed: {e}")
        async with self._mem_lock:
            bucket = self._mem_buckets.get(provider)
            if bucket:
                return bucket
        return (float(pl[1]), 0.0, 0)

    def get_all_limits(self) -> dict[str, Any]:
        """Return all configured provider limits (read-only)."""
        return PROVIDER_LIMITS.copy()


# ── Singleton ──────────────────────────────────────────────────────────────

_limiter: RpcRateLimiter | None = None


def get_rate_limiter() -> RpcRateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RpcRateLimiter()
    return _limiter
