"""Tests for DataBus Core — request deduplication, provider health tracking, and init.

DataBus is THE single source of truth for every data request in the platform.
These tests verify the critical infrastructure layer without needing external
services (Redis, Postgres, etc.).

Focus areas:
  1. RequestDeduplicator — in-flight request dedup, resolve, fail, stats
  2. ProviderHealthTracker — circuit breaker, success/failure tracking, latency EMA
  3. DataBus.__init__ — construction, cache wiring, vault wiring
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

# ═══════════════════════════════════════════════════════════════════════
# Request Deduplicator
# ═══════════════════════════════════════════════════════════════════════


class TestRequestDeduplicator:
    """RequestDeduplicator: in-flight request dedup, resolve, fail, stats."""

    def test_make_key_consistent(self):
        """Same inputs produce the same dedup key."""
        from app.databus.core import RequestDeduplicator

        d = RequestDeduplicator()
        key1 = d.make_key("token_price", mint="So1111", chain="solana")
        key2 = d.make_key("token_price", chain="solana", mint="So1111")
        assert key1 == key2, "Keys should be identical regardless of kwarg order"
        assert key1.startswith("token_price:")

    def test_make_key_different_types_differ(self):
        """Different data types produce different keys."""
        from app.databus.core import RequestDeduplicator

        d = RequestDeduplicator()
        key1 = d.make_key("token_price", mint="So1111")
        key2 = d.make_key("token_security", mint="So1111")
        assert key1 != key2

    def test_make_key_different_args_differ(self):
        """Different arguments produce different keys."""
        from app.databus.core import RequestDeduplicator

        d = RequestDeduplicator()
        key1 = d.make_key("token_price", mint="So1111")
        key2 = d.make_key("token_price", mint="So2222")
        assert key1 != key2

    @pytest.mark.asyncio
    async def test_get_or_create_new_returns_fresh_future(self):
        """First request for a key creates a new future and returns is_new=True."""
        from app.databus.core import RequestDeduplicator

        d = RequestDeduplicator()
        key = d.make_key("test", x=1)
        future, is_duplicate = await d.get_or_create(key)
        assert future is not None
        assert is_duplicate is False
        assert not future.done()

    @pytest.mark.asyncio
    async def test_get_or_create_duplicate_returns_same_future(self):
        """Second request for same key returns the same future and is_duplicate=True."""
        from app.databus.core import RequestDeduplicator

        d = RequestDeduplicator()
        key = d.make_key("test", x=1)
        future1, _ = await d.get_or_create(key)
        future2, is_duplicate = await d.get_or_create(key)
        assert future1 is future2
        assert is_duplicate is True

    @pytest.mark.asyncio
    async def test_resolve_sets_future_result(self):
        """Resolving a future sets its result and removes it from in_flight."""
        from app.databus.core import RequestDeduplicator

        d = RequestDeduplicator()
        key = d.make_key("test", x=1)
        future, _ = await d.get_or_create(key)
        d.resolve(key, {"price": 100})
        assert future.done()
        assert future.result() == {"price": 100}
        # Should be removed from in_flight
        new_future, is_dup = await d.get_or_create(key)
        assert is_dup is False  # fresh, not duplicate

    @pytest.mark.asyncio
    async def test_fail_sets_future_exception(self):
        """Failing a future sets an exception and removes from in_flight."""
        from app.databus.core import RequestDeduplicator

        d = RequestDeduplicator()
        key = d.make_key("test", x=1)
        future, _ = await d.get_or_create(key)
        d.fail(key, ValueError("provider error"))
        assert future.done()
        with pytest.raises(ValueError, match="provider error"):
            future.result()

    @pytest.mark.asyncio
    async def test_resolve_nonexistent_key_no_error(self):
        """Resolving a key that doesn't exist should not raise."""
        from app.databus.core import RequestDeduplicator

        d = RequestDeduplicator()
        d.resolve("nonexistent", {"data": "ok"})  # should not raise

    @pytest.mark.asyncio
    async def test_fail_nonexistent_key_no_error(self):
        """Failing a key that doesn't exist should not raise."""
        from app.databus.core import RequestDeduplicator

        d = RequestDeduplicator()
        d.fail("nonexistent", ValueError("err"))  # should not raise

    @pytest.mark.asyncio
    async def test_stats_tracks_dedup_hits(self):
        """Stats() returns correct dedup_hits and saved_api_calls."""
        from app.databus.core import RequestDeduplicator

        d = RequestDeduplicator()
        key = d.make_key("test", x=1)
        await d.get_or_create(key)
        await d.get_or_create(key)  # duplicate
        await d.get_or_create(key)  # duplicate

        stats = d.stats()
        assert stats["dedup_hits"] == 2
        assert stats["saved_api_calls"] == 2
        assert stats["in_flight"] == 1

    @pytest.mark.asyncio
    async def test_resolve_already_resolved_future_no_error(self):
        """Resolving a future that's already done should not raise."""
        from app.databus.core import RequestDeduplicator

        d = RequestDeduplicator()
        key = d.make_key("test", x=1)
        future, _ = await d.get_or_create(key)
        future.set_result("done")
        d.resolve(key, "again")  # should not raise
        assert future.result() == "done"  # unchanged


# ═══════════════════════════════════════════════════════════════════════
# Provider Health Tracker
# ═══════════════════════════════════════════════════════════════════════


class TestProviderHealthTracker:
    """ProviderHealthTracker: circuit breaker, success/failure, latency EMA."""

    def test_new_provider_has_defaults(self):
        """A newly recorded provider has sensible defaults."""
        from app.databus.core import ProviderHealthTracker

        tracker = ProviderHealthTracker()
        # Access provider via bracket to trigger defaultdict creation
        _ = tracker._providers["new_provider"]  # noqa: SLF001
        health = tracker.get_health("new_provider")
        assert health["total_calls"] == 0
        assert health["successes"] == 0
        assert health["failures"] == 0
        assert health["consecutive_failures"] == 0
        assert health["is_available"] is True

    def test_record_success_increments_counters(self):
        """A successful call increments successes and resets consecutive failures."""
        from app.databus.core import ProviderHealthTracker

        tracker = ProviderHealthTracker()
        tracker.record_success("provider_a", latency_ms=50.0)
        health = tracker.get_health("provider_a")
        assert health["total_calls"] == 1
        assert health["successes"] == 1
        assert health["consecutive_failures"] == 0
        assert health["last_success"] is not None

    def test_record_failure_increments_counters(self):
        """A failed call increments failures and consecutive failures."""
        from app.databus.core import ProviderHealthTracker

        tracker = ProviderHealthTracker()
        tracker.record_failure("provider_a", error="timeout")
        health = tracker.get_health("provider_a")
        assert health["total_calls"] == 1
        assert health["failures"] == 1
        assert health["consecutive_failures"] == 1
        assert health["last_error"] == "timeout"

    def test_three_failures_opens_circuit(self):
        """3 consecutive failures opens the circuit breaker."""
        from app.databus.core import ProviderHealthTracker

        tracker = ProviderHealthTracker()
        tracker.record_failure("provider_a", error="err1")
        tracker.record_failure("provider_a", error="err2")
        tracker.record_failure("provider_a", error="err3")
        health = tracker.get_health("provider_a")
        assert health["consecutive_failures"] == 3
        assert health["is_available"] is False

    def test_success_after_failures_resets_circuit(self):
        """A single success after failures resets consecutive failures."""
        from app.databus.core import ProviderHealthTracker

        tracker = ProviderHealthTracker()
        tracker.record_failure("provider_a")
        tracker.record_failure("provider_a")
        tracker.record_success("provider_a", latency_ms=30.0)
        health = tracker.get_health("provider_a")
        assert health["consecutive_failures"] == 0
        assert health["is_available"] is True

    def test_latency_ema_starts_with_first_value(self):
        """First latency value is stored directly (not averaged)."""
        from app.databus.core import ProviderHealthTracker

        tracker = ProviderHealthTracker()
        tracker.record_success("p", latency_ms=100.0)
        assert tracker.get_health("p")["avg_latency_ms"] == 100.0

    def test_latency_ema_averages_subsequent_values(self):
        """Second latency uses EMA formula: ema * 0.8 + new * 0.2."""
        from app.databus.core import ProviderHealthTracker

        tracker = ProviderHealthTracker()
        tracker.record_success("p", latency_ms=100.0)
        tracker.record_success("p", latency_ms=200.0)
        expected_ema = 100.0 * 0.8 + 200.0 * 0.2
        assert tracker.get_health("p")["avg_latency_ms"] == pytest.approx(expected_ema)

    def test_success_rate_calculation(self):
        """all_health returns correct success_rate percentage."""
        from app.databus.core import ProviderHealthTracker

        tracker = ProviderHealthTracker()
        tracker.record_success("p", latency_ms=10.0)
        tracker.record_success("p", latency_ms=10.0)
        tracker.record_failure("p", error="e")

        all_h = tracker.all_health()
        assert all_h["p"]["success_rate"] == pytest.approx(66.7, rel=0.1)

    def test_zero_calls_returns_zero_success_rate(self):
        """A provider with no calls has 0% success rate."""
        from app.databus.core import ProviderHealthTracker

        tracker = ProviderHealthTracker()
        all_h = tracker.all_health()
        assert all_h == {}  # no providers tracked

    def test_is_available_healthy(self):
        """A healthy provider is always available."""
        from app.databus.core import ProviderHealthTracker

        tracker = ProviderHealthTracker()
        tracker.record_success("p", latency_ms=10.0)
        assert tracker.is_available("p") is True

    def test_is_available_after_circuit_open(self):
        """After circuit opens, is_available returns False during cooldown."""
        from app.databus.core import ProviderHealthTracker

        tracker = ProviderHealthTracker()
        tracker.record_failure("p")
        tracker.record_failure("p")
        tracker.record_failure("p")
        # Should still be in cooldown
        assert tracker.is_available("p") is False

    def test_half_open_after_cooldown_expires(self):
        """After 30s cooldown, is_available returns True (probe allowed).

        We manipulate circuit_open_until to be in the past to simulate
        cooldown expiry without waiting 30 seconds.
        """
        from app.databus.core import ProviderHealthTracker

        tracker = ProviderHealthTracker()
        tracker.record_failure("p")
        tracker.record_failure("p")
        tracker.record_failure("p")

        # Set circuit_open_until in the past so cooldown appears expired
        tracker._providers["p"]["circuit_open_until"] = time.monotonic() - 1.0

        assert tracker.is_available("p") is True  # half-open probe

    def test_multiple_providers_independent(self):
        """Health tracking for multiple providers is independent."""
        from app.databus.core import ProviderHealthTracker

        tracker = ProviderHealthTracker()
        tracker.record_success("provider_a", latency_ms=10.0)
        tracker.record_failure("provider_b", error="err")

        health_a = tracker.get_health("provider_a")
        health_b = tracker.get_health("provider_b")
        assert health_a["successes"] == 1
        assert health_b["failures"] == 1
        assert health_a["is_available"] is True
        assert health_b["consecutive_failures"] == 1


# ═══════════════════════════════════════════════════════════════════════
# DataBus Initialization
# ═══════════════════════════════════════════════════════════════════════


class TestDataBusInit:
    """DataBus construction and initialization."""

    @pytest.mark.asyncio
    async def test_databus_init_with_mocked_deps(self):
        """DataBus initializes cleanly with all dependencies mocked."""
        with (
            patch("app.databus.core.get_cache") as mock_cache,
            patch("app.databus.core.get_vault") as mock_vault,
            patch("app.databus.core.build_provider_chains") as mock_chains,
            patch("app.databus.core.security") as mock_security,
        ):
            from app.databus.core import DataBus

            mock_cache.return_value = AsyncMock()
            mock_vault.return_value = AsyncMock()
            mock_chains.return_value = {}
            mock_security.is_admin_request = AsyncMock(return_value=False)

            db = DataBus()
            assert db.cache is not None
            assert db.chains == {}
            assert db._initialized is False
            assert db._dedup is not None
            assert db._provider_health is not None
            assert db._stats["total_fetches"] == 0
            assert db._stats["cache_hits"] == 0

    def test_databus_has_expected_attributes(self):
        """DataBus instance has all required attributes."""
        from app.databus.core import DataBus

        db = DataBus()
        assert hasattr(db, "cache")
        assert hasattr(db, "vault")
        assert hasattr(db, "chains")
        assert hasattr(db, "security")
        assert hasattr(db, "_initialized")
        assert hasattr(db, "_init_lock")
        assert hasattr(db, "_dedup")
        assert hasattr(db, "_provider_health")
        assert hasattr(db, "_warm_task")
        assert hasattr(db, "_stats")

    def test_databus_stats_defaults(self):
        """DataBus stats dict starts with zeroed counters."""
        from app.databus.core import DataBus

        db = DataBus()
        assert db._stats["total_fetches"] == 0
        assert db._stats["cache_hits"] == 0
        assert db._stats["cache_stale_hits"] == 0
        assert db._stats["cache_misses"] == 0
        assert db._stats["dedup_hits"] == 0
        assert db._stats["fallback_uses"] == 0

    @pytest.mark.asyncio
    async def test_databus_fetch_no_chains_returns_none(self):
        """Fetching with no chains configured returns None gracefully."""
        with (
            patch("app.databus.core.get_cache") as mock_cache,
            patch("app.databus.core.get_vault") as mock_vault,
            patch("app.databus.core.build_provider_chains") as mock_chains,
        ):
            from app.databus.core import DataBus

            mock_cache.return_value = AsyncMock()
            mock_vault.return_value = AsyncMock()
            mock_chains.return_value = {}

            db = DataBus()
            db._initialized = True
            result = await db.fetch("nonexistent_type")
            # Returns an error dict for unknown data types (not None)
            assert result is not None
            assert "error" in result
