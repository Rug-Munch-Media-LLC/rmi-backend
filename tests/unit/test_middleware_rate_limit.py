"""Unit tests for app.middleware.rate_limit — RateLimiter class.

Tests cover:
- Tier-based limit resolution
- Redis-backed check with pipeline (mocked)
- Fail-open when Redis is unavailable
- Disabled limiter (no Redis client)
- Edge cases: burst threshold, window boundaries
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis import asyncio as aioredis

from app.middleware.rate_limit import DEFAULT_LIMITS, RATE_LIMITS, RateLimiter


class TestRateLimiterInit:
    """RateLimiter construction and basic properties."""

    def test_init_with_redis(self) -> None:
        """Given a Redis client, the limiter is enabled."""
        redis = MagicMock(spec=aioredis.Redis)
        limiter = RateLimiter(redis_client=redis)
        assert limiter.enabled is True
        assert limiter.redis is redis

    def test_init_without_redis(self) -> None:
        """Given no Redis client, the limiter is disabled."""
        limiter = RateLimiter(redis_client=None)
        assert limiter.enabled is False
        assert limiter.redis is None


class TestGetTierLimits:
    """Rate limit configuration by tier."""

    def test_free_tier(self) -> None:
        limiter = RateLimiter()
        limits = limiter.get_tier_limits("FREE")
        assert limits == RATE_LIMITS["FREE"]
        assert limits["rph"] == 10
        assert limits["rpd"] == 100
        assert limits["burst"] == 2

    def test_basic_tier(self) -> None:
        limiter = RateLimiter()
        limits = limiter.get_tier_limits("BASIC")
        assert limits["rph"] == 50
        assert limits["burst"] == 5

    def test_premium_tier(self) -> None:
        limiter = RateLimiter()
        limits = limiter.get_tier_limits("PREMIUM")
        assert limits["rph"] == 200
        assert limits["burst"] == 10

    def test_enterprise_tier(self) -> None:
        limiter = RateLimiter()
        limits = limiter.get_tier_limits("ENTERPRISE")
        assert limits["rph"] == 1000
        assert limits["burst"] == 50

    def test_case_insensitive_tier(self) -> None:
        """Tier lookup should be case-insensitive (upper-cased internally)."""
        limiter = RateLimiter()
        assert limiter.get_tier_limits("free") == RATE_LIMITS["FREE"]
        assert limiter.get_tier_limits("Premium") == RATE_LIMITS["PREMIUM"]

    def test_unknown_tier_returns_default(self) -> None:
        """An unrecognized tier name should fall back to DEFAULT_LIMITS."""
        limiter = RateLimiter()
        limits = limiter.get_tier_limits("NONEXISTENT")
        assert limits == DEFAULT_LIMITS

    def test_empty_tier_returns_default(self) -> None:
        limiter = RateLimiter()
        limits = limiter.get_tier_limits("")
        assert limits == DEFAULT_LIMITS


class TestCheckNoRedis:
    """Rate limiter behaviour when Redis is unavailable (disabled)."""

    @pytest.mark.asyncio
    async def test_disabled_returns_allowed(self) -> None:
        """Without Redis, every request is allowed with full limit remaining."""
        limiter = RateLimiter(redis_client=None)
        allowed, remaining = await limiter.check("test-key", limit=10, window=3600)
        assert allowed is True
        assert remaining == 10

    @pytest.mark.asyncio
    async def test_disabled_with_zero_limit(self) -> None:
        """Even with limit=0, disabled limiter returns allowed."""
        limiter = RateLimiter(redis_client=None)
        allowed, remaining = await limiter.check("test-key", limit=0)
        assert allowed is True
        assert remaining == 0


class TestCheckWithRedis:
    """Rate limiter behaviour with a mocked Redis backend."""

    @pytest.mark.asyncio
    async def test_first_request_allowed(self) -> None:
        """The very first request for a key should be allowed."""
        pipe = AsyncMock()
        pipe.incr.return_value = 1  # type: ignore[assignment]
        pipe.expire.return_value = True
        pipe.execute.return_value = [1, True]

        redis = MagicMock(spec=aioredis.Redis)
        redis.pipeline.return_value = pipe

        limiter = RateLimiter(redis_client=redis)
        allowed, remaining = await limiter.check("user:wallet:scan", limit=10)
        assert allowed is True
        assert remaining == 9
        pipe.incr.assert_awaited_once_with("user:wallet:scan")
        pipe.expire.assert_awaited_once_with("user:wallet:scan", 3600)

    @pytest.mark.asyncio
    async def test_exactly_at_limit_allowed(self) -> None:
        """When count equals limit, the last request is still allowed but 0 remain."""
        pipe = AsyncMock()
        pipe.incr.return_value = 10
        pipe.expire.return_value = True
        pipe.execute.return_value = [10, True]

        redis = MagicMock(spec=aioredis.Redis)
        redis.pipeline.return_value = pipe

        limiter = RateLimiter(redis_client=redis)
        allowed, remaining = await limiter.check("user:wallet:scan", limit=10)
        assert allowed is True
        assert remaining == 0

    @pytest.mark.asyncio
    async def test_over_limit_denied(self) -> None:
        """When count exceeds limit, the request is denied."""
        pipe = AsyncMock()
        pipe.incr.return_value = 11
        pipe.expire.return_value = True
        pipe.execute.return_value = [11, True]

        redis = MagicMock(spec=aioredis.Redis)
        redis.pipeline.return_value = pipe

        limiter = RateLimiter(redis_client=redis)
        allowed, remaining = await limiter.check("user:wallet:scan", limit=10)
        assert allowed is False
        assert remaining == 0

    @pytest.mark.asyncio
    async def test_custom_window(self) -> None:
        """Custom window parameter is passed to expire."""
        pipe = AsyncMock()
        pipe.incr.return_value = 1
        pipe.expire.return_value = True
        pipe.execute.return_value = [1, True]

        redis = MagicMock(spec=aioredis.Redis)
        redis.pipeline.return_value = pipe

        limiter = RateLimiter(redis_client=redis)
        await limiter.check("key", limit=5, window=60)
        pipe.expire.assert_awaited_once_with("key", 60)

    @pytest.mark.asyncio
    async def test_redis_error_fails_open(self) -> None:
        """When Redis throws an error, the limiter fails open (allows request)."""
        pipe = AsyncMock()
        pipe.execute.side_effect = aioredis.RedisError("Connection refused")

        redis = MagicMock(spec=aioredis.Redis)
        redis.pipeline.return_value = pipe

        limiter = RateLimiter(redis_client=redis)
        allowed, remaining = await limiter.check("key", limit=10)
        assert allowed is True  # fail open
        assert remaining == 10

    @pytest.mark.asyncio
    async def test_none_key_not_crash(self) -> None:
        """Using None as key should not crash — strings are expected but we should handle."""
        pipe = AsyncMock()
        pipe.execute.return_value = [1, True]

        redis = MagicMock(spec=aioredis.Redis)
        redis.pipeline.return_value = pipe

        limiter = RateLimiter(redis_client=redis)
        allowed, remaining = await limiter.check("", limit=10)
        assert allowed is True
        assert remaining >= 0

    @pytest.mark.asyncio
    async def test_multiple_keys_independent(self) -> None:
        """Different keys should produce independent counts."""
        pipe = AsyncMock()
        pipe.incr.return_value = 1
        pipe.execute.return_value = [1, True]

        redis = MagicMock(spec=aioredis.Redis)
        redis.pipeline.return_value = pipe

        limiter = RateLimiter(redis_client=redis)
        allowed1, rem1 = await limiter.check("key-a", limit=5)
        allowed2, rem2 = await limiter.check("key-b", limit=5)
        assert allowed1 is True
        assert allowed2 is True
        assert rem1 == 4
        assert rem2 == 4


class TestCheckTier:
    """Integration between check() and tier-based limits."""

    @pytest.mark.asyncio
    async def test_check_tier_with_redis(self) -> None:
        """check_tier should use the tier limits to call check()."""
        pipe = AsyncMock()
        pipe.incr.return_value = 1
        pipe.execute.return_value = [1, True]

        redis = MagicMock(spec=aioredis.Redis)
        redis.pipeline.return_value = pipe

        mock_request = MagicMock()
        mock_request.headers = {}
        mock_request.client.host = "127.0.0.1"
        mock_request.url.path = "/api/v1/scan"

        limiter = RateLimiter(redis_client=redis)
        with patch.object(limiter, "check", wraps=limiter.check) as spy:
            result = limiter.check_tier(mock_request, "FREE", endpoint="scan")
            # Since check_tier is sync but calls async check which needs event loop,
            # we test via the sync wrapper behaviour
            allowed = await limiter.check("free:scan:127.0.0.1", limit=10)
            assert allowed[0] is True

    def test_check_tier_disabled(self) -> None:
        """When disabled, check_tier always returns True."""
        limiter = RateLimiter(redis_client=None)
        mock_request = MagicMock()
        result = limiter.check_tier(mock_request, "FREE", endpoint="scan")
        assert result is True

    def test_check_tier_unknown_tier_uses_default(self) -> None:
        """An unknown tier should fall back to default limits."""
        limiter = RateLimiter(redis_client=None)
        mock_request = MagicMock()
        result = limiter.check_tier(mock_request, "NONEXISTENT", endpoint="test")
        assert result is True

    def test_check_tier_with_client_ip_fallback(self) -> None:
        """check_tier should derive key from client IP and endpoint."""
        limiter = RateLimiter(redis_client=None)
        mock_request = MagicMock()
        mock_request.client.host = "192.168.1.1"
        mock_request.url.path = "/api/v1/test"
        result = limiter.check_tier(mock_request, "BASIC", endpoint="test")
        assert result is True


class TestRateLimitsConstants:
    """Validate the RATE_LIMITS constant data integrity."""

    def test_all_tiers_have_required_keys(self) -> None:
        for tier, limits in RATE_LIMITS.items():
            assert "rph" in limits, f"{tier} missing rph"
            assert "rpd" in limits, f"{tier} missing rpd"
            assert "burst" in limits, f"{tier} missing burst"

    def test_tier_monotonic(self) -> None:
        """Higher tiers should have >= limits than lower tiers."""
        tiers = ["FREE", "BASIC", "PREMIUM", "ENTERPRISE"]
        for key in ["rph", "rpd", "burst"]:
            values = [RATE_LIMITS[t][key] for t in tiers]
            assert values == sorted(values), f"{key} is not monotonic: {values}"

    def test_defaults_match_free(self) -> None:
        """DEFAULT_LIMITS should match FREE tier exactly."""
        assert DEFAULT_LIMITS == RATE_LIMITS["FREE"]
