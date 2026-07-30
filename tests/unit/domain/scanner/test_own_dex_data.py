"""Tests for own_dex_data — ClickHouse-powered DEX data.

Tests the caching, query formatting, fallback logic, and price computation
functions that replace DexScreener API calls.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.domains.scanner.engines.own_dex_data import (
    check_data_freshness,
    clear_cache,
    get_cache_stats,
    get_own_dex_data,
    get_token_price_simple,
)


class TestGetOwnDexData:
    """Tests for get_own_dex_data — the main API."""

    @pytest.mark.asyncio
    async def test_returns_dict_with_source_key(self):
        """get_own_dex_data should return a dict with 'source' and 'pairs' keys."""
        clear_cache()
        # Mock ClickHouse query to return empty (triggers graceful path)
        with patch(
            "app.domains.scanner.engines.own_dex_data.query._query_dex_swaps",
            AsyncMock(return_value=[]),
        ):
            result = await get_own_dex_data("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "ethereum")
        assert isinstance(result, dict)
        assert "source" in result
        assert result["source"] in ("own", "fallback", "unavailable")
        assert "pairs" in result
        assert isinstance(result["pairs"], list)

    @pytest.mark.asyncio
    async def test_returns_metrics(self):
        """get_own_dex_data should return metrics block."""
        with patch(
            "app.domains.scanner.engines.own_dex_data.query._query_dex_swaps",
            AsyncMock(return_value=[]),
        ):
            result = await get_own_dex_data("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "ethereum")
        assert "metrics" in result
        metrics = result["metrics"]
        assert "total_volume_24h" in metrics
        assert "swap_count_24h" in metrics
        assert "unique_pools" in metrics

    @pytest.mark.asyncio
    async def test_handles_unknown_token(self):
        """Should gracefully handle tokens with no DEX data."""
        with patch(
            "app.domains.scanner.engines.own_dex_data.query._query_dex_swaps",
            AsyncMock(return_value=[]),
        ):
            result = await get_own_dex_data(
                "0x0000000000000000000000000000000000000001",
                "ethereum",
            )
        assert result["source"] in ("own", "fallback", "unavailable")
        assert isinstance(result["pairs"], list)
        assert "metrics" in result

    def test_cache_starts_empty(self):
        """Cache should be empty after clear."""
        clear_cache()
        stats = get_cache_stats()
        assert stats["cache_size"] == 0

    def test_cache_stats_returns_expected_keys(self):
        """get_cache_stats should return descriptive stats."""
        stats = get_cache_stats()
        assert "cache_size" in stats
        assert "cache_max" in stats
        assert stats["cache_max"] > 0


class TestGetTokenPriceSimple:
    """Tests for get_token_price_simple — TWAP price extraction."""

    @pytest.mark.asyncio
    async def test_returns_price_dict(self):
        """get_token_price_simple should return a dict with price_usd."""
        with patch(
            "app.domains.scanner.engines.own_dex_data.query._query_dex_swaps",
            AsyncMock(return_value=[]),
        ):
            result = await get_token_price_simple(
                "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                "ethereum",
                lookback_minutes=60,
            )
        assert isinstance(result, dict)
        assert "price_usd" in result
        assert "confidence" in result
        assert "swap_count" in result
        assert "source" in result
        # With empty mock data, should get insufficient_data
        assert result["source"] == "insufficient_data"

    @pytest.mark.asyncio
    async def test_insufficient_data_returns_zero_price(self):
        """Tokens with no swaps should return 0 price and 0 confidence."""
        with patch(
            "app.domains.scanner.engines.own_dex_data.query._query_dex_swaps",
            AsyncMock(return_value=[]),
        ):
            result = await get_token_price_simple(
                "0x0000000000000000000000000000000000000001",
                "ethereum",
                lookback_minutes=5,
            )
        assert result["price_usd"] == 0.0
        assert result["confidence"] == 0.0
        assert result["source"] in ("insufficient_data", "no_valid_price")

    @pytest.mark.asyncio
    async def test_bnb_chain_variants(self):
        """Should handle BSC chain aliases."""
        with patch(
            "app.domains.scanner.engines.own_dex_data.query._query_dex_swaps",
            AsyncMock(return_value=[]),
        ):
            result = await get_token_price_simple(
                "0x0000000000000000000000000000000000000001",
                "bsc",
            )
        assert isinstance(result, dict)
        assert "price_usd" in result


class TestCheckDataFreshness:
    """Tests for check_data_freshness — data health monitoring."""

    @pytest.mark.asyncio
    async def test_returns_freshness_dict(self):
        """check_data_freshness should return a dict with health indicators."""
        with patch(
            "app.domains.scanner.engines.own_dex_data.query._get_ch_client",
            return_value=None,
        ):
            result = await check_data_freshness("ethereum")
        assert isinstance(result, dict)
        assert "healthy" in result
        assert result.get("healthy") is False  # No ClickHouse = unhealthy
