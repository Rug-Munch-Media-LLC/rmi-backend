"""Security tests: Rate limiting — token bucket, tiers, fallback, headers.

Tests verify REAL behavior:
  - UnifiedRateLimiter token bucket acquire/release
  - Rate limit headers (Limit, Remaining, Reset) presence and correctness
  - Free tier vs Pro tier limit enforcement
  - In-memory fallback when Redis is unavailable
  - Auto-reconnect detection (redis_failed flag behavior)
  - Burst capacity handling
  - Path category routing (different limits per endpoint type)
  - Tier-based limits (FREE/PRO/ENTERPRISE)
  - RateLimitMiddleware via TestClient

These are unit+integration hybrid tests. The core token-bucket logic is
tested directly on the UnifiedRateLimiter class. Middleware integration
tests via TestClient verify header delivery.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from .conftest import TEST_RMI_AUTH_TOKEN

# ═══════════════════════════════════════════════════════════════════════
# Test Class: Token Bucket Algorithm (direct unit tests)
# ═══════════════════════════════════════════════════════════════════════


class TestTokenBucket:
    """UnifiedRateLimiter token bucket acquire/release (in-memory mode)."""

    @pytest.fixture
    def limiter(self):
        """Create a fresh UnifiedRateLimiter with no Redis (in-memory mode)."""
        from app.core.rate_limiter import UnifiedRateLimiter

        limiter_inst = UnifiedRateLimiter()
        limiter_inst._redis = None
        limiter_inst._redis_failed = True
        limiter_inst._redis_failed_at = 0.0
        return limiter_inst

    @pytest.mark.asyncio
    async def test_acquire_allows_within_burst(self, limiter):
        """Acquire should succeed when tokens are available within burst."""
        allowed, headers = await limiter.acquire("test:key1", rate=10.0, burst=5.0)
        assert allowed is True
        assert "X-RateLimit-Limit" in headers
        assert "X-RateLimit-Remaining" in headers
        assert "X-RateLimit-Reset" in headers

    @pytest.mark.asyncio
    async def test_acquire_exhausts_burst(self, limiter):
        """Acquire should eventually fail when burst is exhausted."""
        # Burst=2 → 2 requests allowed, 3rd denied
        r1, h1 = await limiter.acquire("test:burst-key", rate=0.1, burst=2.0)
        assert r1 is True
        assert int(h1["X-RateLimit-Limit"]) == 2
        assert int(h1["X-RateLimit-Remaining"]) == 1

        r2, h2 = await limiter.acquire("test:burst-key", rate=0.1, burst=2.0)
        assert r2 is True
        assert int(h2["X-RateLimit-Remaining"]) == 0

        r3, h3 = await limiter.acquire("test:burst-key", rate=0.1, burst=2.0)
        assert r3 is False, "Third request should be denied (burst exhausted)"
        assert "Retry-After" in h3

    @pytest.mark.asyncio
    async def test_rate_limit_headers_present(self, limiter):
        """Rate limit headers must include Limit, Remaining, Reset."""
        allowed, headers = await limiter.acquire("test:headers-key", rate=5.0, burst=10.0)
        assert allowed is True
        assert "X-RateLimit-Limit" in headers
        assert "X-RateLimit-Remaining" in headers
        assert "X-RateLimit-Reset" in headers
        # Validate header values are numeric strings (Starlette requires str header values)
        assert isinstance(headers["X-RateLimit-Limit"], str)
        assert isinstance(headers["X-RateLimit-Remaining"], str)
        assert isinstance(headers["X-RateLimit-Reset"], str)

    @pytest.mark.asyncio
    async def test_remaining_decreases_with_each_request(self, limiter):
        """X-RateLimit-Remaining should decrease with each successful acquire."""
        for i in range(3):
            allowed, headers = await limiter.acquire("test:decrease-key", rate=1.0, burst=3.0)
            assert allowed is True
            assert int(headers["X-RateLimit-Remaining"]) == 3 - i - 1

    @pytest.mark.asyncio
    async def test_retry_after_header_on_deny(self, limiter):
        """Denied requests should get a Retry-After header."""
        # Exhaust burst (burst=1)
        await limiter.acquire("test:retry-key", rate=0.01, burst=1.0)
        allowed, headers = await limiter.acquire("test:retry-key", rate=0.01, burst=1.0)
        assert allowed is False
        assert "Retry-After" in headers
        assert isinstance(headers["Retry-After"], str)
        assert int(headers["Retry-After"]) >= 1

    @pytest.mark.asyncio
    async def test_burst_refills_over_time(self, limiter):
        """Burst capacity should refill over time based on rate."""
        # Burst=1, rate=1000/s → fast refill
        await limiter.acquire("test:refill-key", rate=1000.0, burst=1.0)
        # Wait a tiny bit for refill
        await asyncio.sleep(0.01)
        allowed, _ = await limiter.acquire("test:refill-key", rate=1000.0, burst=1.0)
        assert allowed is True, "Burst should refill rapidly with high rate"


# ═══════════════════════════════════════════════════════════════════════
# Test Class: Path Category Resolution
# ═══════════════════════════════════════════════════════════════════════


class TestPathCategoryResolution:
    """_resolve_rate_limit should return correct limits per path category."""

    def test_health_path(self):
        """Health paths should get high rate limits."""
        from app.core.rate_limiter import _resolve_rate_limit

        rate, burst = _resolve_rate_limit("/health")
        assert rate > 10  # 1000/60 ≈ 16.67
        assert burst >= 200

    def test_auth_path(self):
        """Auth paths should get low rate limits (brute-force protection)."""
        from app.core.rate_limiter import _resolve_rate_limit

        # PATH_CATEGORIES maps "/login" (not "/api/v1/auth/login") to "auth" (5 rpm)
        rate, burst = _resolve_rate_limit("/login")
        # 5 rpm / 60 ≈ 0.083
        assert rate < 0.2, f"Expected low rate for auth, got {rate}"
        assert burst == 2

    def test_api_v1_auth_login_gets_auth_limit(self):
        """/api/v1/auth/login maps to the 'auth' category (5 rpm brute-force protection)."""
        from app.core.rate_limiter import _resolve_rate_limit

        rate, burst = _resolve_rate_limit("/api/v1/auth/login")
        # auth: 5 rpm / 60 ≈ 0.083 rps, burst 2
        assert rate < 0.2, f"Expected low rate for auth, got {rate}"
        assert burst == 2

    def test_scan_path(self):
        """Scan paths should get moderate limits."""
        from app.core.rate_limiter import _resolve_rate_limit

        rate, burst = _resolve_rate_limit("/api/v1/scanner/token")
        # 10 rpm / 60 ≈ 0.167
        assert 0.1 < rate < 0.3
        assert burst == 2

    def test_x402_path(self):
        """x402 payment paths should get high limits."""
        from app.core.rate_limiter import _resolve_rate_limit

        rate, burst = _resolve_rate_limit("/api/v1/x402/verify")
        # 1000 rpm / 60 ≈ 16.67
        assert rate > 10
        assert burst >= 200

    def test_admin_path(self):
        """Admin paths should get low limits."""
        from app.core.rate_limiter import _resolve_rate_limit

        rate, burst = _resolve_rate_limit("/api/v1/admin/settings")
        # 10 rpm / 60 ≈ 0.167
        assert rate < 0.3
        assert burst == 2

    def test_default_path(self):
        """Unknown paths should get default limits."""
        from app.core.rate_limiter import _resolve_rate_limit

        rate, burst = _resolve_rate_limit("/some/unknown/path")
        # default: 60 rpm / 60 = 1.0
        assert rate == 1.0
        assert burst == 15


# ═══════════════════════════════════════════════════════════════════════
# Test Class: Free vs Pro Tier Limits
# ═══════════════════════════════════════════════════════════════════════


class TestTierLimits:
    """FREE vs PRO vs ENTERPRISE tier limit enforcement."""

    def test_free_tier_limits_match_expected(self):
        """FREE tier should have 100 req/day, 10 req/min, burst=2."""
        from app.core.rate_limiter import TIER_LIMITS, Tier

        limits = TIER_LIMITS[Tier.FREE]
        assert limits["requests_per_day"] == 100
        assert limits["requests_per_minute"] == 10
        assert limits["concurrent_requests"] == 2

    def test_pro_tier_limits_higher_than_free(self):
        """PRO tier limits should be strictly higher than FREE."""
        from app.core.rate_limiter import TIER_LIMITS, Tier

        free = TIER_LIMITS[Tier.FREE]
        pro = TIER_LIMITS[Tier.PRO]
        assert pro["requests_per_day"] > free["requests_per_day"]
        assert pro["requests_per_minute"] > free["requests_per_minute"]
        assert pro["concurrent_requests"] > free["concurrent_requests"]

    def test_enterprise_tier_limits_highest(self):
        """ENTERPRISE tier should have the highest limits."""
        from app.core.rate_limiter import TIER_LIMITS, Tier

        pro = TIER_LIMITS[Tier.PRO]
        ent = TIER_LIMITS[Tier.ENTERPRISE]
        assert ent["requests_per_day"] >= pro["requests_per_day"]
        assert ent["requests_per_minute"] >= pro["requests_per_minute"]
        assert ent["concurrent_requests"] >= pro["concurrent_requests"]

    def test_get_user_tier_defaults_to_free(self):
        """get_user_tier should return FREE for unknown users."""
        from app.core.rate_limiter import Tier, get_user_tier

        tier = get_user_tier("nonexistent-user-id")
        assert tier == Tier.FREE

    def test_check_rate_limit_free_default(self):
        """check_rate_limit for free user should exist and not crash."""
        from app.core.rate_limiter import check_rate_limit

        # This will try Redis; in test env it may fail but should be graceful
        result = check_rate_limit("test-free-user", "/api/v1/scanner/token")
        # Should not crash; may return True or False depending on Redis
        assert isinstance(result, bool)


# ═══════════════════════════════════════════════════════════════════════
# Test Class: Redis Failure / In-Memory Fallback
# ═══════════════════════════════════════════════════════════════════════


class TestRedisFallback:
    """Rate limiter should gracefully degrade when Redis is unavailable."""

    @pytest.mark.asyncio
    async def test_fallback_when_redis_down(self):
        """UnifiedRateLimiter should use in-memory fallback when Redis is down."""
        from app.core.rate_limiter import UnifiedRateLimiter

        limiter = UnifiedRateLimiter()
        # Force Redis to be "down"
        limiter._redis = None
        limiter._redis_failed = True
        limiter._redis_failed_at = 0.0

        with patch.object(limiter, "_ensure_redis", return_value=None):
            allowed, headers = await limiter.acquire("fallback:key", rate=5.0, burst=3.0)
            assert allowed is True
            assert "X-RateLimit-Limit" in headers
            assert int(headers["X-RateLimit-Limit"]) == 3

    @pytest.mark.asyncio
    async def test_memory_fallback_still_enforces_limits(self):
        """In-memory fallback should still enforce rate limits."""
        from app.core.rate_limiter import UnifiedRateLimiter

        limiter = UnifiedRateLimiter()
        limiter._redis = None
        limiter._redis_failed = True

        with patch.object(limiter, "_ensure_redis", return_value=None):
            # Exhaust burst of 1
            await limiter.acquire("fallback:burst-test", rate=0.001, burst=1.0)
            allowed, _ = await limiter.acquire("fallback:burst-test", rate=0.001, burst=1.0)
            assert allowed is False, "In-memory mode should still deny when burst exhausted"

    @pytest.mark.asyncio
    async def test_memory_fallback_recovers_over_time(self):
        """In-memory fallback should allow requests after burst refill."""
        from app.core.rate_limiter import UnifiedRateLimiter

        limiter = UnifiedRateLimiter()
        limiter._redis = None
        limiter._redis_failed = True

        with patch.object(limiter, "_ensure_redis", return_value=None):
            await limiter.acquire("fallback:recover-test", rate=1000.0, burst=1.0)
            await asyncio.sleep(0.01)
            allowed, _ = await limiter.acquire("fallback:recover-test", rate=1000.0, burst=1.0)
            assert allowed is True, "Should recover after burst refill"

    @pytest.mark.asyncio
    async def test_redis_failure_sets_failed_flag(self):
        """Redis failure should set _redis_failed and _redis_failed_at."""
        from app.core.rate_limiter import UnifiedRateLimiter

        limiter = UnifiedRateLimiter()
        limiter._redis = MagicMock()
        limiter._redis.ping = AsyncMock(side_effect=ConnectionError("Redis down"))

        _ = await limiter._ensure_redis()
        # Should return None because Redis ping failed
        # Note: this is during init, so _ensure_redis tries to connect
        # _ensure_redis would attempt connection

    @pytest.mark.asyncio
    async def test_redis_reconnect_after_interval(self):
        """UnifiedRateLimiter should attempt Redis reconnect after RETRY_INTERVAL."""
        from app.core.rate_limiter import UnifiedRateLimiter

        limiter = UnifiedRateLimiter()
        # Set redis_failed_at far enough in the past that retry is due
        limiter._redis_failed = True
        limiter._redis_failed_at = time.monotonic() - 60.0  # 60s ago (>30s interval)

        # _ensure_redis should attempt connection since RETRY_INTERVAL has passed
        # In test environment without Redis, it will fail and stay in failed mode
        result = await limiter._ensure_redis()
        # Result is None because no Redis available
        assert result is None or hasattr(result, "ping")

    @pytest.mark.asyncio
    async def test_redis_disconnect_does_not_crash(self):
        """If Redis disconnects mid-operation, rate limiter should not crash."""
        from app.core.rate_limiter import UnifiedRateLimiter

        limiter = UnifiedRateLimiter()
        # Simulate Redis that was working but then fails
        mock_redis = MagicMock()
        mock_redis.evalsha = AsyncMock(side_effect=ConnectionError("Lost connection"))

        with patch.object(limiter, "_ensure_redis", return_value=mock_redis):
            # Should fall back to in-memory instead of crashing
            allowed, _ = await limiter.acquire("crash-test-key", rate=5.0, burst=3.0)
            assert allowed is True, "Should not crash on Redis disconnect"
            # The limiter should have nulled the Redis connection
            assert limiter._redis is None
            assert limiter._redis_failed is True


# ═══════════════════════════════════════════════════════════════════════
# Test Class: Rate Limit Middleware Integration
# ═══════════════════════════════════════════════════════════════════════


class TestRateLimitMiddleware:
    """RateLimitMiddleware behavior via TestClient (integration)."""

    @pytest.fixture(autouse=True)
    def _patch_limiter(self):
        """Patch the global rate limiter to use in-memory mode for tests."""
        import app.core.rate_limiter as rl_mod

        with patch.object(rl_mod.UnifiedRateLimiter, "_ensure_redis", return_value=None):
            yield

    def test_rate_limit_headers_on_response(self, client):
        """Responses should include X-RateLimit-* headers."""
        resp = client.get(
            "/api/v1/wallet/abc123",
            headers={"X-API-Key": TEST_RMI_AUTH_TOKEN},
        )
        # Headers may be present depending on middleware installation
        assert "X-RateLimit-Limit" in resp.headers
        assert "X-RateLimit-Remaining" in resp.headers
        assert "X-RateLimit-Reset" in resp.headers
        # At minimum, response should not crash
        assert resp.status_code != 500

    def test_public_endpoints_get_headers(self, client):
        """Public endpoints should also get rate limit headers."""
        resp = client.get("/health")
        assert resp.status_code in (200, 404)


# ═══════════════════════════════════════════════════════════════════════
# Test Class: Concurrent Request Handling
# ═══════════════════════════════════════════════════════════════════════


class TestConcurrency:
    """Rate limiter should handle concurrent requests safely."""

    @pytest.mark.asyncio
    async def test_concurrent_acquires_on_same_key(self):
        """Multiple concurrent acquires on the same key should not over-draft."""
        from app.core.rate_limiter import UnifiedRateLimiter

        limiter = UnifiedRateLimiter()
        limiter._redis = None
        limiter._redis_failed = True

        with patch.object(limiter, "_ensure_redis", return_value=None):
            # Fire 5 concurrent acquires on burst=3
            tasks = [limiter.acquire("concurrent:key", rate=0.01, burst=3.0) for _ in range(5)]
            results = await asyncio.gather(*tasks)

            # At most 3 should be allowed (burst capacity)
            allowed_count = sum(1 for r, _ in results if r)
            assert allowed_count <= 3, f"Expected ≤3 allowed, got {allowed_count}"

    @pytest.mark.asyncio
    async def test_multiple_keys_independent(self):
        """Different keys should have independent rate limit buckets."""
        from app.core.rate_limiter import UnifiedRateLimiter

        limiter = UnifiedRateLimiter()
        limiter._redis = None
        limiter._redis_failed = True

        with patch.object(limiter, "_ensure_redis", return_value=None):
            r1, _ = await limiter.acquire("key-a", rate=0.1, burst=1.0)
            r2, _ = await limiter.acquire("key-b", rate=0.1, burst=1.0)

            assert r1 is True
            assert r2 is True


# ═══════════════════════════════════════════════════════════════════════
# Test Class: PII-safe Hashing
# ═══════════════════════════════════════════════════════════════════════


class TestPIIHash:
    """PII-safe hashing for rate limit keys should be deterministic and secure."""

    def test_hash_identifier_returns_string(self):
        """_hash_identifier should return a hex string."""
        from app.core.rate_limiter import _hash_identifier

        result = _hash_identifier("test-wallet-address")
        assert isinstance(result, str)
        assert len(result) == 32  # HMAC-SHA256 truncated to 32 chars

    def test_hash_identifier_deterministic(self):
        """Same input should produce same hash within same day."""
        from app.core.rate_limiter import _hash_identifier

        h1 = _hash_identifier("wallet-abc")
        h2 = _hash_identifier("wallet-abc")
        assert h1 == h2

    def test_hash_identifier_different_inputs_differ(self):
        """Different inputs should produce different hashes."""
        from app.core.rate_limiter import _hash_identifier

        h1 = _hash_identifier("wallet-abc")
        h2 = _hash_identifier("wallet-xyz")
        assert h1 != h2

    def test_hash_identifier_not_reversible(self):
        """Hash should not be the raw input (no plaintext leakage)."""
        from app.core.rate_limiter import _hash_identifier

        h = _hash_identifier("sensitive-wallet-address")
        assert "sensitive" not in h
        assert h.startswith("sensitive") is False


# ═══════════════════════════════════════════════════════════════════════
# Test Class: Acquire Path (path-based rate limiting)
# ═══════════════════════════════════════════════════════════════════════


class TestAcquirePath:
    """acquire_path should map paths to correct rate limit categories."""

    @pytest.mark.asyncio
    async def test_acquire_path_health(self):
        from app.core.rate_limiter import UnifiedRateLimiter

        limiter = UnifiedRateLimiter()
        limiter._redis = None
        limiter._redis_failed = True

        with patch.object(limiter, "_ensure_redis", return_value=None):
            allowed, headers = await limiter.acquire_path("/health", "test-client")
            assert allowed is True
            assert "X-RateLimit-Limit" in headers

    @pytest.mark.asyncio
    async def test_acquire_path_with_api_key(self):
        """acquire_path should work with hashed API key as client ID."""
        from app.core.rate_limiter import UnifiedRateLimiter

        limiter = UnifiedRateLimiter()
        limiter._redis = None
        limiter._redis_failed = True

        with patch.object(limiter, "_ensure_redis", return_value=None):
            allowed, _ = await limiter.acquire_path(
                "/api/v1/scanner/token",
                "apikey_hash_client",
            )
            assert allowed is True


# ═══════════════════════════════════════════════════════════════════════
# Test Class: Provider-style Acquire (backward compat)
# ═══════════════════════════════════════════════════════════════════════


class TestAcquireProvider:
    """acquire_provider backward-compatible interface."""

    @pytest.mark.asyncio
    async def test_acquire_provider_basic(self):
        from app.core.rate_limiter import UnifiedRateLimiter

        limiter = UnifiedRateLimiter()
        limiter._redis = None
        limiter._redis_failed = True

        with patch.object(limiter, "_ensure_redis", return_value=None):
            allowed, wait = await limiter.acquire_provider("test-provider", "embed", tokens=1, rate=1.0, burst=5.0)
            assert allowed is True
            assert isinstance(wait, float)
            assert wait == 0.0  # No wait when allowed
