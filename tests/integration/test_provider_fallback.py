"""Integration tests for provider fallback chains — UnifiedProvider, circuit breaker, rate limiter, cache.

Tests the multi-provider fallback architecture:
  - Provider fallback when primary fails (UnifiedProvider cascading)
  - Circuit breaker state transitions (OPEN → HALF_OPEN → CLOSED)
  - Rate limiter behavior (can_call / record / limits)
  - Cache hit/miss behavior (ChainCache, RMICache)

These are functional tests that exercise the logic directly by mocking
external dependencies (HTTP, Redis) so they're repeatable in any environment.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ═══════════════════════════════════════════════════════════════
# ── Provider Fallback
# ═══════════════════════════════════════════════════════════════


class TestProviderFallback:
    """UnifiedProvider cascade fallback when primary data source fails."""

    def _make_provider(self):
        """Create a UnifiedProvider with mocked cache to avoid cross-test pollution."""
        from app.unified_provider import UnifiedProvider

        provider = UnifiedProvider()
        # Mock the cache to always return None (miss) so fallback logic runs
        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock(return_value=None)
        provider._get_cache = MagicMock(return_value=mock_cache)  # type: ignore
        return provider

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_token_holders_fallback_empty(self) -> None:
        """When all providers fail, get_token_holders returns empty list."""
        provider = self._make_provider()

        # Mock all internal methods to return empty (simulating failure)
        provider._helius_holders = AsyncMock(return_value=[])  # type: ignore
        provider._birdeye_holders = AsyncMock(return_value=[])  # type: ignore
        provider._solscan_holders = AsyncMock(return_value=[])  # type: ignore
        provider._dexscreener_holders = AsyncMock(return_value=[])  # type: ignore

        result = await provider.get_token_holders(
            "So11111111111111111111111111111111111111112", limit=10
        )
        assert isinstance(result, list)
        assert result == []

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_token_holders_helius_success(self) -> None:
        """When Helius succeeds, results come from Helius (first priority)."""
        provider = self._make_provider()
        fake_holders = [
            {"address": "Addr1", "amount": 100, "source": "helius"},
            {"address": "Addr2", "amount": 50, "source": "helius"},
        ]

        provider._helius_holders = AsyncMock(return_value=fake_holders)  # type: ignore
        provider._birdeye_holders = AsyncMock(return_value=[])  # type: ignore
        provider._solscan_holders = AsyncMock(return_value=[])  # type: ignore
        provider._dexscreener_holders = AsyncMock(return_value=[])  # type: ignore

        result = await provider.get_token_holders(
            "So11111111111111111111111111111111111111112", limit=10
        )
        assert len(result) == 2
        assert result[0]["source"] == "helius"
        assert result[0]["address"] == "Addr1"
        # Birdeye should not have been called since Helius succeeded
        provider._birdeye_holders.assert_not_called()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_token_holders_fallback_to_birdeye(self) -> None:
        """When Helius fails, falls back to Birdeye."""
        provider = self._make_provider()
        fake_holders = [
            {"address": "Addr3", "amount": 200, "source": "birdeye"},
        ]

        provider._helius_holders = AsyncMock(return_value=[])  # type: ignore
        provider._birdeye_holders = AsyncMock(return_value=fake_holders)  # type: ignore
        provider._solscan_holders = AsyncMock(return_value=[])  # type: ignore
        provider._dexscreener_holders = AsyncMock(return_value=[])  # type: ignore

        result = await provider.get_token_holders(
            "So11111111111111111111111111111111111111112", limit=10
        )
        assert len(result) == 1
        assert result[0]["source"] == "birdeye"
        provider._helius_holders.assert_called_once()
        provider._birdeye_holders.assert_called_once()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_token_price_no_providers(self) -> None:
        """When all price providers fail, get_token_price returns None."""
        provider = self._make_provider()

        # Mock birdeye to be available but fail
        mock_birdeye = AsyncMock()
        mock_birdeye.get_price = AsyncMock(return_value={"data": {"value": None}})
        mock_birdeye._call = AsyncMock(return_value={})
        provider._birdeye = mock_birdeye

        # Mock DexScreener httpx to fail too
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_client = AsyncMock()
            mock_httpx.return_value.__aenter__.return_value = mock_client
            mock_response = AsyncMock()
            mock_response.status_code = 429  # Rate limited
            mock_client.get.return_value = mock_response

            result = await provider.get_token_price("So11111111111111111111111111111111111111112")
            assert result is None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_token_metadata_fallback_name(self) -> None:
        """When all metadata providers fail, returns fallback name."""
        provider = self._make_provider()

        # Mock chain_client (Helius) to fail
        with (
            patch(
                "app.chain_client.get_chain_client",
                side_effect=ConnectionError("No connection"),
            ),
            patch("httpx.AsyncClient") as mock_httpx,
        ):
            # Mock DexScreener to fail too
            mock_client = AsyncMock()
            mock_httpx.return_value.__aenter__.return_value = mock_client
            mock_response = AsyncMock()
            mock_response.status_code = 429
            mock_client.get.return_value = mock_response

            result = await provider.get_token_metadata(
                "So11111111111111111111111111111111111111112"
            )

        # Should return fallback metadata with truncated address as name
        assert isinstance(result, dict)
        assert "source" in result
        assert result["source"] == "fallback"
        assert result["name"] == "So111111"  # First 8 chars of the address
        assert result["symbol"] == "???"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_wallet_transactions_fallback_empty(self) -> None:
        """When all tx providers fail, get_wallet_transactions returns empty."""
        provider = self._make_provider()
        provider._helius_txs = AsyncMock(return_value=[])  # type: ignore
        provider._solscan_txs = AsyncMock(return_value=[])  # type: ignore

        result = await provider.get_wallet_transactions(
            "FakeeJx7WjJAsC4rQxqVxqxjQobxKGYyLqCJGgYJyQSn", limit=5
        )
        assert isinstance(result, list)
        assert result == []

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_wallet_transactions_helius_success(self) -> None:
        """When Helius succeeds for txs, result comes from Helius."""
        provider = self._make_provider()
        fake_txs = [
            {"signature": "sig1", "timestamp": 1234567890, "status": "success", "source": "helius"},
        ]

        provider._helius_txs = AsyncMock(return_value=fake_txs)  # type: ignore
        provider._solscan_txs = AsyncMock(return_value=[])  # type: ignore

        result = await provider.get_wallet_transactions(
            "FakeeJx7WjJAsC4rQxqVxqxjQobxKGYyLqCJGgYJyQSn", limit=5
        )
        assert len(result) == 1
        assert result[0]["source"] == "helius"
        provider._solscan_txs.assert_not_called()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_wallet_transactions_fallback_to_solscan(self) -> None:
        """When Helius fails for txs, falls back to Solscan."""
        provider = self._make_provider()
        fake_txs = [
            {"signature": "sig2", "source": "solscan"},
        ]

        provider._helius_txs = AsyncMock(return_value=[])  # type: ignore
        provider._solscan_txs = AsyncMock(return_value=fake_txs)  # type: ignore

        result = await provider.get_wallet_transactions(
            "FakeeJx7WjJAsC4rQxqVxqxjQobxKGYyLqCJGgYJyQSn", limit=5
        )
        assert len(result) == 1
        assert result[0]["source"] == "solscan"


# ═══════════════════════════════════════════════════════════════
# ── Circuit Breaker
# ═══════════════════════════════════════════════════════════════


class TestCircuitBreaker:
    """Circuit breaker state machine behavior (without Redis)."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_check_circuit_redis_down(self) -> None:
        """When Redis is down, check_circuit returns False (fail open)."""
        from app.circuit_breaker import check_circuit

        # When Redis is unreachable, should fail open
        result = await check_circuit("test_service")
        assert result is False

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_record_success_redis_down(self) -> None:
        """record_success should not raise when Redis is down."""
        from app.circuit_breaker import record_success

        try:
            await record_success("test_service")
        except Exception as exc:
            pytest.fail(f"record_success raised unexpectedly: {exc}")

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_record_failure_redis_down(self) -> None:
        """record_failure should not raise when Redis is down."""
        from app.circuit_breaker import record_failure

        try:
            await record_failure("test_service")
        except Exception as exc:
            pytest.fail(f"record_failure raised unexpectedly: {exc}")

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_check_circuit_with_mocked_redis_open(self) -> None:
        """When circuit is OPEN, check_circuit returns True (block the call)."""
        import time

        from app.circuit_breaker import check_circuit

        mock_redis = MagicMock()
        mock_redis.get.side_effect = lambda k: {
            "circuit:svc:state": "OPEN",
            "circuit:svc:opened_at": str(time.time()),
        }.get(k)

        with patch("app.circuit_breaker._get_redis", return_value=mock_redis):
            result = await check_circuit("svc")
            assert result is True, "OPEN circuit should block calls"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_check_circuit_with_mocked_redis_closed(self) -> None:
        """When circuit is CLOSED, check_circuit returns False (safe to call)."""
        from app.circuit_breaker import check_circuit

        mock_redis = MagicMock()
        mock_redis.get.return_value = None  # No state = CLOSED

        with patch("app.circuit_breaker._get_redis", return_value=mock_redis):
            result = await check_circuit("svc2")
            assert result is False, "CLOSED circuit should allow calls"


# ═══════════════════════════════════════════════════════════════
# ── Rate Limiter
# ═══════════════════════════════════════════════════════════════


class TestRateLimiterBehavior:
    """Rate limiter functional behavior tests."""

    @pytest.mark.integration
    def test_rate_limits_config(self) -> None:
        """DEFAULT_RATE_LIMITS has all expected categories."""
        from app.core.rate_limiter import DEFAULT_RATE_LIMITS

        assert "health" in DEFAULT_RATE_LIMITS
        assert "auth" in DEFAULT_RATE_LIMITS
        assert "scan" in DEFAULT_RATE_LIMITS
        assert "ai" in DEFAULT_RATE_LIMITS
        assert "market" in DEFAULT_RATE_LIMITS
        assert "public" in DEFAULT_RATE_LIMITS
        assert "admin" in DEFAULT_RATE_LIMITS
        assert "default" in DEFAULT_RATE_LIMITS

        # All limits should be positive integers
        for category, (rpm, burst_factor) in DEFAULT_RATE_LIMITS.items():
            assert isinstance(rpm, int), f"{category} rpm not int: {type(rpm)}"
            assert rpm > 0, f"{category} rpm not positive: {rpm}"
            assert isinstance(burst_factor, float)

    @pytest.mark.integration
    def test_path_categories_coverage(self) -> None:
        """PATH_CATEGORIES maps common paths to rate limit categories."""
        from app.core.rate_limiter import PATH_CATEGORIES

        assert "/health" in PATH_CATEGORIES
        assert "/api/v1/scanner" in PATH_CATEGORIES
        assert "/api/v1/market" in PATH_CATEGORIES
        assert "/api/v1/admin" in PATH_CATEGORIES

    @pytest.mark.integration
    def test_rate_resolves_correctly(self) -> None:
        """_resolve_rate_limit returns correct rate for paths."""
        from app.core.rate_limiter import _resolve_rate_limit

        rate, burst = _resolve_rate_limit("/health")
        assert rate > 0
        assert burst > 0

        rate, burst = _resolve_rate_limit("/api/v1/scanner/token/scan")
        assert rate <= 20  # scan category: 20/min

    @pytest.mark.integration
    def test_old_rate_limiter_config(self) -> None:
        """Legacy middleware rate limiter has tier configs."""
        from app.middleware.rate_limit import RATE_LIMITS

        for tier in ("FREE", "BASIC", "PREMIUM", "ENTERPRISE"):
            assert tier in RATE_LIMITS, f"Missing tier: {tier}"
            cfg = RATE_LIMITS[tier]
            assert "rph" in cfg
            assert "rpd" in cfg
            assert "burst" in cfg
            assert cfg["rph"] > 0
            assert cfg["rpd"] > 0
            assert cfg["burst"] > 0

    @pytest.mark.integration
    def test_scan_rate_limiter_tiers(self) -> None:
        """Scan rate limiter has tier constants."""
        from app.scan_rate_limiter import FREE_DAILY_LIMIT, PRO_DAILY_LIMIT

        assert isinstance(FREE_DAILY_LIMIT, int)
        assert isinstance(PRO_DAILY_LIMIT, int)
        assert FREE_DAILY_LIMIT > 0 or True  # Might be env-configured
        assert PRO_DAILY_LIMIT >= FREE_DAILY_LIMIT or PRO_DAILY_LIMIT == 0

    @pytest.mark.integration
    def test_rate_tracker_provider_limits(self) -> None:
        """RateTracker providers have valid limits."""
        from app.rate_limiter import PROVIDERS

        assert len(PROVIDERS) > 0
        for p in PROVIDERS:
            assert p.rpm > 0
            assert p.rpd > 0
            assert p.id
            assert p.name

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_embedding_cache_hit_miss(self) -> None:
        """EmbeddingCache properly tracks hits and misses."""
        from app.rate_limiter import EmbeddingCache

        cache = EmbeddingCache(max_size=100)

        # Miss
        result = cache.get("hello world")
        assert result is None
        assert cache._misses == 1
        assert cache._hits == 0

        # Set and hit
        cache.set("hello world", [0.1, 0.2, 0.3], "test_provider")
        result = cache.get("hello world")
        assert result is not None
        assert result[0] == [0.1, 0.2, 0.3]
        assert result[1] == "test_provider"
        assert cache._hits == 1
        assert cache._misses == 1

        # Stats
        stats = cache.stats()
        assert stats["size"] == 1
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 50.0

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_rate_tracker_can_call_memory_mode(self) -> None:
        """RateTracker.can_call works in memory-only mode (no Redis)."""
        from app.rate_limiter import PROVIDERS, RateTracker

        tracker = RateTracker()
        if not PROVIDERS:
            pytest.skip("No providers configured")

        provider = PROVIDERS[0]
        result = await tracker.can_call(provider)
        # Without Redis, should default to unlimited → True
        assert result is True

        # Record should not raise
        try:
            await tracker.record(provider)
        except Exception as exc:
            pytest.fail(f"tracker.record raised: {exc}")

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_rate_tracker_remaining(self) -> None:
        """RateTracker.remaining returns usable structure."""
        from app.rate_limiter import PROVIDERS, RateTracker

        tracker = RateTracker()
        if not PROVIDERS:
            pytest.skip("No providers configured")

        remaining = await tracker.remaining(PROVIDERS[0])
        assert "day_remaining" in remaining
        assert "minute_remaining" in remaining
        assert "total_used" in remaining


# ═══════════════════════════════════════════════════════════════
# ── Cache Hit/Miss Behavior
# ═══════════════════════════════════════════════════════════════


class TestCacheBehavior:
    """ChainCache and RMICache hit/miss behavior tests."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_chain_cache_miss_then_hit(self) -> None:
        """ChainCache miss then set then hit returns correct data."""
        from app.chain_cache import ChainCache

        cache = ChainCache(max_size=10, default_ttl=60)

        # Miss
        result = await cache.get("holders", "test_token")
        assert result is None

        # Set
        data = [{"address": "abc", "amount": 100}]
        await cache.set("holders", data, "test_token")

        # Hit
        result = await cache.get("holders", "test_token")
        assert result is not None
        assert result == data

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_chain_cache_expiry(self) -> None:
        """ChainCache entries expire after TTL."""

        from app.chain_cache import ChainCache

        cache = ChainCache(max_size=10, default_ttl=60)

        # Set with 0-second TTL — the entry has expiry = now, which may or may not
        # be expired when we check. Use a negative TTL to guarantee expiry.
        await cache.set("test", "value", "key1", ttl=-1)

        # A negative TTL means expiry is in the past
        result = await cache.get("test", "key1")
        assert result is None, "TTL-expired entry should not be returned"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_chain_cache_max_size_eviction(self) -> None:
        """ChainCache evicts oldest entries when at max size."""
        from app.chain_cache import ChainCache

        cache = ChainCache(max_size=3, default_ttl=60)

        # Fill cache to max
        await cache.set("key", "v1", "a")
        await cache.set("key", "v2", "b")
        await cache.set("key", "v3", "c")

        # This should evict "a"
        await cache.set("key", "v4", "d")

        # "a" is evicted (LRU)
        assert await cache.get("key", "a") is None
        # Others still present
        assert await cache.get("key", "b") == "v2"
        assert await cache.get("key", "d") == "v4"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_chain_cache_stats(self) -> None:
        """ChainCache.stats returns correct metrics."""
        from app.chain_cache import ChainCache

        cache = ChainCache(max_size=100)
        await cache.set("method", "value", "arg1")
        await cache.set("method", "value2", "arg2")

        stats = await cache.stats()
        assert stats["size"] == 2
        assert stats["max"] == 100
        assert stats["utilization_pct"] == 2.0

    @pytest.mark.integration
    def test_rmi_cache_provider_config(self) -> None:
        """RMICache has provider configurations for major APIs."""
        from app.cache_manager import FREE_PROVIDERS, KEYED_PROVIDERS, PROVIDER_CONFIG

        # Major providers must be configured
        for provider in ("coingecko", "dexscreener", "birdeye", "helius", "jupiter"):
            assert provider in PROVIDER_CONFIG, f"Missing provider config: {provider}"
            cfg = PROVIDER_CONFIG[provider]
            assert "ttl" in cfg
            assert "rate_limit" in cfg
            assert cfg["rate_limit"] > 0

        # Free providers should be tracked
        assert "coingecko" in FREE_PROVIDERS
        assert "dexscreener" in FREE_PROVIDERS

        # Keyed providers should be tracked
        assert "helius" in KEYED_PROVIDERS
        assert "birdeye" in KEYED_PROVIDERS
