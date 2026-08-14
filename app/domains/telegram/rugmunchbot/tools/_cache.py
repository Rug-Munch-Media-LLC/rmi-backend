"""Short-TTL in-memory cache for the bot tool registry.

Every tool result is cached for a short TTL (default 60-120s) so repeated
questions ("what's the price of X" twice in a row) hit the cache instead of
re-hitting external APIs, while keeping data fresh enough for market claims.
Cached results are still REAL API data — they are never synthesized.

Thread-safety: the bot runs a single asyncio event loop, and every access
happens on that loop (tools are async), so a plain dict is sufficient.
"""

from __future__ import annotations

import time
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    """Minimal TTL cache keyed by ``(namespace, key)``."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], tuple[float, T]] = {}

    def get(self, namespace: str, key: str) -> T | None:
        item = self._store.get((namespace, key))
        if item is None:
            return None
        expires_at, value = item
        if time.monotonic() > expires_at:
            self._store.pop((namespace, key), None)
            return None
        return value

    def set(self, namespace: str, key: str, value: T, ttl: float) -> None:
        self._store[(namespace, key)] = (time.monotonic() + ttl, value)

    def clear(self, namespace: str | None = None) -> None:
        if namespace is None:
            self._store.clear()
            return
        for k in [k for k in self._store if k[0] == namespace]:
            self._store.pop(k, None)

    def __len__(self) -> int:
        return len(self._store)

    def _snapshot(self) -> dict[tuple[str, str], Any]:
        """Test helper: raw store contents (expired entries included)."""
        return dict(self._store)
