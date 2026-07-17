"""
Async Circuit Breaker for external API calls.

Prevents cascading failures when external services degrade.
States: CLOSED (normal) → OPEN (failing) → HALF_OPEN (testing) → CLOSED/OPEN.

Usage:
    _cb = AsyncCircuitBreaker("coingecko", failure_threshold=5)
    result = await _cb.call(lambda: client.get(url))
"""

from __future__ import annotations

import logging
import time
from enum import Enum

logger = logging.getLogger(__name__)


class State(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class AsyncCircuitBreaker:
    """Async circuit breaker with in-memory failure tracking."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max: int = 1,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max = half_open_max
        self._state = State.CLOSED
        self._failures: list[float] = []
        self._opened_at: float = 0.0
        self._half_open_count = 0

    @property
    def state(self) -> State:
        return self._state

    def _prune_failures(self, window: float = 120.0) -> None:
        now = time.monotonic()
        self._failures = [t for t in self._failures if now - t < window]

    def _record_failure(self) -> None:
        self._failures.append(time.monotonic())
        self._prune_failures()
        if len(self._failures) >= self.failure_threshold:
            self._transition(State.OPEN)

    def _record_success(self) -> None:
        self._failures.clear()
        self._half_open_count = 0
        if self._state != State.CLOSED:
            self._transition(State.CLOSED)

    def _transition(self, new_state: State) -> None:
        old = self._state
        self._state = new_state
        if new_state == State.OPEN:
            self._opened_at = time.monotonic()
        logger.warning(
            "circuit_breaker.transition",
            extra={"name": self.name, "from": old.value, "to": new_state.value},
        )

    def _should_attempt(self) -> bool:
        if self._state == State.CLOSED:
            return True
        if self._state == State.OPEN:
            if time.monotonic() - self._opened_at >= self.recovery_timeout:
                self._transition(State.HALF_OPEN)
                self._half_open_count = 0
                return True
            return False
        if self._state == State.HALF_OPEN:
            return self._half_open_count < self.half_open_max
        return True

    async def call(self, coro_factory):
        """Wrap an async callable with circuit breaker protection."""
        if not self._should_attempt():
            raise CircuitBreakerOpenError(f"Circuit {self.name} is OPEN — failing fast")

        if self._state == State.HALF_OPEN:
            self._half_open_count += 1

        try:
            result = await coro_factory()
            self._record_success()
            return result
        except Exception:
            self._record_failure()
            raise

    async def __aenter__(self):
        if not self._should_attempt():
            raise CircuitBreakerOpenError(f"Circuit {self.name} is OPEN — failing fast")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self._record_success()
        elif exc_type is not None:
            self._record_failure()
        return False


class CircuitBreakerOpenError(Exception):
    """Raised when circuit is OPEN and call is rejected."""

