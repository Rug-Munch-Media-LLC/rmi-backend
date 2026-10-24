"""In-process sliding-window rate limiter for the page assistant.

Deliberately dependency-free: a Redis outage must not disable the gateway, and
the limiter must be importable in unit tests without infrastructure. Each key
(hashed API key or client host) gets ``limit`` requests per ``window`` seconds.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Final

DEFAULT_LIMIT: Final = 20
DEFAULT_WINDOW: Final = 60.0


class SlidingWindowLimiter:
    """Fixed-memory request limiter with a monotonic clock."""

    def __init__(self, limit: int = DEFAULT_LIMIT, window: float = DEFAULT_WINDOW) -> None:
        self.limit = max(1, limit)
        self.window = max(1.0, window)
        self._hits: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, key: str) -> tuple[bool, dict[str, int]]:
        """Record a hit. Returns ``(allowed, headers)``."""
        now = time.monotonic()
        cutoff = now - self.window
        async with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] < cutoff:
                hits.popleft()
            allowed = len(hits) < self.limit
            if allowed:
                hits.append(now)
            remaining = max(0, self.limit - len(hits))
            reset = int(self.window - (now - hits[0])) + 1 if hits else int(self.window)
            if len(self._hits) > 10_000:  # pragma: no cover - safety valve
                self._prune(cutoff)
        return allowed, {
            "limit": self.limit,
            "remaining": remaining,
            "reset": max(1, reset),
        }

    def _prune(self, cutoff: float) -> None:
        """Drop keys whose windows have fully expired."""
        for key in [k for k, v in self._hits.items() if not v or v[-1] < cutoff]:
            self._hits.pop(key, None)

    def reset(self) -> None:
        """Clear all windows (tests and hot reload)."""
        self._hits.clear()


_LIMITER: SlidingWindowLimiter | None = None


def get_limiter() -> SlidingWindowLimiter:
    """Process-wide limiter singleton so limits hold across requests."""
    global _LIMITER
    if _LIMITER is None:
        import os

        limit = int(os.getenv("ASSISTANT_RATE_LIMIT_PER_MIN", str(DEFAULT_LIMIT)))
        _LIMITER = SlidingWindowLimiter(limit=limit)
    return _LIMITER


def client_key(headers: dict[str, str], host: str | None) -> str:
    """Derive a stable, non-reversible limiter key (never log the raw key)."""
    import hashlib

    raw = headers.get("x-api-key") or headers.get("authorization") or ""
    if raw:
        return "key:" + hashlib.sha256(raw.encode()).hexdigest()[:16]
    return "ip:" + (host or "unknown")
