"""L1 in-memory cache for unified data layer."""

import time
from typing import Any


class TieredCache:
    """L1 in-memory cache with TTL and LRU eviction."""

    def __init__(self, max_l1: int = 2048) -> None:
        self._l1: dict[str, tuple[float, Any]] = {}
        self._max = max_l1
        self.hits = 0
        self.misses = 0

    async def get(self, key: str) -> Any | None:
        entry = self._l1.get(key)
        if entry:
            expiry, data = entry
            if time.monotonic() < expiry:
                self.hits += 1
                return data
            del self._l1[key]
        self.misses += 1
        return None

    async def set(self, key: str, data: Any, ttl: float) -> None:
        self._l1[key] = (time.monotonic() + ttl, data)
        if len(self._l1) > self._max:
            oldest = min(self._l1.keys(), key=lambda k: self._l1[k][0])
            del self._l1[oldest]
