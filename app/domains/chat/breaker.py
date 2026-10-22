"""Per-provider circuit breaker.

closed -> 3 consecutive failures -> open for 300 s -> one half-open probe ->
success closes, failure re-opens for another 300 s.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

FAILURE_THRESHOLD = 3
OPEN_SECONDS = 300.0


class CircuitBreaker:
    """Failure counter keyed by ``provider:model``."""

    def __init__(
        self,
        threshold: int = FAILURE_THRESHOLD,
        open_seconds: float = OPEN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.threshold = threshold
        self.open_seconds = open_seconds
        self._clock = clock
        self._failures: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}
        self._probing: set[str] = set()

    def state(self, key: str) -> str:
        opened = self._opened_at.get(key)
        if opened is None:
            return "closed"
        if self._clock() - opened < self.open_seconds:
            return "open"
        return "half_open"

    def allow(self, key: str) -> bool:
        """True if a call may proceed (and claims the half-open probe slot)."""
        current = self.state(key)
        if current == "closed":
            return True
        if current == "open" or key in self._probing:
            return False
        self._probing.add(key)
        return True

    def record_success(self, key: str) -> None:
        self._failures.pop(key, None)
        self._opened_at.pop(key, None)
        self._probing.discard(key)

    def record_failure(self, key: str) -> None:
        self._probing.discard(key)
        failures = self._failures.get(key, 0) + 1
        self._failures[key] = failures
        if key in self._opened_at or failures >= self.threshold:
            self._opened_at[key] = self._clock()

    def reset(self) -> None:
        """Test/ops helper: forget all state."""
        self._failures.clear()
        self._opened_at.clear()
        self._probing.clear()


BREAKER = CircuitBreaker()
