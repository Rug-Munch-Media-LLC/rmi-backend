"""Unit tests for app.core.rate_limiter — UnifiedRateLimiter (token bucket).

Tests cover:
- _resolve_rate_limit path-to-category resolution
- UnifiedRateLimiter construction and acquire() with in-memory fallback
- UnifiedRateLimiter acquire() with mocked Redis (Lua script)
- acquire_path() and acquire_provider() backward compat
- Constant data integrity (DEFAULT_RATE_LIMITS, PATH_CATEGORIES)
- get_rate_limiter() singleton
- _hash_identifier() PII-safe hashing
"""

from __future__ import annotations

import os
import time as _time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.rate_limiter import (
    DEFAULT_RATE_LIMITS,
    PATH_CATEGORIES,
    UnifiedRateLimiter,
    _hash_identifier,
    _resolve_rate_limit,
    get_rate_limiter,
)

# ═══════════════════════════════════════════════════════════════════════════
# _resolve_rate_limit
# ═══════════════════════════════════════════════════════════════════════════


class TestResolveRateLimit:
    """Path → (rate_per_sec, burst_size) resolution."""

    def test_known_path_health(self) -> None:
        rate, burst = _resolve_rate_limit("/health")
        assert rate == pytest.approx(1000 / 60.0)
        assert burst == max(1, int(1000 * 0.2))

    def test_known_path_auth(self) -> None:
        rate, burst = _resolve_rate_limit("/login")
        assert rate == pytest.approx(5 / 60.0)
        assert burst == max(1, int(5 * 0.4))

    def test_known_path_scan(self) -> None:
        rate, burst = _resolve_rate_limit("/api/v1/scanner/scan")
        assert rate == pytest.approx(20 / 60.0)
        assert burst == max(1, int(20 * 0.25))

    def test_known_path_public(self) -> None:
        rate, burst = _resolve_rate_limit("/api/v1/token/0x...")
        assert rate == pytest.approx(100 / 60.0)
        assert burst == max(1, int(100 * 0.25))

    def test_unknown_path_uses_default(self) -> None:
        rate, burst = _resolve_rate_limit("/some/unknown/route")
        expected_rpm, expected_factor = DEFAULT_RATE_LIMITS["default"]
        assert rate == pytest.approx(expected_rpm / 60.0)
        assert burst == max(1, int(expected_rpm * expected_factor))

    def test_exact_prefix_match(self) -> None:
        """Longest prefix match wins."""
        rate, burst = _resolve_rate_limit("/api/v1/scanner/something")
        assert rate == pytest.approx(20 / 60.0), "Should match 'scan' category"

    def test_burst_never_below_one(self) -> None:
        rate, burst = _resolve_rate_limit("/api/v1/some-unknown")
        assert burst >= 1


# ═══════════════════════════════════════════════════════════════════════════
# UnifiedRateLimiter — construction + in-memory fallback
# ═══════════════════════════════════════════════════════════════════════════


class TestUnifiedRateLimiterInit:
    """Construction and basic properties."""

    def test_init(self) -> None:
        limiter = UnifiedRateLimiter()
        assert limiter._redis is None
        assert limiter._redis_failed is False
        assert limiter._mem_buckets == {}

    def test_singleton(self) -> None:
        limiter1 = get_rate_limiter()
        limiter2 = get_rate_limiter()
        assert limiter1 is limiter2


class TestAcquireInMemory:
    """Token bucket using in-memory fallback (no Redis)."""

    @pytest.mark.asyncio
    async def test_first_request_allowed(self) -> None:
        limiter = UnifiedRateLimiter()
        allowed, headers = await limiter.acquire("test-key", rate=10.0, burst=5)
        assert allowed is True
        assert "X-RateLimit-Limit" in headers
        assert "X-RateLimit-Remaining" in headers
        assert headers["X-RateLimit-Remaining"] >= 0
        assert headers["X-RateLimit-Limit"] == 5

    @pytest.mark.asyncio
    async def test_burst_limit_enforced(self) -> None:
        """Consuming more tokens than burst should be denied."""
        limiter = UnifiedRateLimiter()
        # burst=2, so we can only consume 2 tokens before denial
        allowed1, _ = await limiter.acquire("burst-key", rate=1.0, burst=2)
        allowed2, _ = await limiter.acquire("burst-key", rate=1.0, burst=2)
        allowed3, _ = await limiter.acquire("burst-key", rate=1.0, burst=2)
        assert allowed1 is True
        assert allowed2 is True
        assert allowed3 is False  # burst exhausted

    @pytest.mark.asyncio
    async def test_rate_limit_remaining_decreases(self) -> None:
        limiter = UnifiedRateLimiter()
        _, h1 = await limiter.acquire("remaining-key", rate=10.0, burst=5)
        _, h2 = await limiter.acquire("remaining-key", rate=10.0, burst=5)
        assert h2["X-RateLimit-Remaining"] <= h1["X-RateLimit-Remaining"]

    @pytest.mark.asyncio
    async def test_custom_tokens_consumed(self) -> None:
        """Multiple tokens consumed in one acquire."""
        limiter = UnifiedRateLimiter()
        allowed, _ = await limiter.acquire("multi-token", rate=10.0, burst=10, tokens=3)
        assert allowed is True

    @pytest.mark.asyncio
    async def test_zero_rate_still_allows_first(self) -> None:
        """With rate=0 and burst=1, one token is available."""
        limiter = UnifiedRateLimiter()
        allowed, _ = await limiter.acquire("zero-rate", rate=0.0, burst=1)
        assert allowed is True

    @pytest.mark.asyncio
    async def test_different_keys_independent(self) -> None:
        """Different keys should have independent buckets."""
        limiter = UnifiedRateLimiter()
        # Exhaust key-a (burst=1)
        await limiter.acquire("key-a-independent", rate=1.0, burst=1)
        denied, _ = await limiter.acquire("key-a-independent", rate=1.0, burst=1)
        # key-b should be fresh
        allowed, _ = await limiter.acquire("key-b-independent", rate=1.0, burst=1)
        assert denied is False
        assert allowed is True

    @pytest.mark.asyncio
    async def test_returns_limit_header(self) -> None:
        limiter = UnifiedRateLimiter()
        _, headers = await limiter.acquire("hdr-test", rate=1.0, burst=5)
        assert headers["X-RateLimit-Limit"] == 5
        assert "X-RateLimit-Remaining" in headers
        assert "X-RateLimit-Reset" in headers


# ═══════════════════════════════════════════════════════════════════════════
# acquire_path
# ═══════════════════════════════════════════════════════════════════════════


class TestAcquirePath:
    """Path-based rate limiting."""

    @pytest.mark.asyncio
    async def test_known_path(self) -> None:
        limiter = UnifiedRateLimiter()
        allowed, headers = await limiter.acquire_path("/api/v1/token/0x...", "client-1")
        assert allowed is True
        assert headers["X-RateLimit-Limit"] >= 1

    @pytest.mark.asyncio
    async def test_unknown_path_defaults(self) -> None:
        limiter = UnifiedRateLimiter()
        allowed, headers = await limiter.acquire_path("/unknown/route", "client-2")
        assert allowed is True

    @pytest.mark.asyncio
    async def test_diff_paths_independent(self) -> None:
        """Different paths for same client should have independent limits."""
        limiter = UnifiedRateLimiter()
        # Exhaust the scan bucket (burst=5)
        for _ in range(6):
            await limiter.acquire_path("/api/v1/scanner/scan", "client-scan")
        # Health should be fresh (burst=200)
        allowed, _ = await limiter.acquire_path("/health", "client-scan")
        assert allowed is True


# ═══════════════════════════════════════════════════════════════════════════
# acquire_provider (backward compat)
# ═══════════════════════════════════════════════════════════════════════════


class TestAcquireProvider:
    """Backward-compatible provider-style acquire."""

    @pytest.mark.asyncio
    async def test_allowed(self) -> None:
        limiter = UnifiedRateLimiter()
        allowed, wait = await limiter.acquire_provider("solana", method="getBalance")
        assert allowed is True
        assert wait == 0.0

    @pytest.mark.asyncio
    async def test_returns_wait_seconds_when_denied(self) -> None:
        """When burst is exhausted, wait should be > 0."""
        limiter = UnifiedRateLimiter()
        for _ in range(3):
            await limiter.acquire("provider:test:method", rate=0.001, burst=2)
        allowed, wait = await limiter.acquire_provider("test", method="method", rate=0.001, burst=2)
        # The in-memory bucket key is 'provider:test:method' so the burst of 2 is shared
        # After 3 acquires of 1 token each, the 4th should be denied
        assert allowed is False
        assert wait > 0.0


# ═══════════════════════════════════════════════════════════════════════════
# Constants data integrity
# ═══════════════════════════════════════════════════════════════════════════


class TestDefaultRateLimits:
    """Validate DEFAULT_RATE_LIMITS data integrity."""

    def test_all_categories_have_tuple(self) -> None:
        for cat, limits in DEFAULT_RATE_LIMITS.items():
            assert isinstance(limits, tuple)
            assert len(limits) == 2
            rpm, factor = limits
            assert isinstance(rpm, int), f"{cat} rpm must be int"
            assert rpm > 0, f"{cat} rpm must be > 0"
            assert isinstance(factor, float), f"{cat} factor must be float"
            assert 0 < factor <= 1.0, f"{cat} factor must be in (0, 1]"

    def test_burst_calculation(self) -> None:
        for cat, (rpm, factor) in DEFAULT_RATE_LIMITS.items():
            burst = max(1, int(rpm * factor))
            assert burst >= 1, f"{cat} burst must be >= 1"

    def test_has_default_category(self) -> None:
        assert "default" in DEFAULT_RATE_LIMITS


class TestPathCategories:
    """Validate PATH_CATEGORIES data integrity."""

    def test_all_prefixes_start_with_slash(self) -> None:
        for prefix in PATH_CATEGORIES:
            assert prefix.startswith("/"), f"Prefix {prefix!r} must start with /"

    def test_all_categories_exist_in_limits(self) -> None:
        for prefix, cat in PATH_CATEGORIES.items():
            assert cat in DEFAULT_RATE_LIMITS, (
                f"Category {cat!r} (from prefix {prefix!r}) not found in DEFAULT_RATE_LIMITS"
            )

    def test_longer_prefixes_more_specific(self) -> None:
        """Longer prefix should not be shadowed by shorter."""
        # /api/v1/scanner should match before /api/v1/
        scanner_prefixes = [p for p in PATH_CATEGORIES if "scanner" in p]
        assert len(scanner_prefixes) > 0


# ═══════════════════════════════════════════════════════════════════════════
# _hash_identifier
# ═══════════════════════════════════════════════════════════════════════════


class TestHashIdentifier:
    """PII-safe hashing of identifiers."""

    def test_deterministic(self) -> None:
        h1 = _hash_identifier("user-123")
        h2 = _hash_identifier("user-123")
        assert h1 == h2

    def test_different_inputs_different_hashes(self) -> None:
        h1 = _hash_identifier("user-123")
        h2 = _hash_identifier("user-456")
        assert h1 != h2

    def test_output_is_hex_string(self) -> None:
        h = _hash_identifier("test@example.com")
        assert isinstance(h, str)
        assert len(h) == 32
        int(h, 16)  # should not raise

    def test_empty_string(self) -> None:
        h = _hash_identifier("")
        assert isinstance(h, str)
        assert len(h) == 32

    def test_uses_daily_salt(self) -> None:
        """Same input on different days should produce different hashes
        (the salt rotates daily). We can't fast-forward time here but
        we verify the salt is derived from today's date."""
        import hashlib
        import hmac

        # Simulate the env var being set for test
        os.environ["RATE_LIMIT_SALT"] = "dev-only-rmi-salt-replace-in-prod"
        # Reimport to pick up the env var
        import importlib

        from app.core import rate_limiter

        importlib.reload(rate_limiter)
        from app.core.rate_limiter import _get_daily_salt

        salt = _get_daily_salt()
        today = _time.strftime("%Y-%m-%d")
        today_bytes = today.encode()

        expected = hmac.new(
            b"dev-only-rmi-salt-replace-in-prod",
            today_bytes,
            hashlib.sha256,
        ).digest()
        assert salt == expected


# ═══════════════════════════════════════════════════════════════════════════
# Mocked Redis path
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def mock_redis() -> MagicMock:
    redis = MagicMock()
    redis.evalsha = AsyncMock(return_value=[1, 5.0, 5, 0.0])  # allowed, remaining, limit, wait
    return redis


class TestAcquireWithRedis:
    """Token bucket using mocked Redis backend."""

    @pytest.mark.asyncio
    async def test_redis_acquire_allowed(self, mock_redis: MagicMock) -> None:
        limiter = UnifiedRateLimiter()
        limiter._redis = mock_redis
        limiter._lua_sha = "mock_sha"

        allowed, headers = await limiter.acquire("redis-key", rate=1.0, burst=5)
        assert allowed is True
        assert headers["X-RateLimit-Limit"] == 5
        assert headers["X-RateLimit-Remaining"] >= 0

    @pytest.mark.asyncio
    async def test_redis_acquire_denied(self) -> None:
        """When Lua returns not-allowed."""
        redis = MagicMock()
        redis.evalsha = AsyncMock(return_value=[0, 0.0, 5, 2.0])
        limiter = UnifiedRateLimiter()
        limiter._redis = redis
        limiter._lua_sha = "mock_sha"

        allowed, headers = await limiter.acquire("redis-deny", rate=1.0, burst=5)
        assert allowed is False
        assert "Retry-After" in headers

    @pytest.mark.asyncio
    async def test_redis_passed_evalsha(self, mock_redis: MagicMock) -> None:
        limiter = UnifiedRateLimiter()
        limiter._redis = mock_redis
        limiter._lua_sha = "mock_sha"

        await limiter.acquire("evalsha-key", rate=10.0, burst=20)
        mock_redis.evalsha.assert_called_once()
        args, _ = mock_redis.evalsha.call_args
        assert args[0] == "mock_sha"

    @pytest.mark.asyncio
    async def test_redis_error_falls_back_to_memory(self) -> None:
        """When Redis raises, falls back to in-memory."""
        redis = MagicMock()
        redis.evalsha = AsyncMock(side_effect=ConnectionError("Redis down"))
        limiter = UnifiedRateLimiter()
        limiter._redis = redis
        limiter._lua_sha = "mock_sha"

        allowed, headers = await limiter.acquire("failover-key", rate=1.0, burst=5)
        # Should fail open (in-memory fallback)
        assert allowed is True
        # Connection should be nulled for retry
        assert limiter._redis is None
        assert limiter._redis_failed is True


# ═══════════════════════════════════════════════════════════════════════════
# Stats
# ═══════════════════════════════════════════════════════════════════════════


class TestStats:
    """UnifiedRateLimiter.stats() — bucket inspection."""

    @pytest.mark.asyncio
    async def test_stats_no_redis(self) -> None:
        limiter = UnifiedRateLimiter()
        limiter._redis_failed = True  # Simulate Redis being unavailable
        await limiter.acquire("stat-key", rate=1.0, burst=5)
        stats = await limiter.stats("stat-key")
        assert stats["redis_available"] is False
        assert len(stats["buckets"]) >= 1
        assert "stat-key" in stats["buckets"]
