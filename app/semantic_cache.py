#!/usr/bin/env python3
"""
Semantic Query Cache for RMI RAG
=================================
Caches search results keyed by query embedding similarity.
If a new query's embedding is > 0.95 cosine-similar to a cached query,
the cached results are returned instantly - no vector search needed.

Redis key layout:
  sem_cache:{sha256(embedding_prefix)} -> JSON({embedding, results, timestamp})

TTL: 1 hour (crypto data changes fast)
Max: 10 000 entries, LRU eviction via Redis volatile-lru policy.
"""

import hashlib
import json
import os
import time
from typing import Any, Optional

import numpy as np

from app.core.logging import get_logger

logger = get_logger(__name__)

REDIS_HOST = os.getenv("REDIS_HOST", "rmi-redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

CACHE_TTL = 3600  # 1 hour
MAX_CACHE_SIZE = 10000
SIMILARITY_THRESHOLD = 0.95
EMBEDDING_PREFIX_DIMS = 64  # Use first 64 dims for the lookup hash (fast)


class SemanticCache:
    """
    Semantic query result cache.

    On check: embed the query → compute a 64-dim prefix hash →
    scan nearby cached entries → if cosine > 0.95 → return cached results.

    On store: save query embedding + results to Redis with TTL.
    """

    _instance: Optional["SemanticCache"] = None

    def __init__(self) -> None:
        self._redis = None
        self._hits = 0
        self._misses = 0

    @classmethod
    def get_instance(cls) -> "SemanticCache":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def _get_redis(self) -> Any:
        import redis.asyncio as aioredis

        if self._redis is None:
            self._redis = aioredis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                password=REDIS_PASSWORD or None,
                db=0,
                decode_responses=True,
            )
        return self._redis

    # ── Cache key from embedding ────────────────────────────────

    @staticmethod
    def _embedding_hash(embedding: list[float]) -> str:
        """Deterministic hash of the first N dims of an embedding."""
        prefix = embedding[:EMBEDDING_PREFIX_DIMS]
        raw = ",".join(f"{v:.6f}" for v in prefix)
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        """Fast cosine similarity on two embedding vectors."""
        a_arr = np.array(a, dtype=np.float32)
        b_arr = np.array(b, dtype=np.float32)
        # Use common length
        n = min(len(a_arr), len(b_arr))
        a_arr = a_arr[:n]
        b_arr = b_arr[:n]
        dot = float(np.dot(a_arr, b_arr))
        na = float(np.linalg.norm(a_arr))
        nb = float(np.linalg.norm(b_arr))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    # ── Check ────────────────────────────────────────────────────

    async def check(self, query_embedding: list[float]) -> list[dict[str, Any]] | None:
        """
        Check if a sufficiently similar query is cached.

        Returns cached results if found (cosine > SIMILARITY_THRESHOLD),
        or None on cache miss.
        """
        r = await self._get_redis()
        key_prefix = "sem_cache:"

        # Compute our hash bucket and a few adjacent buckets for robustness
        self._embedding_hash(query_embedding)
        candidate_keys = []

        # Scan recent cache entries - use Redis SCAN for efficiency
        # But limit the scan to prevent O(N) scans on huge caches
        try:
            # Use a pattern scan limited by count
            cursor = 0
            scanned = 0
            while scanned < MAX_CACHE_SIZE:
                cursor, keys = await r.scan(cursor, match=f"{key_prefix}*", count=200)
                candidate_keys.extend(keys)
                scanned += len(keys)
                if cursor == 0:
                    break

            if not candidate_keys:
                self._misses += 1
                return None

            # Fetch all candidate entries
            pipe = r.pipeline()
            for k in candidate_keys:
                pipe.get(k)
            raw_entries = await pipe.execute()

            best_sim = 0.0
            best_results = None

            for raw in raw_entries:
                if not raw:
                    continue
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                cached_emb = entry.get("embedding", [])
                if not cached_emb:
                    continue

                sim = self._cosine(query_embedding, cached_emb)
                if sim > best_sim:
                    best_sim = sim
                    best_results = entry.get("results", [])

                if sim >= SIMILARITY_THRESHOLD:
                    break  # Found a match, no need to check more

            if best_sim >= SIMILARITY_THRESHOLD and best_results is not None:
                self._hits += 1
                logger.info(f"Semantic cache HIT: similarity={best_sim:.4f}")
                return best_results

        except Exception as e:
            logger.warning(f"Semantic cache check error: {e}")

        self._misses += 1
        return None

    # ── Store ────────────────────────────────────────────────────

    async def store(
        self,
        query_embedding: list[float],
        results: list[dict[str, Any]],
    ) -> None:
        """
        Store search results for a query embedding.
        """
        r = await self._get_redis()
        h = self._embedding_hash(query_embedding)
        key = f"sem_cache:{h}"

        entry = {
            "embedding": query_embedding,
            "results": results,
            "timestamp": time.time(),
        }

        try:
            await r.setex(key, CACHE_TTL, json.dumps(entry))
            # Enforce max cache size - delete oldest if over limit
            # (Redis volatile-lru handles this, but let's be explicit)
            cache_keys = []
            cursor = 0
            while True:
                cursor, keys = await r.scan(cursor, match="sem_cache:*", count=500)
                cache_keys.extend(keys)
                if cursor == 0:
                    break
            if len(cache_keys) > MAX_CACHE_SIZE:
                # Delete oldest entries (by TTL remaining - lowest TTL = oldest)
                pipe = r.pipeline()
                for k in cache_keys:
                    pipe.ttl(k)
                ttls = await pipe.execute()
                # Sort by remaining TTL ascending (smallest = oldest)
                pairs = sorted(zip(ttls, cache_keys, strict=False))
                to_delete = len(cache_keys) - MAX_CACHE_SIZE
                for _, k in pairs[:to_delete]:
                    await r.delete(k)
                logger.info(f"Pruned {to_delete} semantic cache entries")
        except Exception as e:
            logger.warning(f"Semantic cache store error: {e}")

    # ── Stats ────────────────────────────────────────────────────

    async def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        r = await self._get_redis()
        try:
            count = 0
            cursor = 0
            while True:
                cursor, keys = await r.scan(cursor, match="sem_cache:*", count=500)
                count += len(keys)
                if cursor == 0:
                    break
        except (ConnectionError, OSError, TimeoutError):
            count = -1

        return {
            "entries": count,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / max(1, self._hits + self._misses), 4),
            "similarity_threshold": SIMILARITY_THRESHOLD,
            "ttl": CACHE_TTL,
            "max_size": MAX_CACHE_SIZE,
        }

    # ── Clear ────────────────────────────────────────────────────

    async def clear(self) -> int:
        """Clear all semantic cache entries."""
        r = await self._get_redis()
        deleted = 0
        cursor = 0
        while True:
            cursor, keys = await r.scan(cursor, match="sem_cache:*", count=500)
            if keys:
                await r.delete(*keys)
                deleted += len(keys)
            if cursor == 0:
                break
        logger.info(f"Cleared {deleted} semantic cache entries")
        return deleted


# ══════════════════════════════════════════════════════════════════════
# Singleton accessor
# ══════════════════════════════════════════════════════════════════════

_semantic_cache: SemanticCache | None = None


def get_semantic_cache() -> SemanticCache:
    """Return the singleton SemanticCache instance."""
    global _semantic_cache
    if _semantic_cache is None:
        _semantic_cache = SemanticCache()
    return _semantic_cache
