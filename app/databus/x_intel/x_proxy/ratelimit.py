"""Async token bucket guarding outbound proxy requests.

A single bucket is shared by the whole proxy tier so the process cannot burst
past the WebShare residential plan's concurrent/rate ceiling. Callers ``await
acquire()`` before each attempt; the bucket paces them instead of failing.
"""

from __future__ import annotations

import asyncio
import time
from typing import Final

# WebShare residential plans tolerate modest concurrency; stay well under.
DEFAULT_RATE: Final[float] = 4.0  # requests per second
DEFAULT_BURST: Final[int] = 4


class TokenBucket:
    """Rate limiter: ``rate`` tokens/second, ``burst`` capacity, FIFO awaiters.

    The lock is held across the sleep so concurrent callers queue in order
    rather than all waking on the same refill tick.
    """

    def __init__(self, *, rate: float = DEFAULT_RATE, burst: int = DEFAULT_BURST) -> None:
        self._rate = max(rate, 0.001)
        self._capacity = float(max(burst, 1))
        self._tokens = self._capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until one token is available, then consume it."""
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._updated
                self._updated = now
                self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                await asyncio.sleep((1.0 - self._tokens) / self._rate)
