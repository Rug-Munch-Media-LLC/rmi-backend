"""
Aggressive Caching Shield - RPC Cache Layer
Redis-backed cache wrapping ConsensusRpcClient with TTL tiers.

Every RPC result is cached before returning. Cache keys are deterministic
from (method, params, chain). TTLs vary by data freshness requirements.
Features: stampede protection, TTL jitter, zlib compression, L1+L2 tiers,
graceful Redis fallback, selective cache bypass.

Free tier rate-aware: Default TTLs keep RPC calls at ~0.5-2 req/s
per data point, well under Helius/QuickNode free limits.
"""

import asyncio
import hashlib
import json
import logging
import os
import random
import time
import zlib
from dataclasses import dataclass

import redis.asyncio as aioredis

from app.consensus_rpc import ConsensusResult, get_consensus_rpc

logger = logging.getLogger("rpc_cache")

# ── TTL Configuration ──────────────────────────────────────────────────────

TTL_TABLE: dict[str, int] = {
    "getBalance": 10,
    "getTokenAccountBalance": 10,
    "getMultipleAccounts": 10,
    "getAccountInfo": 30,
    "getSignaturesForAddress": 30,
    "getTransaction": 30,
    "getTokenAccountsByOwner": 30,
    "getProgramAccounts": 30,
    "getTokenSupply": 60,
    "getTokenLargestAccounts": 60,
    "getBlock": 60,
    "getBlockTime": 60,
    "getSlot": 120,
    "getEpochInfo": 120,
    "getGenesisHash": 3600,
    "getVersion": 3600,
    "eth_getBalance": 10,
    "eth_getTransactionCount": 10,
    "eth_call": 15,
    "eth_getCode": 300,
    "eth_getStorageAt": 30,
    "eth_getLogs": 60,
    "eth_getBlockByNumber": 60,
    "eth_getTransactionReceipt": 120,
    "eth_chainId": 3600,
    "_default": 15,
}

TTL_JITTER_PCT = 0.15
MIN_TTL = 3
MAX_TTL = 86400
COMPRESS_THRESHOLD = 1024
CACHE_PREFIX = "rmi:rpc:"
LOCK_PREFIX = "rmi:rpc_lock:"
LOCK_TTL = 5


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    sets: int = 0
    lock_waits: int = 0
    redis_errors: int = 0
    mem_hits: int = 0
    compress_saved: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return (self.hits / total * 100) if total > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "sets": self.sets,
            "hit_rate_pct": round(self.hit_rate, 1),
            "lock_waits": self.lock_waits,
            "redis_errors": self.redis_errors,
            "mem_hits": self.mem_hits,
            "compress_saved_bytes": self.compress_saved,
        }


class RpcCacheClient:
    """Redis-backed L1+L2 cache wrapping ConsensusRpcClient.

    L1: In-memory dict (sub-millisecond, per-process)
    L2: Redis (shared across workers, persistent)

    Every RPC call goes through: L1 -> L2 -> actual RPC -> store in both.
    Stampede protection prevents duplicate RPC calls for same key.
    """

    L1_MAX_SIZE = 1024

    def __init__(self, redis_url=None, redis_password=None, rpc_client=None):
        self._redis = None
        self._redis_url = redis_url
        self._redis_password = redis_password
        self._redis_failed = False
        self._redis_init_lock = asyncio.Lock()
        self._l1 = {}  # key -> (expiry_ts, bytes)
        self._l1_lock = asyncio.Lock()
        self._rpc = rpc_client
        self.stats = CacheStats()
        self._populating = {}  # key -> asyncio.Event
        self._populating_lock = asyncio.Lock()

    # ── Redis Connection ─────────────────────────────────────────────────

    async def _get_redis(self):
        """Lazy Redis init. Returns None if unavailable."""
        if self._redis is not None:
            return self._redis
        if self._redis_failed:
            return None
        async with self._redis_init_lock:
            if self._redis is not None:
                return self._redis
            if self._redis_failed:
                return None
            try:
                host = self._redis_url or os.getenv("REDIS_HOST", "rmi-redis")
                port = int(os.getenv("REDIS_PORT", "6379"))
                password = self._redis_password or os.getenv("REDIS_PASSWORD", "")
                url = f"redis://:{password}@{host}:{port}" if password else f"redis://{host}:{port}"
                self._redis = aioredis.from_url(url, socket_connect_timeout=2, socket_timeout=2, decode_responses=False)
                await self._redis.ping()
                logger.info("RpcCache: Redis connected OK")
                return self._redis
            except Exception as e:
                logger.warning(f"RpcCache: Redis unavailable ({e}), using L1 only")
                self._redis_failed = True
                self._redis = None
                return None

    async def _get_rpc(self):
        """Get or create underlying ConsensusRpcClient."""
        if self._rpc is None:
            self._rpc = get_consensus_rpc()
        return self._rpc

    # ── Key Generation ──────────────────────────────────────────────────

    @staticmethod
    def make_key(method, params, chain="solana"):
        raw = f"{chain}:{method}:{json.dumps(params, sort_keys=True, default=str)}"
        digest = hashlib.sha256(raw.encode()).hexdigest()[:24]
        return f"{CACHE_PREFIX}{chain}:{method}:{digest}"

    @staticmethod
    def get_ttl(method):
        base = TTL_TABLE.get(method, TTL_TABLE["_default"])
        base = max(MIN_TTL, min(MAX_TTL, base))
        jitter = base * TTL_JITTER_PCT * (random.random() * 2 - 1)
        return max(MIN_TTL, base + jitter)

    # ── Serialization ───────────────────────────────────────────────────

    @staticmethod
    def serialize(value, confidence, sources):
        payload = json.dumps(
            {"v": value, "c": confidence, "s": sources, "t": time.time()},
            default=str,
            separators=(",", ":"),
        )
        data = payload.encode("utf-8")
        if len(data) > COMPRESS_THRESHOLD:
            data = b"Z" + zlib.compress(data, level=6)
        return data

    @staticmethod
    def deserialize(data):
        try:
            if data and data[:1] == b"Z":
                data = zlib.decompress(data[1:])
            return json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, zlib.error, UnicodeDecodeError):
            return None

    # ── L1 In-Memory Cache ──────────────────────────────────────────────

    async def _l1_get(self, key):
        async with self._l1_lock:
            entry = self._l1.get(key)
            if entry is None:
                return None
            expiry, data = entry
            if time.monotonic() > expiry:
                del self._l1[key]
                return None
            self.stats.mem_hits += 1
            return self.deserialize(data)

    async def _l1_set(self, key, data, ttl):
        async with self._l1_lock:
            if len(self._l1) >= self.L1_MAX_SIZE:
                oldest = min(self._l1.keys(), key=lambda k: self._l1[k][0])
                del self._l1[oldest]
            self._l1[key] = (time.monotonic() + ttl, data)

    # ── Core Operations ─────────────────────────────────────────────────

    async def get(self, method, params, chain="solana", bypass_cache=False):
        """Check L1 then L2 cache. Returns ConsensusResult or None."""
        if bypass_cache:
            return None
        key = self.make_key(method, params, chain)
        cached = await self._l1_get(key)
        if cached:
            self.stats.hits += 1
            return _dict_to_result(cached)
        redis = await self._get_redis()
        if redis:
            try:
                raw = await redis.get(key)
                if raw:
                    cached = self.deserialize(raw)
                    if cached:
                        ttl = self.get_ttl(method)
                        await self._l1_set(key, raw, ttl)
                        self.stats.hits += 1
                        return _dict_to_result(cached)
            except Exception as e:
                self.stats.redis_errors += 1
                logger.debug(f"RpcCache Redis get error: {e}")
        self.stats.misses += 1
        return None

    async def set(self, method, params, chain, result):
        """Store result in L1+L2 cache."""
        key = self.make_key(method, params, chain)
        ttl = self.get_ttl(method)
        data = self.serialize(result.value, result.confidence, result.agreed_sources)
        await self._l1_set(key, data, ttl)
        redis = await self._get_redis()
        if redis:
            try:
                await redis.setex(key, int(ttl) + 1, data)
                self.stats.sets += 1
            except Exception as e:
                self.stats.redis_errors += 1
                logger.debug(f"RpcCache Redis set error: {e}")

    async def query_with_cache(self, method, params, chain="solana", min_agreement=2, bypass_cache=False):
        """Main entry: cache hit -> return; miss -> query RPC -> cache -> return."""
        key = self.make_key(method, params, chain)
        cached = await self.get(method, params, chain, bypass_cache=bypass_cache)
        if cached:
            return cached
        # Stampede protection
        async with self._populating_lock:
            populating = self._populating.get(key)
        if populating is not None:
            self.stats.lock_waits += 1
            try:
                await asyncio.wait_for(populating.wait(), timeout=LOCK_TTL)
                cached = await self.get(method, params, chain)
                if cached:
                    return cached
            except TimeoutError:
                pass
        event = asyncio.Event()
        async with self._populating_lock:
            self._populating[key] = event
        try:
            rpc = await self._get_rpc()
            if chain == "solana":
                result = await rpc.query_with_consensus(method, params, min_agreement)
            else:
                try:
                    chain_id = int(chain) if chain.isdigit() else 1
                except ValueError:
                    chain_id = 1
                result = await rpc.evm_query_with_consensus(chain_id, method, params)
            if result.confidence > 0:
                await self.set(method, params, chain, result)
            return result
        finally:
            event.set()
            async with self._populating_lock:
                self._populating.pop(key, None)

    # ── Convenience Methods ─────────────────────────────────────────────

    async def get_account_info(self, address, chain="solana", bypass_cache=False):
        return await self.query_with_cache(
            "getAccountInfo",
            [address, {"encoding": "jsonParsed"}],
            chain=chain,
            bypass_cache=bypass_cache,
        )

    async def get_balance(self, address, chain="solana", bypass_cache=False):
        return await self.query_with_cache("getBalance", [address], chain=chain, bypass_cache=bypass_cache)

    async def get_token_supply(self, mint, chain="solana", bypass_cache=False):
        return await self.query_with_cache("getTokenSupply", [mint], chain=chain, bypass_cache=bypass_cache)

    async def get_signatures_for_address(self, address, limit=20, chain="solana", bypass_cache=False):
        return await self.query_with_cache(
            "getSignaturesForAddress",
            [address, {"limit": limit}],
            chain=chain,
            bypass_cache=bypass_cache,
        )

    async def get_token_account_balance(self, token_account, chain="solana", bypass_cache=False):
        return await self.query_with_cache(
            "getTokenAccountBalance", [token_account], chain=chain, bypass_cache=bypass_cache
        )

    # ── Invalidation ────────────────────────────────────────────────────

    async def invalidate_address(self, address, chain="solana"):
        """Invalidate all cached data for a specific address."""
        pattern = f"{CACHE_PREFIX}{chain}:*"
        async with self._l1_lock:
            addr_digest = hashlib.sha256(address.encode()).hexdigest()[:16]
            keys_to_del = [k for k in self._l1 if addr_digest.lower() in k.lower()]
            for k in keys_to_del:
                del self._l1[k]
        redis = await self._get_redis()
        if redis:
            try:
                cursor = 0
                deleted = 0
                while True:
                    cursor, keys = await redis.scan(cursor, match=pattern, count=100)
                    for key in keys:
                        key_str = key.decode() if isinstance(key, bytes) else key
                        try:
                            raw = await redis.get(key_str)
                            if raw:
                                cached = self.deserialize(raw)
                                if cached and address.lower() in json.dumps(cached, default=str).lower():
                                    await redis.delete(key_str)
                                    deleted += 1
                        except Exception:
                            pass
                    if cursor == 0:
                        break
                if deleted:
                    logger.debug(f"Invalidated {deleted} cache entries for {address[:12]}...")
            except Exception:
                pass

    # ── Health ──────────────────────────────────────────────────────────

    async def health(self):
        redis_ok = False
        redis = await self._get_redis()
        if redis:
            try:
                await redis.ping()
                redis_ok = True
            except Exception:
                pass
        async with self._l1_lock:
            l1_size = len(self._l1)
        return {
            "redis_available": redis_ok,
            "l1_size": l1_size,
            "l1_max": self.L1_MAX_SIZE,
            **self.stats.to_dict(),
        }

    async def clear_l1(self):
        async with self._l1_lock:
            self._l1.clear()
            logger.info("RpcCache: L1 cache cleared")


# ── Helpers ────────────────────────────────────────────────────────────────


def _dict_to_result(d):
    return ConsensusResult(
        value=d.get("v"),
        confidence=d.get("c", 0.0),
        agreed_sources=d.get("s", []),
        total_sources=len(d.get("s", [])),
        response_count=len(d.get("s", [])),
    )


# ── Singleton ──────────────────────────────────────────────────────────────

_cache_client = None


def get_rpc_cache():
    global _cache_client
    if _cache_client is None:
        _cache_client = RpcCacheClient()
    return _cache_client
