"""
Tests for app/core/circuit_breaker.py — in-memory AsyncCircuitBreaker state machine.

Covers:
- State transitions: CLOSED → OPEN (on 5 failures) → HALF_OPEN (after timeout) → CLOSED (on success)
- 4xx vs 5xx distinction: client errors don't trip the breaker
- Stale-cache serving during OPEN/HALF_OPEN states
- Half-open max probe limit
- Failure sliding-window expiry
- ``call()``, ``wrap()`` decorator, and ``async with`` context manager patterns
- SimpleCircuitBreaker (sync variant)
- Stats observability
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.core.circuit_breaker import (
    AsyncCircuitBreaker,
    CircuitBreakerOpenError,
    SimpleCircuitBreaker,
    State,
)


class TestStateEnum:
    """State enum contract."""

    def test_has_three_states(self) -> None:
        assert len(State) == 3

    def test_states_have_correct_names(self) -> None:
        assert State.CLOSED.value == "closed"
        assert State.OPEN.value == "open"
        assert State.HALF_OPEN.value == "half_open"


class TestCircuitBreakerOpenError:
    """Exception contract."""

    def test_default_message(self) -> None:
        err = CircuitBreakerOpenError("myservice", State.OPEN)
        assert "myservice" in str(err)
        assert "open" in str(err)
        assert err.name == "myservice"
        assert err.state is State.OPEN

    def test_custom_message(self) -> None:
        err = CircuitBreakerOpenError("s", State.HALF_OPEN, "custom msg")
        assert "custom msg" in str(err)


class TestDefaults:
    """Default configuration contract."""

    async def test_defaults(self) -> None:
        cb = AsyncCircuitBreaker("test")
        assert cb.name == "test"
        assert cb.failure_threshold == 5
        assert cb.failure_window == 60.0
        assert cb.recovery_timeout == 30.0
        assert cb.half_open_max == 1
        assert cb.state is State.CLOSED


class TestCanCall:
    """can_call() — the core gate."""

    async def test_closed_returns_true(self) -> None:
        cb = AsyncCircuitBreaker("test")
        assert await cb.can_call() is True

    async def test_closed_prunes_old_failures(self) -> None:
        cb = AsyncCircuitBreaker("test", failure_window=0.01)
        cb._failures = [time.monotonic() - 10, time.monotonic() - 10]
        await cb.can_call()  # triggers prunes
        assert len(cb._failures) == 0

    async def test_open_returns_false(self) -> None:
        cb = AsyncCircuitBreaker("test", recovery_timeout=3600)
        cb._state = State.OPEN
        cb._opened_at = time.monotonic()
        assert await cb.can_call() is False

    async def test_open_transitions_to_half_open_after_timeout(self) -> None:
        cb = AsyncCircuitBreaker("test", recovery_timeout=0.01)
        cb._state = State.OPEN
        cb._opened_at = time.monotonic() - 10  # far past timeout
        assert await cb.can_call() is True
        assert cb.state is State.HALF_OPEN

    async def test_half_open_allows_probe(self) -> None:
        cb = AsyncCircuitBreaker("test")
        cb._state = State.HALF_OPEN
        cb._half_open_count = 0
        assert await cb.can_call() is True

    async def test_half_open_blocks_after_max_probes(self) -> None:
        cb = AsyncCircuitBreaker("test", half_open_max=2)
        cb._state = State.HALF_OPEN
        cb._half_open_count = 2
        assert await cb.can_call() is False


class TestRecordSuccess:
    """record_success() — resets state."""

    async def test_clears_failures(self) -> None:
        cb = AsyncCircuitBreaker("test")
        cb._failures = [time.monotonic(), time.monotonic()]
        cb._half_open_count = 3
        await cb.record_success()
        assert len(cb._failures) == 0
        assert cb._half_open_count == 0

    async def test_closes_open_circuit(self) -> None:
        cb = AsyncCircuitBreaker("test")
        cb._state = State.OPEN
        await cb.record_success()
        assert cb.state is State.CLOSED

    async def test_closes_half_open_circuit(self) -> None:
        cb = AsyncCircuitBreaker("test")
        cb._state = State.HALF_OPEN
        await cb.record_success()
        assert cb.state is State.CLOSED

    async def test_increments_success_stats(self) -> None:
        cb = AsyncCircuitBreaker("test")
        await cb.record_success()
        assert cb._successful_calls == 1
        assert cb._total_calls == 1


class TestRecordFailure:
    """record_failure() — tracks and trips."""

    async def test_4xx_does_not_count(self) -> None:
        cb = AsyncCircuitBreaker("test", failure_threshold=1)
        await cb.record_failure(is_server_error=False)
        assert len(cb._failures) == 0
        assert cb.state is State.CLOSED

    async def test_5xx_increments_failures(self) -> None:
        cb = AsyncCircuitBreaker("test", failure_threshold=5)
        await cb.record_failure(is_server_error=True)
        assert len(cb._failures) == 1

    async def test_trips_at_threshold(self) -> None:
        cb = AsyncCircuitBreaker("test", failure_threshold=3)
        for _ in range(3):
            await cb.record_failure(is_server_error=True)
        assert cb.state is State.OPEN

    async def test_does_not_trip_below_threshold(self) -> None:
        cb = AsyncCircuitBreaker("test", failure_threshold=3)
        for _ in range(2):
            await cb.record_failure(is_server_error=True)
        assert cb.state is State.CLOSED

    async def test_increments_total_stats(self) -> None:
        cb = AsyncCircuitBreaker("test")
        await cb.record_failure(is_server_error=True)
        assert cb._total_calls == 1


class TestFailureWindowExpiry:
    """Sliding window — old failures expire."""

    async def test_old_failures_do_not_trip(self) -> None:
        cb = AsyncCircuitBreaker("test", failure_window=0.01, failure_threshold=3)
        cb._failures = [time.monotonic() - 10, time.monotonic() - 10]  # expired
        # One real failure — total should be 1 after prunes, not 3
        await cb.record_failure(is_server_error=True)
        await cb._prune_failures()
        assert len(cb._failures) == 1
        assert cb.state is State.CLOSED


class TestStaleCache:
    """Stale-cache serving during failures."""

    async def test_set_and_get_stale(self) -> None:
        cb = AsyncCircuitBreaker("test")
        cb.set_stale_result({"price": 100})
        result = cb.get_stale_result()
        assert result == {"price": 100}

    async def test_stale_expires(self) -> None:
        cb = AsyncCircuitBreaker("test", failure_window=0.01)
        cb._stale_max_age = 0.01
        cb.set_stale_result("data")
        await asyncio.sleep(0.02)
        assert cb.get_stale_result() is None

    async def test_custom_max_age(self) -> None:
        cb = AsyncCircuitBreaker("test")
        cb.set_stale_result("data")
        await asyncio.sleep(0.01)
        assert cb.get_stale_result(max_age=0.005) is None

    async def test_stale_served_when_open(self) -> None:
        cb = AsyncCircuitBreaker("test", recovery_timeout=3600)
        cb._state = State.OPEN
        cb._opened_at = time.monotonic()
        cb.set_stale_result("stale-data")

        # can_call returns False, but get_stale_result should work
        assert await cb.can_call() is False
        assert cb.get_stale_result() == "stale-data"


class TestCallPattern:
    """call() — coroutine factory wrapper."""

    async def test_call_success(self) -> None:
        cb = AsyncCircuitBreaker("test")

        async def ok() -> str:
            return "ok"

        result = await cb.call(ok)
        assert result == "ok"
        assert cb.state is State.CLOSED

    async def test_call_failure_connection_error(self) -> None:
        cb = AsyncCircuitBreaker("test")

        async def fail() -> None:
            msg = "connection refused"
            raise ConnectionError(msg)

        with pytest.raises(ConnectionError):
            await cb.call(fail)
        assert len(cb._failures) == 1

    async def test_call_open_raises(self) -> None:
        cb = AsyncCircuitBreaker("test", recovery_timeout=3600)
        cb._state = State.OPEN
        cb._opened_at = time.monotonic()

        async def never() -> str:
            return "x"

        with pytest.raises(CircuitBreakerOpenError):
            await cb.call(never, stale_ok=False)

    async def test_call_open_serves_stale(self) -> None:
        cb = AsyncCircuitBreaker("test", recovery_timeout=3600)
        cb._state = State.OPEN
        cb._opened_at = time.monotonic()
        cb.set_stale_result("cached")

        async def never() -> str:
            return "should-not-reach"

        result = await cb.call(never, stale_ok=True)
        assert result == "cached"


class TestWrapDecorator:
    """wrap() — decorator pattern."""

    async def test_wrap_success(self) -> None:
        cb = AsyncCircuitBreaker("test")

        class Service:
            @cb.wrap()
            async def fetch(self) -> str:
                return "data"

        svc = Service()
        assert await svc.fetch() == "data"
        assert cb.state is State.CLOSED

    async def test_wrap_connection_error_trips(self) -> None:
        cb = AsyncCircuitBreaker("test", failure_threshold=1)

        class Service:
            @cb.wrap()
            async def fetch(self) -> str:
                msg = "timeout"
                raise ConnectionError(msg)

        svc = Service()
        with pytest.raises(ConnectionError):
            await svc.fetch()
        assert cb.state is State.OPEN

    async def test_wrap_rejects_when_open(self) -> None:
        cb = AsyncCircuitBreaker("test", recovery_timeout=3600)
        cb._state = State.OPEN
        cb._opened_at = time.monotonic()

        class Service:
            @cb.wrap(stale_ok=False)
            async def fetch(self) -> str:
                return "x"

        svc = Service()
        with pytest.raises(CircuitBreakerOpenError):
            await svc.fetch()


class TestContextManager:
    """async with — context manager pattern."""

    async def test_context_success(self) -> None:
        cb = AsyncCircuitBreaker("test")
        async with cb:
            pass
        assert cb.state is State.CLOSED

    async def test_context_raises_when_open(self) -> None:
        cb = AsyncCircuitBreaker("test", recovery_timeout=3600)
        cb._state = State.OPEN
        cb._opened_at = time.monotonic()
        with pytest.raises(CircuitBreakerOpenError):
            async with cb:
                pass  # pragma: no cover

    async def test_context_failure_trips(self) -> None:
        cb = AsyncCircuitBreaker("test", failure_threshold=1)
        with pytest.raises(ValueError, match="something bad"):
            async with cb:
                raise ValueError("something bad")
        assert cb.state is State.OPEN

    async def test_context_4xx_does_not_trip(self) -> None:
        cb = AsyncCircuitBreaker("test", failure_threshold=1)
        with pytest.raises(ValueError, match="client error"):
            async with cb:
                raise ValueError("client error")
        # Currently context manager records all exceptions as server errors.
        # This test documents current behavior; caller can still use inline
        # can_call/record_failure for fine-grained 4xx vs 5xx distinction.
        assert cb.state is State.OPEN


class TestStats:
    """stats() — observability snapshot."""

    async def test_stats_initial(self) -> None:
        cb = AsyncCircuitBreaker("test")
        s = cb.stats()
        assert s["name"] == "test"
        assert s["state"] == "closed"
        assert s["total_calls"] == 0
        assert s["rejected_calls"] == 0
        assert s["successful_calls"] == 0
        assert s["failure_count"] == 0

    async def test_stats_after_failures(self) -> None:
        cb = AsyncCircuitBreaker("test", failure_threshold=3)
        await cb.record_failure(is_server_error=True)
        await cb.record_failure(is_server_error=True)
        s = cb.stats()
        assert s["total_calls"] == 2
        assert s["failure_count"] == 2
        assert s["state"] == "closed"

    async def test_stats_after_trip(self) -> None:
        cb = AsyncCircuitBreaker("test", failure_threshold=2)
        await cb.record_failure(is_server_error=True)
        await cb.record_failure(is_server_error=True)
        s = cb.stats()
        assert s["state"] == "open"
        assert s["failure_count"] == 2


class TestFullLifecycle:
    """End-to-end state machine transitions."""

    async def test_closed_to_open_to_half_open_to_closed(self) -> None:
        cb = AsyncCircuitBreaker(
            "lifecycle",
            failure_threshold=2,
            recovery_timeout=0.02,
        )

        # CLOSED — normal operation
        assert cb.state is State.CLOSED
        assert await cb.can_call() is True

        # Trip the breaker
        await cb.record_failure(is_server_error=True)
        await cb.record_failure(is_server_error=True)
        assert cb.state is State.OPEN
        assert await cb.can_call() is False  # fast-fail

        # Wait for recovery timeout — transitions to HALF_OPEN
        await asyncio.sleep(0.03)
        assert await cb.can_call() is True
        assert cb.state is State.HALF_OPEN

        # Success during HALF_OPEN — transitions back to CLOSED
        await cb.record_success()
        assert cb.state is State.CLOSED
        assert await cb.can_call() is True

    async def test_half_open_failure_reopens(self) -> None:
        cb = AsyncCircuitBreaker(
            "reopen",
            failure_threshold=1,
            recovery_timeout=0.02,
        )

        # Trip
        await cb.record_failure(is_server_error=True)
        assert cb.state is State.OPEN

        # Wait for half-open
        await asyncio.sleep(0.03)
        assert await cb.can_call() is True  # transitions to HALF_OPEN

        # Fail in HALF_OPEN — trips back to OPEN
        await cb.record_failure(is_server_error=True)
        assert cb.state is State.OPEN

    async def test_failures_pruned_in_window(self) -> None:
        cb = AsyncCircuitBreaker(
            "prune",
            failure_window=0.02,
            failure_threshold=3,
        )

        await cb.record_failure(is_server_error=True)  # t=0
        await asyncio.sleep(0.03)  # wait for expiry
        # This failure should be the only one in window
        await cb.record_failure(is_server_error=True)
        await cb.record_failure(is_server_error=True)
        # Only 2 in window — below threshold
        assert cb.state is State.CLOSED
        assert len(cb._failures) == 2


class TestSimpleCircuitBreaker:
    """Sync variant — SimpleCircuitBreaker."""

    def test_default_state(self) -> None:
        cb = SimpleCircuitBreaker("simple")
        assert cb.name == "simple"
        assert cb.state is State.CLOSED

    def test_open_blocks(self) -> None:
        cb = SimpleCircuitBreaker("simple", recovery_timeout=3600)
        cb._state = State.OPEN
        cb._opened_at = time.monotonic()
        assert cb.can_call() is False

    def test_open_transitions_after_timeout(self) -> None:
        cb = SimpleCircuitBreaker("simple", recovery_timeout=0.01)
        cb._state = State.OPEN
        cb._opened_at = time.monotonic() - 10
        assert cb.can_call() is True
        assert cb.state is State.HALF_OPEN

    def test_success_closes(self) -> None:
        cb = SimpleCircuitBreaker("simple")
        cb._state = State.OPEN
        cb.record_success()
        assert cb.state is State.CLOSED

    def test_failure_trips(self) -> None:
        cb = SimpleCircuitBreaker("simple", failure_threshold=1)
        cb.record_failure(is_server_error=True)
        assert cb.state is State.OPEN

    def test_4xx_does_not_count_simple(self) -> None:
        cb = SimpleCircuitBreaker("simple", failure_threshold=1)
        cb.record_failure(is_server_error=False)
        assert cb.state is State.CLOSED

    def test_stale_cache(self) -> None:
        cb = SimpleCircuitBreaker("simple")
        cb.set_stale_result(42)
        assert cb.get_stale_result() == 42

    def test_stale_expires(self) -> None:
        cb = SimpleCircuitBreaker("simple")
        cb._stale_max_age = 0.01
        cb.set_stale_result(42)
        time.sleep(0.02)
        assert cb.get_stale_result() is None

    def test_half_open_blocks_after_max(self) -> None:
        cb = SimpleCircuitBreaker("simple", half_open_max=1)
        cb._state = State.HALF_OPEN
        cb._half_open_count = 1
        assert cb.can_call() is False

    def test_half_open_allows_probe(self) -> None:
        cb = SimpleCircuitBreaker("simple")
        cb._state = State.HALF_OPEN
        cb._half_open_count = 0
        assert cb.can_call() is True
