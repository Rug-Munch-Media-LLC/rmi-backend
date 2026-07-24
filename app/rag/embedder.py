"""RAG Embedder Protocol + Implementations.

Provides a clean embedder abstraction with:
  - EmbedderProtocol ABC for all embedders
  - BGE_M3_Embedder — HTTP client for the bge-m3 Docker container
  - OllamaEmbedder — Ollama /api/embeddings client
  - CachedEmbedder — in-memory LRU + optional Redis
  - FallbackEmbedder — primary → secondary with dimension normalization
  - get_embedder() — factory that wires everything from settings
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from typing import Any

import httpx
import numpy as np

from app.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

# ── Known embedding dimensions ─────────────────────────────────────────
KNOWN_DIMS: dict[str, int] = {
    "bge-m3": 1024,
    "nomic-embed-text": 768,
    "qwen3-embed:4b": 2048,
    "qwen3-embed:4b-lowctx": 2048,
    "llama-nemotron-embed-vl-1b-v2:free": 2048,
    "text-embedding-ada-002": 1536,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
}


def lookup_dim(model_name: str) -> int:
    """Look up or default to 1024 for unknown models."""
    return KNOWN_DIMS.get(model_name, 1024)


def normalize_dim(vec: list[float], target_dim: int) -> list[float]:
    """Pad/truncate *vec* to *target_dim*, then L2-normalize.

    Lossy when truncating — only intended for cross-backend fallback
    where primary and secondary have different dimensions (e.g. bge-m3
    1024d -> nomic-embed-text 768d).
    """
    arr = np.asarray(vec, dtype=np.float32)
    if arr.shape[0] < target_dim:
        arr = np.concatenate([arr, np.zeros(target_dim - arr.shape[0], dtype=np.float32)])
    elif arr.shape[0] > target_dim:
        log.warning("embedder_dim_truncation from=%d to=%d", arr.shape[0], target_dim)
        arr = arr[:target_dim]
    norm = float(np.linalg.norm(arr))
    if norm > 0:
        arr = arr / norm
    return arr.tolist()


# ═══════════════════════════════════════════════════════════════════════════
# Protocol
# ═══════════════════════════════════════════════════════════════════════════

class EmbedderProtocol(ABC):
    """Abstract embedder. All implementations share this interface."""

    @property
    @abstractmethod
    def dim(self) -> int:
        """Output dimension of this embedder."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable identifier (e.g. 'bge-m3', 'nomic-embed-text')."""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Returns one vector per input text.

        Raises RuntimeError if the backend is unreachable, returns wrong
        dimensions, or produces empty results.
        """

    async def embed_one(self, text: str) -> list[float]:
        """Convenience: embed a single text."""
        results = await self.embed([text])
        if not results:
            raise RuntimeError(f"{self.name} returned empty result for single text")
        return results[0]


# ═══════════════════════════════════════════════════════════════════════════
# BGE-M3  (Docker container HTTP API)
# ═══════════════════════════════════════════════════════════════════════════

class BGE_M3_Embedder(EmbedderProtocol):
    """BGE-M3 embedder via its container's HTTP API.

    Expects a FastAPI service at *url* with a POST ``/embed`` endpoint
    that accepts ``{"texts": ["..."]}`` and returns
    ``{"embeddings": [[...]], "dim": 1024}``.
    """

    def __init__(
        self,
        url: str = "",
        dim: int = 1024,
        timeout: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        self._url = (url or settings.bge_m3_url).rstrip("/")
        self._dim = dim
        self._timeout = timeout
        self._max_retries = max_retries
        self._client: httpx.AsyncClient | None = None

    # ── client lifecycle ──────────────────────────────────────────────────
    @property
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                limits=httpx.Limits(max_keepalive_connections=4, max_connections=8),
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── EmbedderProtocol ──────────────────────────────────────────────────
    @property
    def dim(self) -> int:
        return self._dim

    @property
    def name(self) -> str:
        return "bge-m3"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = await self._http.post(
                    f"{self._url}/embed",
                    json={"texts": texts},
                )
                resp.raise_for_status()
                data = resp.json()
                vectors: list[list[float]] | None = data.get("embeddings")
                if not vectors:
                    raise RuntimeError("bge-m3 returned empty embeddings list")
                self._validate(vectors)
                return vectors
            except (httpx.HTTPError, OSError, RuntimeError) as exc:
                last_exc = exc
                log.warning("bge_m3_attempt_failed attempt=%d error=%s", attempt, exc)
                if attempt < self._max_retries:
                    await asyncio.sleep(0.5 * (attempt + 1))

        raise RuntimeError(
            f"bge-m3 embed failed after {self._max_retries + 1} attempts"
        ) from last_exc

    def _validate(self, vectors: list[list[float]]) -> None:
        for i, vec in enumerate(vectors):
            actual = len(vec)
            if actual != self._dim:
                raise RuntimeError(
                    f"bge-m3 returned {actual}d vector for text[{i}], "
                    f"expected {self._dim}d"
                )


# ═══════════════════════════════════════════════════════════════════════════
# Ollama  (Ollama /api/embeddings)
# ═══════════════════════════════════════════════════════════════════════════

class OllamaEmbedder(EmbedderProtocol):
    """Ollama embedder via its HTTP API.

    Calls ``POST {url}/api/embeddings`` with model and prompt.
    Supports batching — tries native batch first, falls back to
    sequential single-text calls for models with limited batch support.
    """

    def __init__(
        self,
        model: str = "",
        url: str = "",
        dim: int = 0,
        timeout: float = 30.0,
    ) -> None:
        self._model = model or settings.embedder_fallback
        self._url = (url or settings.ollama_url).rstrip("/")
        self._dim = dim if dim > 0 else lookup_dim(self._model)
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    @property
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                limits=httpx.Limits(max_keepalive_connections=4, max_connections=8),
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def name(self) -> str:
        return self._model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        # Try native batch first (most Ollama models support this)
        try:
            resp = await self._http.post(
                f"{self._url}/api/embeddings",
                json={"model": self._model, "prompt": texts},
            )
            resp.raise_for_status()
            data = resp.json()

            # Ollama returns {"embedding": [[...]]} for batch
            # or {"embedding": [...]} for single
            raw = data.get("embedding")
            if raw is None:
                raise RuntimeError(f"Ollama response missing 'embedding': {list(data.keys())}")

            if raw and isinstance(raw[0], list):
                vectors: list[list[float]] = raw
            else:
                vectors = [raw]

            self._validate(vectors, texts)
            return vectors

        except (httpx.HTTPError, OSError, RuntimeError) as exc:
            log.warning(
                "ollama_batch_embed_failed model=%s error=%s",
                self._model, exc,
            )
            return await self._embed_sequential(texts)

    async def _embed_sequential(self, texts: list[str]) -> list[list[float]]:
        """Fallback: embed one text at a time via Ollama."""
        results: list[list[float]] = []
        for i, text in enumerate(texts):
            try:
                resp = await self._http.post(
                    f"{self._url}/api/embeddings",
                    json={"model": self._model, "prompt": text},
                )
                resp.raise_for_status()
                vec = resp.json().get("embedding")
                if not vec or not isinstance(vec, list):
                    log.warning("ollama_empty_embedding index=%d", i)
                    vec = [0.0] * self._dim
                results.append(vec)
            except (httpx.HTTPError, OSError) as exc:
                raise RuntimeError(
                    f"Ollama embed failed on text[{i}]: {exc}"
                ) from exc

        self._validate(results, texts)
        return results

    def _validate(self, vectors: list[list[float]], texts: list[str]) -> None:
        if len(vectors) != len(texts):
            raise RuntimeError(
                f"Ollama returned {len(vectors)} vectors for {len(texts)} texts"
            )
        for i, vec in enumerate(vectors):
            actual = len(vec)
            if actual != self._dim:
                # First mismatch: auto-detect and update dimension
                if self._dim == 0 or KNOWN_DIMS.get(self._model) != actual:
                    log.info(
                        "ollama_auto_dim model=%s expected=%d actual=%d",
                        self._model, self._dim, actual,
                    )
                    KNOWN_DIMS[self._model] = actual
                    self._dim = actual
                else:
                    raise RuntimeError(
                        f"Ollama model {self._model} returned {actual}d vector "
                        f"for text[{i}], expected {self._dim}d"
                    )


# ═══════════════════════════════════════════════════════════════════════════
# CachedEmbedder  (in-memory LRU + optional Redis)
# ═══════════════════════════════════════════════════════════════════════════

class _MemoryCache:
    """Simple TTL-based in-memory cache with LRU eviction."""

    def __init__(self, maxsize: int = 10_000, default_ttl: int = 86_400) -> None:
        self._maxsize = maxsize
        self._default_ttl = default_ttl
        self._store: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            self._misses += 1
            return None
        expires_at, value = entry
        if time.monotonic() > expires_at:
            self._store.pop(key, None)
            self._misses += 1
            return None
        self._hits += 1
        self._store.move_to_end(key)
        return value

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        expires = time.monotonic() + (ttl if ttl is not None else self._default_ttl)
        self._store[key] = (expires, value)
        self._store.move_to_end(key)
        while len(self._store) > self._maxsize:
            self._store.popitem(last=False)

    @property
    def stats(self) -> dict[str, Any]:
        total = self._hits + self._misses
        return {
            "size": len(self._store),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total * 100, 1) if total > 0 else 0.0,
        }


class CachedEmbedder(EmbedderProtocol):
    """Wraps any ``EmbedderProtocol`` with an in-memory LRU cache.

    Optionally uses Redis as a second-level cache when available.
    Cache keys are ``embed:{dim}:{md5(text)}``.
    """

    def __init__(
        self,
        inner: EmbedderProtocol,
        ttl: int = 86_400,
        maxsize: int = 10_000,
        redis_url: str = "",
    ) -> None:
        self._inner = inner
        self._ttl = ttl
        self._mem = _MemoryCache(maxsize=maxsize, default_ttl=ttl)
        self._redis: Any = None
        self._redis_ok = False
        if redis_url or os.getenv("REDIS_URL"):
            self._init_redis(redis_url or "")

    def _init_redis(self, url: str) -> None:
        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(
                url or settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            self._redis_ok = True
        except Exception as exc:
            log.info("cached_embedder_redis_unavailable: %s", exc)
            self._redis_ok = False

    @property
    def dim(self) -> int:
        return self._inner.dim

    @property
    def name(self) -> str:
        return f"cached:{self._inner.name}"

    def _cache_key(self, text: str) -> str:
        h = hashlib.md5(text.encode("utf-8"), usedforsecurity=False).hexdigest()
        return f"embed:{self._inner.dim}:{h}"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        # Check memory cache for all texts
        keys = [self._cache_key(t) for t in texts]
        cached = [self._mem.get(k) for k in keys]

        # Identify uncached indices
        uncached_indices = [i for i, v in enumerate(cached) if v is None]
        if not uncached_indices:
            return cached  # type: ignore[return-value]

        # Hit the inner embedder for uncached texts
        uncached_texts = [texts[i] for i in uncached_indices]
        try:
            fresh = await self._inner.embed(uncached_texts)
        except RuntimeError:
            log.warning("cached_embedder_inner_failed uncached=%d", len(uncached_indices))
            fresh = [[0.0] * self._inner.dim] * len(uncached_texts)

        # Store in memory cache
        for idx, vec in zip(uncached_indices, fresh, strict=False):
            self._mem.set(keys[idx], vec, self._ttl)
            cached[idx] = vec

        # Fire-and-forget Redis write for uncached vectors
        if self._redis_ok:
            asyncio.ensure_future(self._redis_bulk_set(keys, fresh, uncached_indices))

        return cached  # type: ignore[return-value]

    async def _redis_bulk_set(
        self,
        keys: list[str],
        fresh: list[list[float]],
        indices: list[int],
    ) -> None:
        try:
            pipe = self._redis.pipeline()
            for idx, vec in zip(indices, fresh, strict=False):
                pipe.setex(keys[idx], self._ttl, json.dumps(vec))
            await pipe.execute()
        except Exception:
            log.debug("redis_bulk_set_failed")

    async def embed_one(self, text: str) -> list[float]:
        key = self._cache_key(text)
        cached = self._mem.get(key)
        if cached is not None:
            return cached

        vec = await self._inner.embed_one(text)
        self._mem.set(key, vec, self._ttl)
        if self._redis_ok:
            asyncio.ensure_future(self._redis_single_set(key, vec))
        return vec

    async def _redis_single_set(self, key: str, vec: list[float]) -> None:
        try:
            await self._redis.setex(key, self._ttl, json.dumps(vec))
        except Exception:
            log.debug("redis_single_set_failed %s", key[:20])

    def stats(self) -> dict[str, Any]:
        return {"memory": self._mem.stats, "redis_ok": self._redis_ok}


# ═══════════════════════════════════════════════════════════════════════════
# FallbackEmbedder  (primary -> secondary with dimension normalization)
# ═══════════════════════════════════════════════════════════════════════════

class FallbackEmbedder(EmbedderProtocol):
    """Tries the *primary* embedder first; falls back to *secondary* on failure.

    If the secondary returns a different dimension, vectors are padded/truncated
    and L2-normalized to match the primary's dimension so downstream collections
    remain compatible.
    """

    def __init__(
        self,
        primary: EmbedderProtocol,
        secondary: EmbedderProtocol,
    ) -> None:
        self._primary = primary
        self._secondary = secondary

    @property
    def dim(self) -> int:
        return self._primary.dim

    @property
    def name(self) -> str:
        primary_name = self._primary.name
        secondary_name = self._secondary.name
        return f"{primary_name}->{secondary_name}"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            return await self._primary.embed(texts)
        except (RuntimeError, httpx.HTTPError, OSError) as exc:
            log.warning(
                "fallback_embedder_primary_failed primary=%s error=%s",
                self._primary.name,
                exc,
            )

        # Fallback: secondary may have a different dimension
        try:
            vectors = await self._secondary.embed(texts)
        except (RuntimeError, httpx.HTTPError, OSError) as exc:
            raise RuntimeError(
                f"FallbackEmbedder: both primary ({self._primary.name}) and "
                f"secondary ({self._secondary.name}) failed. Last error: {exc}"
            ) from exc

        target_dim = self._primary.dim
        if vectors and len(vectors[0]) != target_dim:
            log.info(
                "fallback_embedder_normalizing from_dim=%d to_dim=%d",
                len(vectors[0]),
                target_dim,
            )
            vectors = [normalize_dim(v, target_dim) for v in vectors]

        return vectors


# ═══════════════════════════════════════════════════════════════════════════
# Factory
# ═══════════════════════════════════════════════════════════════════════════

def _create_bge_m3() -> BGE_M3_Embedder:
    return BGE_M3_Embedder(url=settings.bge_m3_url, dim=1024)


def _create_ollama(model: str | None = None) -> OllamaEmbedder:
    return OllamaEmbedder(model=model or settings.embedder_fallback)


def get_embedder(
    primary: str | None = None,
    fallback: str | None = None,
    enable_cache: bool = True,
) -> EmbedderProtocol:
    """Build and return a fully-wired embedder chain.

    Args:
        primary: Primary backend name (``"bge-m3"`` | ``"ollama"``).
                 Defaults to ``settings.embedder_primary``.
        fallback: Fallback Ollama model name (e.g. ``"nomic-embed-text"``).
                  Defaults to ``settings.embedder_fallback``.
        enable_cache: Wrap in ``CachedEmbedder`` for in-memory + Redis cache.

    Returns:
        An ``EmbedderProtocol`` instance ready for ``await embed(texts)``.

    Examples::

        embedder = get_embedder()
        vectors = await embedder.embed(["scam token 0x...", "phishing site..."])

        embedder_no_cache = get_embedder(enable_cache=False)
    """
    primary_name = primary or settings.embedder_primary
    fallback_model = fallback or settings.embedder_fallback

    # Build primary
    if primary_name == "bge-m3":
        primary_embedder: EmbedderProtocol = _create_bge_m3()
    elif primary_name == "ollama":
        primary_embedder = _create_ollama(model=fallback_model)
    else:
        log.warning(
            "get_embedder_unknown_primary=%s falling_back_to_bge_m3",
            primary_name,
        )
        primary_embedder = _create_bge_m3()

    # Build secondary (always Ollama with fallback model)
    secondary = _create_ollama(model=fallback_model)

    # Wire fallback chain
    chain: EmbedderProtocol = FallbackEmbedder(
        primary=primary_embedder,
        secondary=secondary,
    )

    # Wrap in cache
    if enable_cache:
        chain = CachedEmbedder(
            inner=chain,
            ttl=settings.embedder_cache_ttl,
            maxsize=settings.embedder_cache_maxsize,
        )

    return chain


# ═══════════════════════════════════════════════════════════════════════════
# Singleton for application-wide use
# ═══════════════════════════════════════════════════════════════════════════

_embedder: EmbedderProtocol | None = None


def get_default_embedder() -> EmbedderProtocol:
    """Lazily-initialized singleton embedder.

    Use this in application code rather than calling ``get_embedder()``
    repeatedly so that connections and caches are shared.
    """
    global _embedder
    if _embedder is None:
        _embedder = get_embedder()
    return _embedder


async def shutdown_embedder() -> None:
    """Close pooled connections. Call during app shutdown."""
    global _embedder
    if _embedder is None:
        return

    # Unwrap to find the inner HTTP clients
    inner = _embedder
    if isinstance(inner, CachedEmbedder):
        # Access _inner via name-mangled attribute
        inner = inner._CachedEmbedder__inner  # type: ignore[attr-defined]

    if isinstance(inner, FallbackEmbedder):
        for sub in [inner._FallbackEmbedder__primary, inner._FallbackEmbedder__secondary]:  # type: ignore[attr-defined]
            if hasattr(sub, "close"):
                await sub.close()
    elif hasattr(inner, "close"):
        await inner.close()

    _embedder = None
