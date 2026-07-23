"""
Multi-Layer Cache Manager - L1 In-Memory + L2 Redis with Smart TTLs.

Provides a two-level caching strategy:
  L1: In-memory dict (fast, per-process, bounded size)
  L2: Redis (shared across processes/workers, persistent optional)

If REDIS_URL env var is not set, falls back to L1-only mode.
Key format: {data_type}:{chain}:{address} for easy pattern invalidation.

Smart TTLs per data type (seconds):
  token_meta=86400 (24h), deployer=0 (permanent), holders=300 (5min),
  price=10, liquidity=30, social=900 (15min), rpc=60, block=15.

Depends on: redis (>=5.0) for L2; falls back gracefully if not installed.
"""

import asyncio
import hashlib
import os
import re
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Smart TTLs per Data Type ───────────────────────────────────────────────

# fmt: off
SMART_TTLS: dict[str, int] = {
    "token_meta":    86400,   # 24 hours - token name/symbol/logo rarely change
    "deployer":      0,       # Permanent (0 = never expire in L1, Redis TTL omitted)
    "contract_code": 3600,    # 1 hour - contract code is immutable but may redeploy
    "holders":        300,    # 5 minutes - holder count changes frequently
    "holder_list":    120,    # 2 minutes - individual holder data can shift
    "price":          10,    # 10 seconds - price is highly volatile
    "liquidity":      30,    # 30 seconds - liquidity changes but slower than price
    "volume":         30,    # 30 seconds - same reasoning
    "market_cap":     60,    # 1 minute - derived from price x supply
    "social":         900,   # 15 minutes - social media data updates slowly
    "metadata":      3600,    # 1 hour - token metadata is semi-static
    "rpc":             60,    # 1 minute - generic RPC responses
    "rpc_account":     30,    # 30 seconds - account state (balance, etc.)
    "rpc_block":       15,    # 15 seconds - block data is fresh
    "rpc_tx":          60,    # 1 minute - confirmed transactions
    "scanner":        300,    # 5 minutes - scanner results (risk scores, etc.)
    "entity":        3600,    # 1 hour - entity labeling by address
    "news":           600,    # 10 minutes - news articles
    "trending":       300,    # 5 minutes - trending tokens
    "default":        120,    # 2 minutes - catch-all
}
# fmt: on


# ── Data Classes ────────────────────────────────────────────────────────────


@dataclass
class CacheEntry:
    """A cached value with metadata."""

    value: Any
    created_at: float = field(default_factory=time.monotonic)
    ttl: float = 0.0  # 0 = permanent (no expiry)
    access_count: int = 0
    size_bytes: int = 0

    @property
    def is_expired(self) -> bool:
        if self.ttl <= 0:
            return False
        return time.monotonic() - self.created_at > self.ttl

    @property
    def age_seconds(self) -> float:
        return time.monotonic() - self.created_at


@dataclass
class CacheStats:
    """Aggregate cache performance stats."""

    hit_count: int = 0
    miss_count: int = 0
    total_calls: int = 0
    l1_size: int = 0
    l2_size: int = 0
    l1_hits: int = 0
    l2_hits: int = 0
    invalidations: int = 0

    @property
    def hit_rate(self) -> float:
        if self.total_calls == 0:
            return 0.0
        return self.hit_count / self.total_calls

    def to_dict(self) -> dict[str, Any]:
        return {
            "hit_count": self.hit_count,
            "miss_count": self.miss_count,
            "total_calls": self.total_calls,
            "hit_rate": round(self.hit_rate * 100, 1),
            "l1_size": self.l1_size,
            "l2_size": self.l2_size,
            "l1_hits": self.l1_hits,
            "l2_hits": self.l2_hits,
            "invalidations": self.invalidations,
        }


# ── Cache Key Builder ──────────────────────────────────────────────────────


def build_key(data_type: str, chain: str, address: str) -> str:
    """Build a standardized cache key.

    Format: {data_type}:{chain}:{address}
    All segments are lowercased. Addresses longer than 80 chars are truncated
    and hashed for readability.

    Examples:
        build_key("price", "solana", "EPjFWdd5AufqSSqeM2qN1xzybapC8G5wUGxvZq2moaomYR")
        → "price:solana:epjfwd..."
    """
    address = str(address).strip().lower()
    chain = str(chain).strip().lower()
    data_type = str(data_type).strip().lower()

    if len(address) > 80:
        # Hash long addresses (e.g., complex contract addresses)
        short = hashlib.sha256(address.encode()).hexdigest()[:16]
        address = f"{address[:40]}..{short}"

    return f"{data_type}:{chain}:{address}"


def get_ttl(data_type: str, override: int | None = None) -> int:
    """Get TTL for a data type, with optional override."""
    if override is not None:
        return override
    return SMART_TTLS.get(data_type.lower(), SMART_TTLS["default"])


# ── Cache Implementation ────────────────────────────────────────────────────


class RpcCache:
    """Two-layer cache: L1 in-memory (OrderedDict, LRU eviction) + L2 Redis.

    Usage:
        cache = RpcCache()
        result = await cache.get_or_fetch(
            "price", "solana", token_address,
            factory_fn=lambda: fetch_price(token_address),
        )
    """

    # Max entries in L1 memory cache (prevents unbounded growth)
    MAX_L1_SIZE = 10000

    # Redis key prefix for namespacing
    REDIS_PREFIX = "rmi:cache:"

    def __init__(
        self,
        l1_max_size: int = 10000,
        redis_url: str | None = None,
    ) -> None:
        """Initialize the cache.

        Args:
            l1_max_size: Maximum entries in L1 (memory) cache
            redis_url: Redis connection URL. If None, reads REDIS_URL env var.
                       Set to empty string or False to force L1-only mode.
        """
        self._l1: OrderedDict[str, CacheEntry] = OrderedDict()
        self._l1_max = l1_max_size
        self._lock = asyncio.Lock()
        self._stats = CacheStats()

        # Redis L2 setup
        self._redis = None
        self._redis_available = False

        _url = redis_url if redis_url is not None else os.getenv("REDIS_URL", "")
        if _url and _url.strip():
            try:
                import redis.asyncio as redis_asyncio

                self._redis = redis_asyncio.from_url(
                    _url.strip(),
                    decode_responses=False,  # We handle serialization ourselves
                    socket_connect_timeout=3.0,
                    socket_timeout=2.0,
                    retry_on_timeout=True,
                    health_check_interval=30,
                )
                self._redis_available = True
                logger.info(f"RpcCache: L2 Redis connected (prefix={self.REDIS_PREFIX})")
            except ImportError:
                logger.warning("RpcCache: redis package not installed, L2 disabled")
            except Exception as e:
                logger.warning(f"RpcCache: Redis connection failed ({e}), L2 disabled")
        else:
            logger.info("RpcCache: No REDIS_URL set, L1-only mode")

    # ── Public API ──────────────────────────────────────────────────────

    async def get(self, data_type: str, chain: str, address: str) -> Any | None:
        """Retrieve a cached value. Returns None on miss or expiry.

        Checks L1 first, then L2 (Redis), then returns None.
        On L2 hit, promotes the value into L1.
        """
        key = build_key(data_type, chain, address)
        ttl = get_ttl(data_type)

        # 1. Check L1 (in-memory)
        async with self._lock:
            entry = self._l1.get(key)
            if entry is not None:
                if entry.is_expired:
                    del self._l1[key]
                    self._stats.l1_size = len(self._l1)
                else:
                    entry.access_count += 1
                    self._l1.move_to_end(key)
                    self._stats.hit_count += 1
                    self._stats.total_calls += 1
                    self._stats.l1_hits += 1
                    self._stats.l1_size = len(self._l1)
                    return entry.value

        # 2. Check L2 (Redis)
        if self._redis_available and self._redis:
            try:
                redis_key = f"{self.REDIS_PREFIX}{key}"
                raw = await self._redis.get(redis_key)
                if raw is not None:
                    value = self._deserialize(raw)
                    # Promote to L1
                    async with self._lock:
                        self._stats.hit_count += 1
                        self._stats.total_calls += 1
                        self._stats.l2_hits += 1
                        self._l1[key] = CacheEntry(value=value, ttl=float(ttl))
                        self._l1.move_to_end(key)
                        self._stats.l1_size = len(self._l1)
                        self._evict_l1_if_needed()
                    return value
            except Exception as e:
                logger.debug(f"Redis get error for {key}: {e}")

        # 3. Miss
        async with self._lock:
            self._stats.miss_count += 1
            self._stats.total_calls += 1
        return None

    async def set(
        self,
        data_type: str,
        chain: str,
        address: str,
        value: Any,
        ttl_override: int | None = None,
    ) -> None:
        """Store a value in both L1 and L2 caches.

        Args:
            data_type: Type of data (see SMART_TTLS keys)
            chain: Blockchain identifier
            address: Address or identifier
            value: Any JSON-serializable value
            ttl_override: Override the default TTL for this data type
        """
        key = build_key(data_type, chain, address)
        ttl = get_ttl(data_type, ttl_override)

        # Serialize for size estimation
        raw = self._serialize(value)
        size = len(raw)

        entry = CacheEntry(value=value, ttl=float(ttl), size_bytes=size)

        # Store in L1
        async with self._lock:
            self._l1[key] = entry
            self._l1.move_to_end(key)
            self._stats.l1_size = len(self._l1)
            self._evict_l1_if_needed()

        # Store in L2
        if self._redis_available and self._redis:
            try:
                redis_key = f"{self.REDIS_PREFIX}{key}"
                if ttl > 0:
                    await self._redis.setex(redis_key, ttl, raw)
                else:
                    # Permanent - no expiry
                    await self._redis.set(redis_key, raw)
            except Exception as e:
                logger.debug(f"Redis set error for {key}: {e}")

    async def get_or_fetch(
        self,
        data_type: str,
        chain: str,
        address: str,
        factory_fn: Callable[[], Awaitable[Any]],
        ttl_override: int | None = None,
    ) -> Any:
        """Get cached value or fetch fresh via factory function.

        This is the primary convenience method - it combines get() + set()
        in a single call. If the value is cached and not expired, returns it
        immediately. Otherwise, calls factory_fn(), caches the result, and
        returns it.

        Args:
            data_type: Type of data (e.g., 'price', 'token_meta')
            chain: Blockchain identifier
            address: Address/identifier
            factory_fn: Async callable that returns the value
            ttl_override: Override TTL for this entry

        Returns:
            The cached or freshly fetched value, or None if fetch failed.
        """
        # Try cache first
        cached = await self.get(data_type, chain, address)
        if cached is not None:
            return cached

        # Fetch fresh
        try:
            value = await factory_fn()
            if value is not None:
                await self.set(data_type, chain, address, value, ttl_override)
            return value
        except Exception as e:
            logger.warning(f"Factory fetch failed for {data_type}:{chain}:{address}: {e}")
            return None

    async def invalidate(self, key_pattern: str) -> int:
        """Remove all cached entries matching a key pattern.

        For L1, uses simple string matching (fnmatch-style with *):
          - "price:*:*" - invalidates all price entries
          - "*:solana:*" - invalidates all Solana entries
          - "price:solana:*" - invalidates a specific token's price

        For L2, uses Redis SCAN + pattern matching.

        Returns number of entries invalidated (L1 + L2 count).
        """
        # Convert glob-like pattern to regex for L1
        regex_pattern = re.escape(key_pattern).replace(r"\*", ".*")
        regex = re.compile(f"^{regex_pattern}$")

        count = 0

        # L1 invalidation
        async with self._lock:
            keys_to_remove = [k for k in self._l1 if regex.match(k)]
            for k in keys_to_remove:
                del self._l1[k]
                count += 1
            self._stats.l1_size = len(self._l1)

        # L2 invalidation
        if self._redis_available and self._redis:
            try:
                redis_pattern = f"{self.REDIS_PREFIX}{key_pattern}"
                cursor = 0
                l2_count = 0
                while True:
                    cursor, keys = await self._redis.scan(
                        cursor=cursor,
                        match=redis_pattern,
                        count=100,
                    )
                    if keys:
                        await self._redis.delete(*keys)
                        l2_count += len(keys)
                    if cursor == 0:
                        break
                count += l2_count
                logger.debug(f"Invalidated {l2_count} L2 entries matching '{key_pattern}'")
            except Exception as e:
                logger.warning(f"Redis invalidation error for pattern '{key_pattern}': {e}")

        async with self._lock:
            self._stats.invalidations += count
        logger.info(f"Cache invalidation: {count} entries removed (pattern='{key_pattern}')")
        return count

    async def invalidate_address(
        self,
        data_type: str,
        chain: str,
        address: str,
    ) -> bool:
        """Invalidate a specific cache key."""
        key = build_key(data_type, chain, address)

        found = False

        async with self._lock:
            if key in self._l1:
                del self._l1[key]
                self._stats.l1_size = len(self._l1)
                self._stats.invalidations += 1
                found = True

        if self._redis_available and self._redis:
            try:
                redis_key = f"{self.REDIS_PREFIX}{key}"
                deleted = await self._redis.delete(redis_key)
                if deleted:
                    found = True
            except Exception as e:
                logger.debug(f"Redis delete error for {key}: {e}")

        return found

    async def clear(self) -> int:
        """Clear all entries from both L1 and L2 caches.

        Returns total number of entries cleared.
        """
        count = 0

        async with self._lock:
            count = len(self._l1)
            self._l1.clear()
            self._stats.l1_size = 0

        if self._redis_available and self._redis:
            try:
                keys = await self._redis.keys(f"{self.REDIS_PREFIX}*")
                if keys:
                    await self._redis.delete(*keys)
                count += len(keys)
            except Exception as e:
                logger.warning(f"Redis clear error: {e}")

        logger.info(f"Cache cleared: {count} entries removed")
        return count

    # ── Stats ────────────────────────────────────────────────────────────

    async def stats(self) -> dict[str, Any]:
        """Return cache performance statistics."""
        async with self._lock:
            result = self._stats.to_dict()
        # Add Redis L2 info
        if self._redis_available and self._redis:
            try:
                key_count = await self._redis.dbsize()
                result["l2_size"] = key_count
            except Exception:
                result["l2_size"] = -1
        result["redis_available"] = self._redis_available
        result["l1_max"] = self._l1_max
        return result

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def close(self) -> None:
        """Close Redis connection if open."""
        if self._redis:
            try:
                await self._redis.aclose()
                self._redis = None
                self._redis_available = False
                logger.info("RpcCache: Redis connection closed")
            except Exception as e:
                logger.debug(f"Redis close error: {e}")

    # ── Internal Helpers ──────────────────────────────────────────────────

    def _serialize(self, value: Any) -> bytes:
        """Serialize a value to bytes for Redis storage. Uses JSON."""
        import json

        try:
            # Use a custom encoder for special types
            return json.dumps(value, default=str, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError) as e:
            logger.warning(f"Serialization fallback for {type(value)}: {e}")
            return json.dumps({"__serialized__": str(value)}).encode("utf-8")

    def _deserialize(self, raw: bytes) -> Any:
        """Deserialize bytes from Redis back to Python object."""
        import json

        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning(f"Deserialization error: {e}")
            return None

    def _evict_l1_if_needed(self) -> None:
        """Evict oldest entries from L1 if over capacity (LRU eviction)."""
        # Called within self._lock
        while len(self._l1) > self._l1_max:
            self._l1.popitem(last=False)  # Remove oldest (first inserted)

    # ── Health Check ──────────────────────────────────────────────────────

    async def health(self) -> dict[str, Any]:
        """Quick health check."""
        status = {
            "l1_ok": True,
            "l1_entries": len(self._l1),
            "l2_ok": False,
        }
        if self._redis_available and self._redis:
            try:
                await self._redis.ping()
                status["l2_ok"] = True
            except Exception:
                status["l2_ok"] = False
        return status


# ── Singleton ──────────────────────────────────────────────────────────────

_cache_instance: RpcCache | None = None


def get_rpc_cache() -> RpcCache:
    """Get the global RpcCache singleton."""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = RpcCache()
    return _cache_instance
