"""
LRU cache with TTL for bounded-memory feed caching.

Exports:
    LRUCache, and all shared pipeline constants.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any

# ══════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════

MAX_CONCURRENT_FETCHES = 10  # Global semaphore
PROVIDER_TIMEOUT_DEFAULT = 15.0  # Seconds per provider
PROVIDER_TIMEOUT_SLOW = 30.0  # For slow providers (RSS with many entries)
CIRCUIT_BREAKER_THRESHOLD = 3  # Failures before circuit opens
CIRCUIT_BREAKER_RESET_SEC = 300  # Seconds before half-open retry
LRU_CACHE_MAXSIZE = 2000  # Max cached entries per cache
DEDUP_SIMILARITY_THRESHOLD = 88  # RapidFuzz score % for near-duplicate
MAX_POST_LENGTH = 100000  # Reject posts over 100K chars
MIN_POST_LENGTH = 5  # Reject posts under 5 chars
MAX_TITLE_LENGTH = 1000  # Reject absurdly long titles
MAX_POSTS_PER_PROVIDER = 100  # Cap on posts from one provider per cycle
SEEN_IDS_MAXSIZE = 50000  # Max tracked seen IDs (prevents OOM)
HEALTH_WINDOW_MINUTES = 60  # Rolling health window


class LRUCache:
    """Thread-safe LRU cache with TTL and maxsize. Prevents OOM."""

    def __init__(self, maxsize: int = LRU_CACHE_MAXSIZE) -> None:
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

    def __len__(self) -> int:
        return len(self._cache)

    def keys_starting_with(self, prefix: str) -> list[str]:
        return [k for k in self._cache if k.startswith(prefix)]


__all__ = [
    "CIRCUIT_BREAKER_RESET_SEC",
    "CIRCUIT_BREAKER_THRESHOLD",
    "DEDUP_SIMILARITY_THRESHOLD",
    "HEALTH_WINDOW_MINUTES",
    "LRU_CACHE_MAXSIZE",
    "MAX_CONCURRENT_FETCHES",
    "MAX_POSTS_PER_PROVIDER",
    "MAX_POST_LENGTH",
    "MAX_TITLE_LENGTH",
    "MIN_POST_LENGTH",
    "PROVIDER_TIMEOUT_DEFAULT",
    "PROVIDER_TIMEOUT_SLOW",
    "SEEN_IDS_MAXSIZE",
    "LRUCache",
]
