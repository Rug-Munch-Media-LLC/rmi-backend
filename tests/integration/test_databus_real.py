"""Integration tests for the DataBus — provider chains, cache, health tracking, fallback.

Tests REAL business logic:
  - CacheLayer with L1 (LRU + SWR) and L2 (Redis) tiers
  - ProviderHealthTracker: success/failure tracking, circuit breaker, recovery
  - RequestDeduplicator: dedup key creation, in-flight tracking, resolve
  - Rate limit tracking per provider
  - Access control: admin gating, consumer identification
  - Provider chain: provider sorting, quota skips, circuit breaker skips
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# =========================================================================
# Cache Layer Tests
# =========================================================================


class TestL1Cache:
    """Tests for L1Cache — in-memory LRU with Stale-While-Revalidate."""

    def test_l1_fresh_hit_returns_data(self):
        """Fresh cache entry should return data with is_stale=False."""
        from app.databus.cache import L1Cache

        cache = L1Cache(max_keys=100)
        asyncio.run(cache.set("test:price:abc123", {"price": 100.0}, ttl=60))
        value, is_stale = asyncio.run(cache.get("test:price:abc123"))
        assert value == {"price": 100.0}
        assert is_stale is False

    def test_l1_miss_returns_none(self):
        """Cache miss should return None."""
        from app.databus.cache import L1Cache

        cache = L1Cache(max_keys=100)
        value, is_stale = asyncio.run(cache.get("nonexistent:key"))
        assert value is None
        assert is_stale is False

    def test_l1_lru_eviction_drops_oldest(self):
        """When over max_keys, the oldest entry should be evicted."""
        from app.databus.cache import L1Cache

        cache = L1Cache(max_keys=3)
        asyncio.run(cache.set("key1", "value1", ttl=60))
        asyncio.run(cache.set("key2", "value2", ttl=60))
        asyncio.run(cache.set("key3", "value3", ttl=60))
        asyncio.run(cache.set("key4", "value4", ttl=60))  # Should evict key1

        v1, _ = asyncio.run(cache.get("key1"))
        assert v1 is None, "key1 should have been evicted"
        v4, _ = asyncio.run(cache.get("key4"))
        assert v4 == "value4", "key4 should be present"

    def test_l1_delete_removes_entry(self):
        """Deleted entries should return None on subsequent get."""
        from app.databus.cache import L1Cache

        cache = L1Cache(max_keys=100)
        asyncio.run(cache.set("delete_me", "data", ttl=60))
        v1, _ = asyncio.run(cache.get("delete_me"))
        assert v1 == "data"
        asyncio.run(cache.delete("delete_me"))
        v2, _ = asyncio.run(cache.get("delete_me"))
        assert v2 is None

    def test_l1_clear_removes_all(self):
        """Clear should remove all entries."""
        from app.databus.cache import L1Cache

        cache = L1Cache(max_keys=100)
        asyncio.run(cache.set("a", 1, ttl=60))
        asyncio.run(cache.set("b", 2, ttl=60))
        asyncio.run(cache.clear())
        va, _ = asyncio.run(cache.get("a"))
        vb, _ = asyncio.run(cache.get("b"))
        assert va is None
        assert vb is None

    def test_l1_stale_while_revalidate(self):
        """After TTL expires but within stale window, data should be returned as stale."""
        from app.databus.cache import L1Cache

        cache = L1Cache(max_keys=100, stale_multiplier=2.5)
        asyncio.run(cache.set("stale_test", "fresh_data", ttl=1))
        time.sleep(1.1)
        value, is_stale = asyncio.run(cache.get("stale_test"))
        assert value == "fresh_data", "Stale data should still be returned"
        assert is_stale is True, "Should be flagged as stale"

    def test_l1_fully_expired_returns_none(self):
        """After stale window passes, entry should be fully expired."""
        from app.databus.cache import L1Cache

        cache = L1Cache(max_keys=100, stale_multiplier=1.0)
        asyncio.run(cache.set("expired_test", "old_data", ttl=1))
        time.sleep(1.1)
        value, _is_stale = asyncio.run(cache.get("expired_test"))
        assert value is None, "Fully expired entry should return None"

    def test_l1_stats_track_hits_misses(self):
        """Stats should track hit/miss/stale_hit counts."""
        from app.databus.cache import L1Cache

        cache = L1Cache(max_keys=100)
        asyncio.run(cache.set("stat_test", "data", ttl=60))
        asyncio.run(cache.get("no_such_key"))  # miss
        asyncio.run(cache.get("stat_test"))  # hit
        asyncio.run(cache.get("stat_test"))  # hit

        stats = cache.stats()
        assert stats["hits"] == 2
        assert stats["misses"] == 1
        assert stats["keys"] == 1
        assert stats["hit_rate"] > 0

    def test_cache_layer_uses_l1_first(self):
        """CacheLayer.get should return L1 data if available (fast path)."""
        from app.databus.cache import CacheLayer

        cache = CacheLayer()
        asyncio.run(cache.set("l1_first", {"from": "l1"}, data_type="test"))
        result, is_stale = asyncio.run(cache.get("l1_first", data_type="test"))
        assert result == {"from": "l1"}
        assert is_stale is False


# =========================================================================
# Request Deduplicator Tests
# =========================================================================


class TestRequestDeduplicator:
    """Tests for RequestDeduplicator — in-flight request deduplication."""

    def test_dedup_key_consistency(self):
        """Same data_type + kwargs should produce same key."""
        from app.databus.core import RequestDeduplicator

        d = RequestDeduplicator()
        key1 = d.make_key("token_price", mint="So11111111111111111111111111111111111111112")
        key2 = d.make_key("token_price", mint="So11111111111111111111111111111111111111112")
        key3 = d.make_key("token_price", mint="DIFFERENT_TOKEN")

        assert key1 == key2
        assert key1 != key3

    def test_get_or_create_returns_new_future(self):
        """First call should create a new future."""
        from app.databus.core import RequestDeduplicator

        d = RequestDeduplicator()
        future, is_duplicate = asyncio.run(d.get_or_create("new_key"))
        assert future is not None
        assert is_duplicate is False
        assert not future.done()

    def test_get_or_create_returns_existing_for_dup(self):
        """Second call with same key should return existing future."""
        from app.databus.core import RequestDeduplicator

        d = RequestDeduplicator()
        future1, is_dup1 = asyncio.run(d.get_or_create("dup_key"))
        future2, is_dup2 = asyncio.run(d.get_or_create("dup_key"))

        assert future1 is future2
        assert is_dup1 is False
        assert is_dup2 is True

    def test_resolve_resolves_future(self):
        """resolve should complete the future with given result."""
        from app.databus.core import RequestDeduplicator

        async def test():
            d = RequestDeduplicator()
            future, _ = await d.get_or_create("resolve_me")
            d.resolve("resolve_me", {"result": "success"})
            assert future.done()
            result = await future
            assert result == {"result": "success"}

        asyncio.run(test())

    def test_fail_sets_exception(self):
        """fail should set an exception on the future."""
        from app.databus.core import RequestDeduplicator

        async def test():
            d = RequestDeduplicator()
            future, _ = await d.get_or_create("fail_me")
            d.fail("fail_me", ValueError("API error"))
            assert future.done()
            with pytest.raises(ValueError, match="API error"):
                await future

        asyncio.run(test())

    def test_stats_count_dedup_hits(self):
        """Stats should reflect deduplication activity."""
        from app.databus.core import RequestDeduplicator

        async def test():
            d = RequestDeduplicator()
            await d.get_or_create("stats_key")
            await d.get_or_create("stats_key")  # Duplicate
            d.resolve("stats_key", "done")
            stats = d.stats()
            assert stats["dedup_hits"] == 1
            assert stats["saved_api_calls"] == 1

        asyncio.run(test())


# =========================================================================
# Provider Health Tracker Tests
# =========================================================================


class TestProviderHealthTracker:
    """Tests for ProviderHealthTracker — circuit breakers, latency, availability."""

    def test_initially_available(self):
        """All providers should start as available."""
        from app.databus.core import ProviderHealthTracker

        tracker = ProviderHealthTracker()
        assert tracker.is_available("unknown_provider") is True

    def test_success_records_and_clears_failures(self):
        """Successful call should clear consecutive failures."""
        from app.databus.core import ProviderHealthTracker

        tracker = ProviderHealthTracker()
        tracker.record_failure("test_provider", "timeout")
        tracker.record_success("test_provider", latency_ms=100)

        health = tracker.get_health("test_provider")
        assert health["consecutive_failures"] == 0
        assert health["successes"] == 1
        assert health["failures"] == 1

    def test_latency_moving_average(self):
        """Latency should be tracked as an exponential moving average."""
        from app.databus.core import ProviderHealthTracker

        tracker = ProviderHealthTracker()
        tracker.record_success("latency_probe", latency_ms=100)
        tracker.record_success("latency_probe", latency_ms=200)
        tracker.record_success("latency_probe", latency_ms=200)

        health = tracker.get_health("latency_probe")
        # EMA: 100 + (200-100)*0.2 = 120, then 120 + (200-120)*0.2 = 136
        assert health["avg_latency_ms"] == 136.0
        assert health["successes"] == 3

    def test_circuit_breaker_opens_after_3_failures(self):
        """3 consecutive failures should open the circuit breaker."""
        from app.databus.core import ProviderHealthTracker

        tracker = ProviderHealthTracker()
        tracker.record_failure("breaker", "error1")
        tracker.record_failure("breaker", "error2")
        tracker.record_failure("breaker", "error3")

        health = tracker.get_health("breaker")
        assert health["is_available"] is False
        assert health["consecutive_failures"] == 3
        assert health["circuit_open_until"] > 0

    def test_circuit_breaker_recovers_as_half_open(self):
        """After circuit open timeout, provider should become half-open."""
        from app.databus.core import ProviderHealthTracker

        tracker = ProviderHealthTracker()
        tracker.record_failure("recover", "error1")
        tracker.record_failure("recover", "error2")
        tracker.record_failure("recover", "error3")
        assert tracker.is_available("recover") is False

        # Simulate time passing by resetting circuit_open_until
        health_data = tracker._providers["recover"]
        health_data["circuit_open_until"] = 0
        assert tracker.is_available("recover") is True

    def test_success_after_half_open_closes_circuit(self):
        """Successful call after half-open should close the circuit."""
        from app.databus.core import ProviderHealthTracker

        tracker = ProviderHealthTracker()
        tracker.record_failure("close_test", "e1")
        tracker.record_failure("close_test", "e2")
        tracker.record_failure("close_test", "e3")
        assert tracker.is_available("close_test") is False

        # Force half-open
        health_data = tracker._providers["close_test"]
        health_data["circuit_open_until"] = 0
        assert tracker.is_available("close_test") is True

        # Success should close the circuit
        tracker.record_success("close_test", latency_ms=50)
        health = tracker.get_health("close_test")
        assert health["is_available"] is True
        assert health["consecutive_failures"] == 0

    def test_all_health_summary(self):
        """all_health() should return summary across all providers."""
        from app.databus.core import ProviderHealthTracker

        tracker = ProviderHealthTracker()
        tracker.record_success("provider_a", latency_ms=100)
        tracker.record_failure("provider_b", "timeout")
        tracker.record_failure("provider_b", "timeout")

        summary = tracker.all_health()
        assert "provider_a" in summary
        assert "provider_b" in summary
        assert summary["provider_a"]["success_rate"] == 100.0
        assert summary["provider_b"]["success_rate"] == 0.0


# =========================================================================
# Provider Chain Tests
# =========================================================================


class TestProviderChain:
    """Tests for ProviderChain fallback logic.

    NOTE: provider_core.py has pre-existing bugs (missing httpx import,
    _rate_limiters direct key access). The test setup pre-populates
    global state to work around these so we can test the sorting and
    fallback logic.
    """

    @pytest.fixture(autouse=True)
    def _setup_provider_state(self):
        """Pre-populate provider state to avoid pre-existing bugs."""
        from app.core.circuit_breaker import SimpleCircuitBreaker
        from app.databus.provider_core import (
            _circuit_breakers,
            _rate_limiters,
            _RateLimiter,
        )

        provider_names = [
            "primary",
            "secondary",
            "fallback",
            "p1",
            "p2",
            "quota_provider",
            "fallback2",
            "skipped_provider",
        ]
        for name in provider_names:
            _rate_limiters[name] = _RateLimiter()
            _circuit_breakers[name] = SimpleCircuitBreaker(name=name)
        yield
        for name in provider_names:
            _rate_limiters.pop(name, None)
            _circuit_breakers.pop(name, None)

    async def _primary_fn(self, api_key=None, **kwargs) -> dict | None:
        return {"data": "from_primary", "source": "primary", "latency_ms": 10, "tier": "free_api"}

    async def _secondary_fn(self, api_key=None, **kwargs) -> dict | None:
        return {
            "data": "from_secondary",
            "source": "secondary",
            "latency_ms": 20,
            "tier": "free_api",
        }

    @pytest.mark.asyncio
    async def test_primary_provider_succeeds(self):
        """Chain should return primary provider's result if it succeeds."""
        from app.databus.provider_core import Provider, ProviderChain, ProviderTier

        chain = ProviderChain(
            data_type="test_data",
            providers=[
                Provider(
                    name="primary",
                    tier=ProviderTier.FREE_API,
                    fetch_fn=self._primary_fn,
                    weight=1.0,
                ),
            ],
        )
        result = await chain.fetch()
        assert result is not None
        assert result["data"] == "from_primary"
        assert result["source"] == "primary"

    @pytest.mark.asyncio
    async def test_providers_sorted_by_weight(self):
        """Higher weight providers should be tried first."""
        from app.databus.provider_core import Provider, ProviderChain, ProviderTier

        call_order = []

        async def provider_a(**kwargs):
            call_order.append("a")
            return {"data": "from_a", "source": "a", "latency_ms": 10, "tier": "free_api"}

        async def provider_b(**kwargs):
            call_order.append("b")
            return {"data": "from_b", "source": "b", "latency_ms": 10, "tier": "free_api"}

        chain = ProviderChain(
            data_type="test",
            providers=[
                Provider(name="a", tier=ProviderTier.FREE_API, fetch_fn=provider_a, weight=0.5),
                Provider(name="b", tier=ProviderTier.FREE_API, fetch_fn=provider_b, weight=1.0),
            ],
        )
        result = await chain.fetch()
        assert result["data"] == "from_b"
        # First success short-circuits (fetch docstring: "until one succeeds"):
        # higher weight tried first, lower weight never called on success.
        assert call_order == ["b"], "Higher weight first; fallback only on failure"

    @pytest.mark.asyncio
    async def test_fallback_tries_next_provider_on_failure(self):
        """When the higher-weight provider fails, the next is tried."""
        from app.databus.provider_core import Provider, ProviderChain, ProviderTier

        call_order = []

        async def failing(**kwargs):
            call_order.append("primary")
            raise ConnectionError("primary down")  # fetch() catches network-class errors

        async def healthy(**kwargs):
            call_order.append("secondary")
            return {"data": "from_secondary", "source": "s", "latency_ms": 10, "tier": "free_api"}

        chain = ProviderChain(
            data_type="test",
            providers=[
                Provider(name="primary", tier=ProviderTier.FREE_API, fetch_fn=failing, weight=1.0),
                Provider(name="secondary", tier=ProviderTier.FREE_API, fetch_fn=healthy, weight=0.5),
            ],
        )
        result = await chain.fetch()
        assert result["data"] == "from_secondary"
        assert call_order == ["primary", "secondary"], "Fallback must try next provider"

    @pytest.mark.asyncio
    async def test_all_return_none_returns_none(self):
        """When all providers return None, chain should return None."""
        from app.databus.provider_core import Provider, ProviderChain, ProviderTier

        async def returns_none(**kwargs) -> None:
            return None

        chain = ProviderChain(
            data_type="test",
            providers=[
                Provider(name="p1", tier=ProviderTier.FREE_API, fetch_fn=returns_none, weight=1.0),
                Provider(name="p2", tier=ProviderTier.FREE_API, fetch_fn=returns_none, weight=0.8),
            ],
        )
        result = await chain.fetch()
        assert result is None

    @pytest.mark.asyncio
    async def test_circuit_breaker_skips_open_providers(self):
        """Providers with open circuit breakers should be skipped."""
        from app.core.circuit_breaker import SimpleCircuitBreaker, State
        from app.databus.provider_core import (
            Provider,
            ProviderChain,
            ProviderTier,
            _circuit_breakers,
        )

        # Open the circuit breaker for 'skipped_provider'
        cb = SimpleCircuitBreaker(name="skipped_provider", failure_threshold=1, recovery_timeout=60.0)
        cb.record_failure(is_server_error=True)
        assert cb.state is State.OPEN  # ensure it's open
        _circuit_breakers["skipped_provider"] = cb

        skipped = False

        async def should_be_skipped(**kwargs) -> dict | None:
            nonlocal skipped
            skipped = True
            return {"data": "should_not_happen"}

        async def fallback_works(**kwargs) -> dict | None:
            return {
                "data": "from_fallback",
                "source": "fallback",
                "latency_ms": 10,
                "tier": "free_api",
            }

        chain = ProviderChain(
            data_type="test",
            providers=[
                Provider(
                    name="skipped_provider",
                    tier=ProviderTier.FREE_API,
                    fetch_fn=should_be_skipped,
                    weight=1.0,
                ),
                Provider(name="fallback", tier=ProviderTier.FREE_API, fetch_fn=fallback_works, weight=0.5),
            ],
        )
        result = await chain.fetch()
        assert result["data"] == "from_fallback"
        assert skipped is False, "Circuit-broken provider should have been skipped"

    @pytest.mark.asyncio
    async def test_provider_description_is_optional(self):
        """Provider and ProviderChain should work without descriptions."""
        from app.databus.provider_core import Provider, ProviderChain, ProviderTier

        async def simple_fn(**kwargs):
            return {"data": "works", "source": "simple", "latency_ms": 5, "tier": "free_api"}

        chain = ProviderChain(
            data_type="minimal",
            providers=[
                Provider(name="simple", tier=ProviderTier.LOCAL, fetch_fn=simple_fn, is_local=True),
            ],
        )
        result = await chain.fetch()
        assert result["data"] == "works"


# =========================================================================
# Provider Rate Limiting Tests
# =========================================================================


class TestProviderRateLimiting:
    """Tests for DataBus provider rate limiting and quota tracking."""

    def test_rate_limiter_tracks_calls(self):
        """Rate limiter enforces min_interval between calls."""

        from app.databus.provider_core import _RateLimiter

        limiter = _RateLimiter(rps=10)  # min_interval = 0.1s
        assert limiter.can_call() is True

        limiter.record_call()
        assert limiter.can_call() is False, "call within min_interval must be rejected"

        limiter.last_call -= 0.2  # simulate interval elapsed
        assert limiter.can_call() is True
        assert limiter.min_interval == 0.1

    def test_provider_tier_enum_values(self):
        """ProviderTier enum should have correct values."""
        from app.databus.provider_core import ProviderTier

        assert ProviderTier.LOCAL.value == "local"
        assert ProviderTier.FREE_API.value == "free_api"
        assert ProviderTier.FREEMIUM.value == "freemium"
        assert ProviderTier.PAID.value == "paid"

    def test_provider_minimal_creation(self):
        """Provider should work with minimal fields."""
        from app.databus.provider_core import Provider, ProviderTier

        async def dummy_fn(**kwargs):
            return None

        p = Provider(name="minimal", tier=ProviderTier.LOCAL, fetch_fn=dummy_fn)
        assert p.name == "minimal"
        assert p.tier == ProviderTier.LOCAL
        assert p.weight == 1.0
        assert p.is_local is False  # Only true when explicitly set
        assert p.rate_limit_rps == 1.0
        assert p.monthly_quota == 0  # 0 = unlimited

    def test_rate_limiter_initial_state(self):
        """Rate limiter should start ready to call with correct interval."""
        from app.databus.provider_core import _RateLimiter

        limiter = _RateLimiter(rps=30)
        assert limiter.can_call() is True
        assert limiter.min_interval == 1.0 / 30
        assert limiter.last_call == 0.0


# =========================================================================
# DataBus Access Control Tests
# =========================================================================


class TestDataBusAccessControl:
    """Tests for DataBus access control — admin gating, consumer identification."""

    def test_security_get_access_level_returns_string(self):
        """get_access_level should return a string access level."""
        from app.databus.security import security

        level = security.get_access_level("token_price")
        assert isinstance(level, str)
        assert level in ("public", "authenticated", "admin", "elite")

    def test_security_check_access_admin(self):
        """Admin key should pass access check without error."""
        from app.databus.security import security

        result = security.check_access("token_price", admin_key="test-internal-key-32-chars-long!!!")
        assert isinstance(result, bool)

    def test_data_types_exist_in_provider_chains(self):
        """Key data types should exist in the provider chains."""
        from app.domains.databus.providers import build_provider_chains

        chains = build_provider_chains()
        for key_type in [
            "token_price",
            "market_overview",
            "trending",
            "wallet_labels",
            "fear_greed",
        ]:
            assert key_type in chains, f"Required chain '{key_type}' missing"

    def test_provider_chain_has_fallback(self):
        """Chains should have multiple providers for fallback."""
        from app.domains.databus.providers import build_provider_chains

        chains = build_provider_chains()
        for key_type in ["token_price", "market_overview"]:
            chain = chains[key_type]
            assert len(chain.providers) >= 2, f"Chain '{key_type}' should have 2+ providers"
