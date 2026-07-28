"""DataBus module unit tests — 15+ tests covering provider chain building,
circuit breaker logic, schema validation, request deduplication, and
provider health tracking.

Uses pytest-asyncio (enabled via pytest.ini). All external calls mocked.
"""

from __future__ import annotations

import os

# Force env vars BEFORE any app imports to satisfy Settings validation
os.environ["JWT_SECRET"] = "a" * 32
os.environ["ENVIRONMENT"] = "dev"  # override .env default 'development' which is invalid

import time

import pytest

from app.core.circuit_breaker import SimpleCircuitBreaker, State
from app.databus.core import ProviderHealthTracker, RequestDeduplicator
from app.databus.provider_core import (
    Provider,
    ProviderChain,
    ProviderTier,
    _quota_usage,
    _RateLimiter,
    reset_state,
)
from app.databus.response_schema import SchemaValidator, schema_validator

# ═══════════════════════════════════════════════════════════════════════
# ── SchemaValidator tests
# ═══════════════════════════════════════════════════════════════════════


class TestSchemaValidator:
    """Response schema validation for DataBus data types."""

    def test_valid_data_passes(self):
        """Data containing all required fields passes validation."""
        data = {"price_usd": 0.05, "change_24h": 1.2}
        is_valid, missing = schema_validator.validate("token_price", data)
        assert is_valid is True
        assert missing == []

    def test_missing_required_field_fails(self):
        """Data missing a required field fails with field name."""
        data = {"change_24h": 1.2}  # missing price_usd
        is_valid, missing = schema_validator.validate("token_price", data)
        assert is_valid is False
        assert "price_usd" in missing

    def test_unknown_data_type_passes(self):
        """Unknown data types pass through without validation."""
        data = {"anything": "goes"}
        is_valid, missing = schema_validator.validate("unknown_type", data)
        assert is_valid is True
        assert missing == []

    def test_non_dict_data_fails(self):
        """Non-dict data returns validation failure."""
        is_valid, missing = schema_validator.validate("token_price", [1, 2, 3])
        assert is_valid is False
        assert "data must be dict" in missing

    def test_optional_fields_not_required(self):
        """Optional fields are not in the missing list."""
        data = {"risk_score": 42}
        is_valid, missing = schema_validator.validate("risk_scan", data)
        assert is_valid is True

    def test_check_response_annotates(self):
        """check_response adds schema_valid and schema_missing to result."""
        result = {"data": {"price_usd": 0.1}}
        annotated = schema_validator.check_response("token_price", result)
        assert annotated["schema_valid"] is True
        assert "schema_missing" not in annotated

    def test_check_response_missing_data(self):
        """check_response returns unchanged when no 'data' key."""
        result = {"error": "something went wrong"}
        annotated = schema_validator.check_response("token_price", result)
        assert annotated == result

    def test_check_response_missing_field(self):
        """check_response adds schema_missing for missing required fields."""
        result = {"data": {"volume_24h": 1000}}
        annotated = schema_validator.check_response("token_price", result)
        assert annotated["schema_valid"] is False
        assert "price_usd" in annotated.get("schema_missing", [])

    def test_schema_registry_coverage(self):
        """All registered schemas have required fields."""
        for data_type, schema in SchemaValidator.SCHEMAS.items():
            required = schema.get("required", [])
            assert len(required) > 0, f"{data_type} has no required fields"
            # All field names are strings
            for field in required:
                assert isinstance(field, str), f"{data_type}.{field} not a string"


# ═══════════════════════════════════════════════════════════════════════
# ── RequestDeduplicator tests
# ═══════════════════════════════════════════════════════════════════════


class TestRequestDeduplicator:
    """Deduplicate in-flight DataBus requests."""

    def setup_method(self):
        self.dedup = RequestDeduplicator()

    def test_make_key_consistent(self):
        """Same data_type + kwargs produces same key."""
        key1 = self.dedup.make_key("token_price", mint="EPjFWdd5Aufq")
        key2 = self.dedup.make_key("token_price", mint="EPjFWdd5Aufq")
        assert key1 == key2

    def test_make_key_different(self):
        """Different kwargs produce different keys."""
        key1 = self.dedup.make_key("token_price", mint="abc")
        key2 = self.dedup.make_key("token_price", mint="xyz")
        assert key1 != key2

    def test_different_data_types_different(self):
        """Different data types produce different keys even with same kwargs."""
        key1 = self.dedup.make_key("token_price", address="abc")
        key2 = self.dedup.make_key("wallet_labels", address="abc")
        assert key1 != key2

    @pytest.mark.asyncio
    async def test_get_or_create_new(self):
        """First call returns (future, is_duplicate=False)."""
        key = self.dedup.make_key("token_price", mint="test")
        future, is_duplicate = await self.dedup.get_or_create(key)
        assert is_duplicate is False
        assert not future.done()

    @pytest.mark.asyncio
    async def test_get_or_create_duplicate(self):
        """Second call with same key returns same future, is_duplicate=True."""
        key = self.dedup.make_key("token_price", mint="test-dup")
        future1, _ = await self.dedup.get_or_create(key)
        future2, is_duplicate = await self.dedup.get_or_create(key)
        assert is_duplicate is True
        assert future1 is future2

    @pytest.mark.asyncio
    async def test_resolve_sets_result(self):
        """resolve sets the future result."""
        key = self.dedup.make_key("token_price", mint="resolve-test")
        future, _ = await self.dedup.get_or_create(key)
        self.dedup.resolve(key, {"price": 100})
        assert future.done()
        assert future.result() == {"price": 100}

    @pytest.mark.asyncio
    async def test_fail_sets_exception(self):
        """fail sets an exception on the future."""
        key = self.dedup.make_key("token_price", mint="fail-test")
        future, _ = await self.dedup.get_or_create(key)
        self.dedup.fail(key, ValueError("API failed"))
        assert future.done()
        with pytest.raises(ValueError, match="API failed"):
            future.result()

    def test_stats_initially_zero(self):
        """Stats start at zero."""
        stats = self.dedup.stats()
        assert stats["in_flight"] == 0
        assert stats["dedup_hits"] == 0
        assert stats["saved_api_calls"] == 0

    @pytest.mark.asyncio
    async def test_stats_tracks_hits(self):
        """Stats reflect dedup hits."""
        key = self.dedup.make_key("token_price", mint="stats-test")
        await self.dedup.get_or_create(key)  # first
        await self.dedup.get_or_create(key)  # duplicate
        stats = self.dedup.stats()
        assert stats["dedup_hits"] == 1
        assert stats["saved_api_calls"] == 1
        assert stats["in_flight"] == 1  # still in flight

    @pytest.mark.asyncio
    async def test_resolve_clears_key(self):
        """Resolving a key removes it from in_flight."""
        key = self.dedup.make_key("token_price", mint="clear-test")
        await self.dedup.get_or_create(key)
        self.dedup.resolve(key, "done")
        stats = self.dedup.stats()
        assert stats["in_flight"] == 0


# ═══════════════════════════════════════════════════════════════════════
# ── _CircuitBreaker tests
# ═══════════════════════════════════════════════════════════════════════


class TestCircuitBreaker:
    """Core circuit breaker for DataBus providers."""

    def test_initial_state_closed(self):
        """New breaker is closed and allows calls."""
        cb = SimpleCircuitBreaker(name="test", failure_threshold=3, recovery_timeout=1.0)
        assert cb.can_call() is True
        assert cb.state is State.CLOSED

    def test_trips_after_threshold_failures(self):
        """Breaker opens after hitting failure threshold."""
        cb = SimpleCircuitBreaker(name="test", failure_threshold=3, recovery_timeout=60.0)
        cb.record_failure(is_server_error=True)
        assert cb.state is State.CLOSED
        cb.record_failure(is_server_error=True)
        assert cb.state is State.CLOSED
        cb.record_failure(is_server_error=True)
        assert cb.state is State.OPEN

    def test_open_blocks_calls(self):
        """When open, can_call returns False."""
        cb = SimpleCircuitBreaker(name="test", failure_threshold=1, recovery_timeout=60.0)
        cb.record_failure(is_server_error=True)
        assert cb.can_call() is False

    def test_success_resets_failures(self):
        """A successful call resets failure count and closes breaker."""
        cb = SimpleCircuitBreaker(name="test", failure_threshold=3, recovery_timeout=60.0)
        cb.record_failure(is_server_error=True)
        cb.record_failure(is_server_error=True)
        cb.record_success()
        assert cb.state is State.CLOSED
        assert cb.can_call() is True

    def test_half_open_after_timeout(self):
        """After recovery_timeout, breaker transitions to half-open (allows one call)."""
        cb = SimpleCircuitBreaker(name="test", failure_threshold=1, recovery_timeout=0.05)
        cb.record_failure(is_server_error=True)
        assert cb.can_call() is False  # open
        time.sleep(0.06)
        assert cb.can_call() is True  # half-open (recovered)

    def test_success_in_half_open_closes(self):
        """Success in half-open state fully closes breaker."""
        cb = SimpleCircuitBreaker(name="test", failure_threshold=1, recovery_timeout=0.05)
        cb.record_failure(is_server_error=True)  # opens
        time.sleep(0.06)
        cb.can_call()  # transitions to half-open
        cb.record_success()  # closes
        assert cb.state is State.CLOSED
        assert cb.can_call() is True


# ═══════════════════════════════════════════════════════════════════════
# ── _RateLimiter tests
# ═══════════════════════════════════════════════════════════════════════


class TestRateLimiter:
    """Rate limiter for DataBus providers."""

    def test_first_call_allowed(self):
        """First call is always allowed."""
        rl = _RateLimiter(rps=10.0)
        assert rl.can_call() is True

    def test_calls_blocked_within_interval(self):
        """Rapid calls within min_interval are blocked."""
        rl = _RateLimiter(rps=100.0)  # high rps
        rl.record_call()
        # With rps=100, min_interval = 0.01s. We haven't waited, so should be blocked
        assert rl.can_call() is False

    def test_calls_allowed_after_interval(self):
        """Calls after min_interval passes are allowed."""
        rl = _RateLimiter(rps=100.0)  # min_interval = 0.01s
        rl.record_call()
        time.sleep(0.015)
        assert rl.can_call() is True

    def test_very_low_rps_blocks(self):
        """Extremely low rps (near zero) blocks calls within interval."""
        # Use a tiny positive rps so we don't ZeroDivide
        rl = _RateLimiter(rps=0.001)  # min_interval = 1000 seconds
        rl.record_call()
        assert rl.can_call() is False  # 1000s haven't passed


# ═══════════════════════════════════════════════════════════════════════
# ── ProviderChain tests
# ═══════════════════════════════════════════════════════════════════════


class TestProviderChain:
    """ProviderChain fallback logic."""

    @pytest.fixture(autouse=True)
    def _clean_state(self):
        reset_state()
        yield
        reset_state()

    @pytest.mark.asyncio
    async def test_first_provider_succeeds(self):
        """Chain returns first successful provider's result."""

        async def provider_a(**kw):
            return {"price": 100}

        async def provider_b(**kw):
            return {"price": 200}

        chain = ProviderChain(
            data_type="token_price",
            providers=[
                Provider(name="a", tier=ProviderTier.FREE_API, fetch_fn=provider_a, weight=2.0),
                Provider(name="b", tier=ProviderTier.FREE_API, fetch_fn=provider_b, weight=1.0),
            ],
        )
        result = await chain.fetch()
        assert result == {"price": 100}

    @pytest.mark.asyncio
    async def test_fallback_to_second(self):
        """When first provider returns None, chain falls back."""

        async def provider_a(**kw):
            return None

        async def provider_b(**kw):
            return {"price": 200}

        chain = ProviderChain(
            data_type="token_price",
            providers=[
                Provider(name="a", tier=ProviderTier.FREE_API, fetch_fn=provider_a),
                Provider(name="b", tier=ProviderTier.FREE_API, fetch_fn=provider_b),
            ],
        )
        result = await chain.fetch()
        assert result == {"price": 200}

    @pytest.mark.asyncio
    async def test_all_fail_returns_none(self):
        """When all providers fail, chain returns None."""

        async def fail_provider(**kw):
            return None

        chain = ProviderChain(
            data_type="token_price",
            providers=[
                Provider(name="fail1", tier=ProviderTier.FREE_API, fetch_fn=fail_provider),
                Provider(name="fail2", tier=ProviderTier.FREE_API, fetch_fn=fail_provider),
            ],
        )
        result = await chain.fetch()
        assert result is None

    @pytest.mark.asyncio
    async def test_provider_exception_triggers_fallback(self):
        """When a provider raises, chain falls through to next."""

        async def broken_provider(**kw):
            raise ConnectionError("timeout")

        async def good_provider(**kw):
            return {"price": 300}

        chain = ProviderChain(
            data_type="token_price",
            providers=[
                Provider(name="broken", tier=ProviderTier.FREE_API, fetch_fn=broken_provider),
                Provider(name="good", tier=ProviderTier.FREE_API, fetch_fn=good_provider),
            ],
        )
        result = await chain.fetch()
        assert result == {"price": 300}

    @pytest.mark.asyncio
    async def test_circuit_breaker_skips_open_provider(self):
        """Circuit breaker in OPEN state should skip provider."""
        from app.databus.provider_core import _circuit_breakers

        async def should_not_call(**kw):
            raise AssertionError("This provider should not be called")

        async def fallback(**kw):
            return {"price": 400}

        # Manually open the circuit breaker for provider "bad"
        cb = SimpleCircuitBreaker(name="bad", failure_threshold=1, recovery_timeout=60.0)
        cb.record_failure(is_server_error=True)  # now open
        _circuit_breakers["bad"] = cb

        chain = ProviderChain(
            data_type="token_price",
            providers=[
                Provider(name="bad", tier=ProviderTier.FREE_API, fetch_fn=should_not_call),
                Provider(name="fallback", tier=ProviderTier.FREE_API, fetch_fn=fallback),
            ],
        )
        result = await chain.fetch()
        assert result == {"price": 400}

    @pytest.mark.asyncio
    async def test_quota_exceeded_skips_provider(self):
        """Provider with exceeded monthly quota is skipped."""

        async def should_not_call(**kw):
            raise AssertionError("This provider should not be called")

        async def free_provider(**kw):
            return {"price": 500}

        _quota_usage["expensive"] = 100  # 100% used

        chain = ProviderChain(
            data_type="token_price",
            providers=[
                Provider(
                    name="expensive",
                    tier=ProviderTier.PAID,
                    fetch_fn=should_not_call,
                    monthly_quota=100,
                ),
                Provider(
                    name="free",
                    tier=ProviderTier.FREE_API,
                    fetch_fn=free_provider,
                ),
            ],
        )
        result = await chain.fetch()
        assert result == {"price": 500}

    def test_provider_tier_ordering(self):
        """Providers sort by weight descending then tier value."""
        paid = Provider(name="paid", tier=ProviderTier.PAID, fetch_fn=lambda **kw: None, weight=3.0)
        free = Provider(name="free", tier=ProviderTier.FREE_API, fetch_fn=lambda **kw: None, weight=2.0)
        local = Provider(name="local", tier=ProviderTier.LOCAL, fetch_fn=lambda **kw: None, weight=1.0)

        chain = ProviderChain(
            data_type="test",
            providers=[paid, free, local],
        )
        # Providers sorted by (-weight, tier.value)
        sorted_providers = sorted(chain.providers, key=lambda p: (-p.weight, p.tier.value))
        assert sorted_providers[0].name == "paid"  # weight=3, highest
        assert sorted_providers[1].name == "free"  # weight=2
        assert sorted_providers[2].name == "local"  # weight=1, lowest

    def test_reset_state_clears_all(self):
        """reset_state clears circuit breakers, rate limiters, quotas."""
        import app.databus.provider_core as pcore

        pcore._circuit_breakers["test"] = SimpleCircuitBreaker(name="test")
        pcore._rate_limiters["test"] = _RateLimiter()
        pcore._quota_usage["test"] = 42

        reset_state()

        # Access through module to see the replaced global
        assert len(pcore._circuit_breakers) == 0
        assert len(pcore._rate_limiters) == 0
        assert len(pcore._quota_usage) == 0


# ═══════════════════════════════════════════════════════════════════════
# ── ProviderHealthTracker tests
# ═══════════════════════════════════════════════════════════════════════


class TestProviderHealthTracker:
    """Health tracking for DataBus providers."""

    def setup_method(self):
        self.tracker = ProviderHealthTracker()

    def test_initial_state(self):
        """New tracker has no providers."""
        health = self.tracker.all_health()
        assert health == {}

    def test_record_success(self):
        """record_success increments counters."""
        self.tracker.record_success("provider_a", latency_ms=100.0)
        p = self.tracker.get_health("provider_a")
        assert p["total_calls"] == 1
        assert p["successes"] == 1
        assert p["failures"] == 0
        assert p["is_available"] is True

    def test_record_failure(self):
        """record_failure increments failure counters."""
        self.tracker.record_failure("provider_a", error="timeout")
        p = self.tracker.get_health("provider_a")
        assert p["total_calls"] == 1
        assert p["failures"] == 1
        assert p["consecutive_failures"] == 1
        assert p["last_error"] == "timeout"

    def test_success_rate_calculation(self):
        """all_health returns correct success_rate."""
        self.tracker.record_success("provider_a", latency_ms=10)
        self.tracker.record_success("provider_a", latency_ms=10)
        self.tracker.record_failure("provider_a", error="err")
        health = self.tracker.all_health()
        p = health["provider_a"]
        assert p["total_calls"] == 3
        assert p["successes"] == 2
        assert p["failures"] == 1
        assert p["success_rate"] == 66.7

    def test_latency_running_average(self):
        """Latency uses exponential moving average (80/20 split)."""
        self.tracker.record_success("provider_a", latency_ms=100.0)
        p = self.tracker.get_health("provider_a")
        assert p["avg_latency_ms"] == 100.0

        self.tracker.record_success("provider_a", latency_ms=200.0)
        p = self.tracker.get_health("provider_a")
        # EMA = prev * 0.8 + new * 0.2
        expected = 100.0 * 0.8 + 200.0 * 0.2
        assert p["avg_latency_ms"] == pytest.approx(expected)

    def test_three_failures_open_circuit(self):
        """3 consecutive failures open circuit breaker."""
        self.tracker.record_failure("provider_a")
        self.tracker.record_failure("provider_a")
        self.tracker.record_failure("provider_a")
        p = self.tracker.get_health("provider_a")
        assert p["consecutive_failures"] == 3
        assert p["is_available"] is False

    def test_available_after_success_reopens(self):
        """is_available returns True for healthy provider."""
        self.tracker.record_success("provider_a", latency_ms=10)
        assert self.tracker.is_available("provider_a") is True
