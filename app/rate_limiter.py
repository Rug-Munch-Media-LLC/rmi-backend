# DEPRECATED as API rate limiter: Use app.core.rate_limiter (UnifiedRateLimiter).
# This file is an AI EMBEDDING provider dispatcher — not a general API rate limiter.
# The RateTracker/EmbeddingDispatcher here solves a different problem (load-balancing
# across free embedding providers). For API request rate limiting, use UnifiedRateLimiter.

"""
Rate-Limit-Aware Multi-Provider System
=======================================

Tracks usage across ALL free AI providers in real-time (Redis + memory).
Smart dispatcher routes to the provider with most remaining capacity.
Embedding cache avoids redundant API calls.

Provider limits (verified June 2026):
  OpenRouter free (no credits):  50 req/day,  20 rpm
  OpenRouter free ($10+ credits): 1000 req/day, 20 rpm  ← we have credits
  NVIDIA NIM free:               1000 credits, 40 rpm    ← different keys, same account
  Local Qwen3:                   unlimited,    0 cost
  Hash fallback:                 unlimited,    0 cost

Auto-detects credit status and adjusts limits accordingly.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger

logger = get_logger("rate.limiter")

# ──────────────────────────────────────────────────────────────
# Verified Provider Limits (June 2026)
# ──────────────────────────────────────────────────────────────


@dataclass
class ProviderLimit:
    id: str
    name: str
    model: str
    dims: int
    base_url: str
    key_env: str
    rpm: int  # Requests per minute
    rpd: int  # Requests per day
    free: bool = True
    priority: int = 100  # Higher = preferred within free tier
    timeout: float = 15.0


# Configured limits - auto-detected from credit status
PROVIDERS: list[ProviderLimit] = []


def configure_providers(has_openrouter_credits: bool = True) -> None:
    """Configure provider limits based on credit status."""
    global PROVIDERS

    # OpenRouter - different limits based on credit status
    or_rpd = 1000 if has_openrouter_credits else 50
    PROVIDERS = [
        # ── Tier 1: LOCAL ALWAYS FIRST (free, self-hosted Qwen3) ──
        ProviderLimit(
            id="qwen3_4b_local",
            name="Qwen3-Embedding-4B local",
            model="qwen3-embedding-4b",
            dims=1024,
            base_url=os.getenv("QWEN_EMBED_URL", "http://127.0.0.1:8004/v1") + "/embeddings",
            key_env="",
            rpm=99999,
            rpd=99999,
            free=True,
            priority=120,
        ),
        # 2026-08-29 consolidation: the qwen3_06b_local provider (:8007)
        # is RETIRED — every tier now routes through the 4B (:8004).
        # ── Tier 2: OpenRouter PAID backup (backup account) ──
        ProviderLimit(
            id="qwen3_8b_openrouter_paid",
            name="Qwen3-Embedding-8B via OpenRouter (paid backup)",
            model="qwen/qwen3-embedding-8b",
            dims=1024,
            base_url="https://openrouter.ai/api/v1/embeddings",
            key_env="OPENROUTER_BACKUP_API_KEY",
            rpm=60,
            rpd=or_rpd,
            free=False,
            priority=20,
        ),
        # ── Tier 3: OpenRouter FREE (main account) ──
        ProviderLimit(
            id="qwen3_free_openrouter",
            name="LFM2.5-Embedding-350M free via OpenRouter (main acct, 1024-dim)",
            model=os.getenv("OPENROUTER_FREE_EMBED_MODEL", "liquid/lfm-2.5-embedding-350m:free"),
            dims=1024,
            base_url="https://openrouter.ai/api/v1/embeddings",
            key_env="OPENROUTER_API_KEY",
            rpm=20,
            rpd=or_rpd,
            free=True,
            priority=15,
        ),
        # ── Tier 4: Hash fallback (zero cost, deterministic, last resort) ──
        ProviderLimit(
            id="hash",
            name="Hash Fallback",
            model="hash-sha256",
            dims=384,
            base_url="",
            key_env="",
            rpm=99999,
            rpd=99999,
            free=True,
            priority=0,
        ),
    ]


# Default: assume credits
configure_providers(has_openrouter_credits=True)

# ──────────────────────────────────────────────────────────────
# Redis-Backed Rate Tracker
# ──────────────────────────────────────────────────────────────


class RateTracker:
    """Tracks per-provider usage in Redis + memory. Survives restarts."""

    def __init__(self) -> None:
        self._redis = None
        self._memory: dict[str, dict[str, int]] = {}  # provider_id → {minute, day, total}
        self._last_flush: dict[str, float] = {}

    async def _connect(self) -> None:
        if self._redis:
            return
        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.Redis(
                host=os.getenv("REDIS_HOST", "rmi-redis"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD", ""),
                db=int(os.getenv("REDIS_DB", "0")),
                socket_connect_timeout=2,
                decode_responses=True,
            )
            await self._redis.ping()
        except Exception as e:
            logger.warning(f"RateTracker Redis unavailable: {e} - memory-only mode")
            self._redis = None

    async def _get_counts(self, provider_id: str) -> dict[str, int]:
        """Get current usage counts for a provider."""
        today = time.strftime("%Y-%m-%d")
        minute_key = f"rmi:rate:{provider_id}:{today}:{int(time.time() // 60)}"

        if self._redis:
            try:
                day = await self._redis.get(f"rmi:rate:{provider_id}:{today}")
                minute = await self._redis.get(minute_key)
                total = await self._redis.get(f"rmi:rate:{provider_id}:total")
                return {
                    "day": int(day or 0),
                    "minute": int(minute or 0),
                    "total": int(total or 0),
                }
            except (ConnectionError, OSError):
                pass

        # Fallback to memory
        return self._memory.get(provider_id, {"day": 0, "minute": 0, "total": 0})

    async def _incr(self, provider_id: str) -> None:
        """Increment usage counters."""
        today = time.strftime("%Y-%m-%d")
        minute_key = f"rmi:rate:{provider_id}:{today}:{int(time.time() // 60)}"

        if self._redis:
            try:
                pipe = self._redis.pipeline()
                pipe.incr(f"rmi:rate:{provider_id}:{today}")
                pipe.expire(f"rmi:rate:{provider_id}:{today}", 86400)
                pipe.incr(minute_key)
                pipe.expire(minute_key, 120)
                pipe.incr(f"rmi:rate:{provider_id}:total")
                await pipe.execute()
                return
            except (ConnectionError, OSError):
                pass

        # Memory fallback
        mem = self._memory.setdefault(provider_id, {"day": 0, "minute": 0, "total": 0})
        mem["day"] += 1
        mem["minute"] += 1
        mem["total"] += 1

    async def can_call(self, provider: ProviderLimit) -> bool:
        """Check if we can call this provider without hitting limits."""
        counts = await self._get_counts(provider.id)

        # Use 90% of limit as safety margin
        day_margin = int(provider.rpd * 0.9)
        minute_margin = int(provider.rpm * 0.9)

        if counts["day"] >= day_margin:
            logger.debug(f"{provider.name}: daily limit approached ({counts['day']}/{provider.rpd})")
            return False
        if counts["minute"] >= minute_margin:
            logger.debug(f"{provider.name}: RPM limit approached ({counts['minute']}/{provider.rpm})")
            return False

        return True

    async def record(self, provider: ProviderLimit) -> None:
        """Record a successful call."""
        await self._incr(provider.id)

    async def remaining(self, provider: ProviderLimit) -> dict[str, int]:
        """Get remaining capacity."""
        counts = await self._get_counts(provider.id)
        return {
            "day_remaining": max(0, provider.rpd - counts["day"]),
            "minute_remaining": max(0, provider.rpm - counts["minute"]),
            "total_used": counts["total"],
        }

    async def stats(self) -> dict[str, Any]:
        """Get usage stats for all providers."""
        result = {"providers": [], "timestamp": time.time()}
        for p in PROVIDERS:
            counts = await self._get_counts(p.id)
            result["providers"].append(
                {
                    "id": p.id,
                    "name": p.name,
                    "free": p.free,
                    "day_used": counts["day"],
                    "day_limit": p.rpd,
                    "minute_used": counts["minute"],
                    "minute_limit": p.rpm,
                    "total": counts["total"],
                    "available": await self.can_call(p),
                    "day_pct": round(counts["day"] / max(p.rpd, 1) * 100, 1),
                }
            )
        return result


# ──────────────────────────────────────────────────────────────
# Embedding Cache
# ──────────────────────────────────────────────────────────────


class EmbeddingCache:
    """Caches embedding results to avoid redundant API calls.

    Uses content hash → embedding mapping. Same text always returns
    same embedding from the same provider. Zero API cost for repeats.
    """

    def __init__(self, max_size: int = 10000) -> None:
        self._cache: dict[str, tuple[list[float], str]] = {}  # hash → (vector, provider)
        self._max = max_size
        self._hits = 0
        self._misses = 0

    def key(self, text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()[:32]

    def get(self, text: str) -> tuple[list[float], str] | None:
        k = self.key(text)
        if k in self._cache:
            self._hits += 1
            return self._cache[k]
        self._misses += 1
        return None

    def set(self, text: str, vector: list[float], provider: str) -> None:
        if len(self._cache) >= self._max:
            # Evict oldest 10%
            keys = list(self._cache.keys())[: self._max // 10]
            for k in keys:
                del self._cache[k]
        self._cache[self.key(text)] = (vector, provider)
        # Persist to Redis asynchronously (best-effort)
        self._save_to_redis()

    def _save_to_redis(self) -> None:
        """Persist cache to Redis so it survives restarts."""
        try:
            import json

            import redis

            r = redis.Redis(
                host="localhost",
                port=6379,
                db=3,
                password=os.environ.get("REDIS_PASSWORD", ""),
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            # Save 500 most recent entries with full vectors
            entries = {k: [v[0], v[1]] for k, v in list(self._cache.items())[-500:]}
            r.setex("embed_cache:data", 86400, json.dumps(entries))
        except json.JSONDecodeError:
            pass

    def load_from_redis(self) -> None:
        """Restore cache from Redis on startup."""
        try:
            import json

            import redis

            r = redis.Redis(
                host="localhost",
                port=6379,
                db=3,
                password=os.environ.get("REDIS_PASSWORD", ""),
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            raw = r.get("embed_cache:data")
            if raw:
                entries = json.loads(raw)
                for k, (vec, prov) in entries.items():
                    self._cache[k] = (vec, prov)
                logger.info(f"Loaded {len(entries)} cache entries from Redis")
        except Exception as e:
            logger.info(f"Cache restore skipped: {e}")

    def stats(self) -> dict[str, Any]:
        total = self._hits + self._misses
        return {
            "size": len(self._cache),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / max(total, 1) * 100, 1),
        }


# ──────────────────────────────────────────────────────────────
# Smart Embedding Dispatcher
# ──────────────────────────────────────────────────────────────


class EmbeddingDispatcher:
    """Routes embedding requests to the best provider with available capacity."""

    def __init__(self) -> None:
        self.tracker = RateTracker()
        self.cache = EmbeddingCache()
        self._local_model = None
        self._providers = PROVIDERS

    async def init(self) -> None:
        await self.tracker._connect()
        # Restore cache from Redis if available
        self.cache.load_from_redis()
        # Local embedders are the self-hosted Qwen3 llama.cpp server
        # (:8004) — no in-process model to preload.
        logger.info("Embed providers: local Qwen3 4B first, OR paid 8B backup, OR free LFM")

    async def embed(self, texts: list[str], task: str = "default") -> tuple[list[list[float]], str]:
        """Smart embed with quality-tier routing.

        task: 'scam_detection', 'user_search', 'news_ingestion', 'bulk_ingestion', etc.
        Routes to appropriate provider tier - saves premium credits for critical tasks.
        """
        from app.embed_tiers import get_allowed_providers, get_min_dims, get_tier

        get_tier(task)
        allowed = get_allowed_providers(task)
        min_dims = get_min_dims(task)
        if not texts:
            return [], "none"

        # Check cache first
        cached = []
        to_embed = []
        for t in texts:
            c = self.cache.get(t)
            if c:
                cached.append(c[0])
            else:
                to_embed.append(t)

        if not to_embed:
            return cached, "cache"

        # Pick best available provider - filtered by tier
        vectors = None
        provider_name = "none"

        for p in sorted(self._providers, key=lambda x: -x.priority):
            # Tier filter: only use providers allowed for this task
            # Local/hash always available as fallback
            if allowed and p.id not in allowed and p.id not in ("qwen3_4b_local", "hash"):
                continue
            # Dimension filter
            if p.dims < min_dims and p.id not in ("qwen3_4b_local", "hash"):
                continue

            if not await self.tracker.can_call(p):
                continue

            # ── Local ──
            if p.id == "qwen3_4b_local":
                try:
                    vecs = await self._embed_local(to_embed, provider_id=p.id)
                    if vecs:
                        await self.tracker.record(p)
                        provider_name = p.name
                        vectors = vecs
                        break
                except Exception as e:
                    logger.warning(f"Local: {e}")
                continue

            # ── Hash ──
            if p.id == "hash":
                vecs = self._embed_hash(to_embed)
                await self.tracker.record(p)
                provider_name = p.name
                vectors = vecs
                break

            # ── API providers ──
            key = os.environ.get(p.key_env, "") or os.environ.get(p.key_env.replace("_KEY", "_API_KEY"), "")
            if not key or len(key) < 10:
                continue

            try:
                vecs = await self._embed_api(p, key, to_embed)
                if vecs:
                    await self.tracker.record(p)
                    provider_name = p.name
                    vectors = vecs
                    break
            except Exception as e:
                logger.warning(f"{p.name}: {e}")
                continue

        # Absolute last resort
        if vectors is None:
            vectors = self._embed_hash(to_embed)
            provider_name = "hash_emergency"

        # Enforce the collection dim on every vector (MRL truncate +
        # renormalize). Undersized vectors from a misconfigured provider
        # are dropped loudly rather than poisoning a collection.
        from app.embed_tiers import COLLECTION_DIM, normalize_dims

        cleaned: list[list[float]] = []
        for vec in vectors or []:
            if len(vec) < COLLECTION_DIM:
                logger.error(
                    f"provider {provider_name} returned {len(vec)} dims < required {COLLECTION_DIM}; dropping batch"
                )
                return [], provider_name
            cleaned.append(normalize_dims(vec))
        vectors = cleaned

        # Cache results
        for text, vec in zip(to_embed, texts, strict=False):
            self.cache.set(text, vec, provider_name)

        # Merge with cached
        result = []
        ci = 0
        for t in texts:
            c = self.cache.get(t)
            if c and ci < len(cached):
                result.append(cached[ci])
                ci += 1
            elif vectors and len(result) - ci < len(vectors):
                result.append(vectors[len(result) - ci])
            else:
                result.append([])

        return result, provider_name

    async def _embed_api(self, p: ProviderLimit, key: str, texts: list[str]) -> list[list[float]] | None:
        import httpx

        # Gemini uses different auth (key in query param) and different payload format
        if p.id.startswith("gemini"):
            vectors = []
            async with httpx.AsyncClient(timeout=p.timeout) as c:
                for text in texts:
                    r = await c.post(
                        f"{p.base_url}?key={key}",
                        json={"content": {"parts": [{"text": text}]}},
                    )
                    if r.status_code == 200:
                        data = r.json()
                        vec = data.get("embedding", {}).get("values", [])
                        if vec:
                            vectors.append(vec)
                    elif r.status_code == 429:
                        logger.warning(f"{p.name} rate limited (429)")
                        return None
                    else:
                        logger.warning(f"{p.name} HTTP {r.status_code}")
                        return None
            return vectors if vectors else None

        # OpenRouter / NVIDIA format
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        if "openrouter" in p.base_url:
            headers["HTTP-Referer"] = "https://rugmunch.io"
            headers["X-Title"] = "RMI SENTINEL"

        payload = {"model": p.model, "input": texts}

        async with httpx.AsyncClient(timeout=p.timeout) as c:
            r = await c.post(p.base_url, json=payload, headers=headers)
            if r.status_code == 429:
                logger.warning(f"{p.name} rate limited (429)")
                return None
            if r.status_code != 200:
                logger.warning(f"{p.name} HTTP {r.status_code}: {r.text[:150]}")
                return None
            data = r.json()
            return [e.get("embedding", []) for e in data.get("data", [])]

    async def _embed_local(self, texts: list[str], provider_id: str = "qwen3_4b_local") -> list[list[float]] | None:
        """Embed via the self-hosted Qwen3-Embedding-4B llama.cpp server
        (:8004). OpenAI-compatible /v1/embeddings; output already 1024-dim
        matching our collections. 2026-08-29: the 0.6B fast lane (:8007) is
        retired — this is the only local embedder."""
        import os as _os

        base = _os.getenv("QWEN_EMBED_URL", "http://127.0.0.1:8004/v1")
        model = "qwen3-embedding-4b"
        try:
            import httpx as _httpx

            async with _httpx.AsyncClient(timeout=60) as client:
                r = await client.post(
                    f"{base.rstrip('/')}/embeddings",
                    json={"input": texts, "model": model},
                )
                r.raise_for_status()
                data = r.json()
            out = []
            for item in sorted(data.get("data", []), key=lambda d: d.get("index", 0)):
                out.append([float(x) for x in item["embedding"]])
            return out or None
        except Exception as e:
            logger.warning(f"Local embed ({provider_id}): {e}")
            return None

    def _embed_hash(self, texts: list[str]) -> list[list[float]]:
        import numpy as np

        vecs = []
        for t in texts:
            h = hashlib.sha256(t.encode()).digest() + hashlib.sha256(("v2:" + t).encode()).digest()
            v = [(int.from_bytes(h[i : i + 4], "big") / 2**32) * 2 - 1 for i in range(0, min(len(h), 1536), 4)]
            while len(v) < 384:
                v.append(0.0)
            v = v[:384]
            norm = np.sqrt(sum(x * x for x in v))
            if norm > 0:
                v = [x / norm for x in v]
            vecs.append(v)
        return vecs

    async def stats(self) -> dict[str, Any]:
        return {
            "rate_limits": await self.tracker.stats(),
            "cache": self.cache.stats(),
            "providers_configured": len(self._providers),
        }

    def health(self) -> dict[str, Any]:
        """Quick health check (no async needed)."""
        available = sum(1 for p in self._providers if p.free)
        return {
            "providers": len(self._providers),
            "free_providers": available,
            "local_embedders": ["qwen3_4b_local"],
        }


# ── Singleton ──
_dispatcher: EmbeddingDispatcher | None = None


async def get_dispatcher() -> EmbeddingDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = EmbeddingDispatcher()
        await _dispatcher.init()
    return _dispatcher


async def smart_embed(texts: list[str]) -> tuple[list[list[float]], str]:
    return await (await get_dispatcher()).embed(texts)
