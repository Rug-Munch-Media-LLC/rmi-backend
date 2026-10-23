"""Per-partner sliding-window rate limiting for attribution ingest.

In-process window: correct for a single worker, advisory across workers.
``POLY_ATTRIBUTION_RATE_LIMIT`` is ``"<limit>/<window_seconds>"`` (default
120/60). Denied calls return the seconds to wait via ``Retry-After``.
"""

from __future__ import annotations

import os
import time
from collections import deque


class SlidingWindowLimiter:
    """Fixed-capacity deque of timestamps per key."""

    def __init__(self, limit: int, window_s: float) -> None:
        self._limit = max(1, limit)
        self._window = max(1.0, window_s)
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str) -> tuple[bool, int, int]:
        """Return ``(allowed, remaining, retry_after_seconds)`` and record the hit."""
        now = time.monotonic()
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= self._window:
            hits.popleft()
        if len(hits) >= self._limit:
            retry_after = max(1, int(self._window - (now - hits[0])) + 1)
            return False, 0, retry_after
        hits.append(now)
        return True, self._limit - len(hits), 0


def build_limiter() -> SlidingWindowLimiter:
    """Build the process-wide limiter from env, falling back to 120/60."""
    raw = os.getenv("POLY_ATTRIBUTION_RATE_LIMIT", "120/60")
    limit_s, _, window_s = raw.partition("/")
    try:
        return SlidingWindowLimiter(int(limit_s), float(window_s or 60))
    except ValueError:
        return SlidingWindowLimiter(120, 60.0)


limiter = build_limiter()
