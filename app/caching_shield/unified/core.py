"""Core unified data layer primitives: cache, provider chain, rate limiter."""

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


class TieredCache:
    """Simple tiered cache with TTL support."""

    def __init__(self, max_entries: int = 2048) -> None:
        self._cache: dict[str, tuple[Any, float]] = {}  # key -> (value, expiry)
        self._max = max_entries
        self.hits = 0
        self.misses = 0

    async def get(self, key: str) -> Any | None:
        entry = self._cache.get(key)
        if entry is None:
            self.misses += 1
            return None
        value, expiry = entry
        if time.monotonic() > expiry:
            del self._cache[key]
            self.misses += 1
            return None
        self.hits += 1
        return value

    async def set(self, key: str, value: Any, ttl: int) -> None:
        self._cache[key] = (value, time.monotonic() + ttl)
        if len(self._cache) > self._max:
            oldest = min(self._cache.keys(), key=lambda k: self._cache[k][1])
            del self._cache[oldest]


@dataclass
class ToolResult:
    """Result from a tool data call with provenance tracking."""

    data: Any
    source: str  # which provider returned the data
    cached: bool = False
    fallback_used: bool = False
    latency_ms: float = 0
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "data": self.data,
            "source": self.source,
            "cached": self.cached,
            "fallback": self.fallback_used,
            "latency_ms": round(self.latency_ms, 1),
            "confidence": round(self.confidence, 2),
        }


class ProviderChain:
    """Fallback chain of providers for a data type."""

    def __init__(
        self,
        name: str,
        hedge_after_s: float | None = None,
        per_try_timeout_s: float = 5.0,
    ) -> None:
        self.name = name
        self.hedge_after_s = hedge_after_s
        self.per_try_timeout_s = per_try_timeout_s
        self._providers: list[tuple[str, "Callable[..., Any]", float]] = []  # (name, fn, weight)
        self._last_provider: str | None = None

    def add(self, name: str, fn: "Callable[..., Any]", weight: float) -> "ProviderChain":
        self._providers.append((name, fn, weight))
        return self

    async def fetch(self, **kwargs: Any) -> ToolResult | None:
        if self.hedge_after_s is not None and len(self._providers) > 1:
            return await self._fetch_hedged(**kwargs)
        return await self._fetch_sequential(**kwargs)

    async def _fetch_sequential(self, **kwargs: Any) -> ToolResult | None:
        start = time.monotonic()
        for prov_name, fn, _rps in self._providers:
            try:
                data = await fn(**kwargs)
                if data is not None:
                    self._last_provider = prov_name
                    return ToolResult(
                        data=data,
                        source=prov_name,
                        latency_ms=(time.monotonic() - start) * 1000,
                        fallback_used=(prov_name != self._providers[0][0]),
                    )
            except Exception as e:
                import logging
                logger = logging.getLogger("unified_data")
                logger.debug("Provider %s failed: %s", prov_name, e)
                continue
        return None

    async def _fetch_hedged(self, **kwargs: Any) -> ToolResult | None:
        """Hedged provider execution: the leader gets ``hedge_after_s`` grace; if
        it has not answered, every remaining provider fans out concurrently and
        the first usable result wins. Each attempt is bounded by
        ``per_try_timeout_s`` so a hanging provider cannot set the tail.
        """
        start = time.monotonic()
        primary = self._providers[0][0] if self._providers else ""
        queue = list(self._providers)
        pending: dict[asyncio.Task[ToolResult | None], tuple[str, bool]] = {}

        def _launch(prov_name: str, fn: "Callable[..., Any]") -> None:
            task = asyncio.create_task(asyncio.wait_for(fn(**kwargs), timeout=self.per_try_timeout_s))
            pending[task] = (prov_name, prov_name == primary)

        def _harvest(done: set[asyncio.Task[ToolResult | None]]) -> ToolResult | None:
            for task in done:
                prov_name, is_primary = pending.pop(task)
                try:
                    data = task.result()
                except Exception as e:
                    import logging
                    logger = logging.getLogger("unified_data")
                    logger.debug("Provider %s failed: %s", prov_name, e)
                    continue
                if data is not None:
                    return ToolResult(
                        data=data,
                        source=prov_name,
                        latency_ms=(time.monotonic() - start) * 1000,
                        fallback_used=not is_primary,
                    )
            return None

        try:
            prov_name, fn, _rps = queue.pop(0)
            _launch(prov_name, fn)
            done, _ = await asyncio.wait(pending, timeout=self.hedge_after_s, return_when=asyncio.FIRST_COMPLETED)
            result = _harvest(done) if done else None
            if result is not None:
                return result
            # Leader slow or unusable: fan out every remaining provider.
            while queue:
                pn, f2, _r2 = queue.pop(0)
                _launch(pn, f2)
            while pending:
                done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                result = _harvest(done)
                if result is not None:
                    return result
        finally:
            for task in pending:
                task.cancel()
        return None


class TokenBucketRateLimiter:
    """Token bucket rate limiter for provider-level rate limiting."""

    def __init__(self) -> None:
        self._buckets: dict[str, tuple[float, float]] = {}  # provider -> (tokens, last_refill)
        self._lock = asyncio.Lock()

    async def check_rate(self, provider: str, rps: float) -> bool:
        async with self._lock:
            now = time.monotonic()
            bucket = self._buckets.get(provider)
            if not bucket:
                self._buckets[provider] = (rps, now)
                return True
            tokens, last = bucket
            tokens = min(rps, tokens + (now - last) * rps)
            self._buckets[provider] = (tokens - 1, now) if tokens >= 1 else (tokens, now)
            return tokens >= 1


def make_cache_key(data_type: str, **kwargs: Any) -> str:
    """Generate a deterministic cache key for a data type and arguments."""
    return hashlib.sha256(
        f"{data_type}:{json.dumps(kwargs, sort_keys=True, default=str)}".encode()
    ).hexdigest()[:24]


def ttl_for(data_type: str) -> int:
    """Get TTL in seconds for a data type."""
    return {
        "token_price": 15,
        "token_meta": 120,
        "wallet_balance": 10,
        "risk_scan": 300,
        "tx_history": 30,
        "funding_source": 3600,
    }.get(data_type, 60)