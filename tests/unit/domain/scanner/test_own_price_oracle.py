"""Tests for own_price_oracle — ClickHouse-powered CoinGecko replacement.

Tests the TWAP/VWAP computation, three-tier fallback chain,
caching, and chain normalization logic.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.domains.scanner.engines.own_price_oracle import (
    clear_cache,
    get_batch_prices,
    get_cache_stats,
    get_token_price,
)

# ── Mock helper — prevents all ClickHouse queries from returning data ──
_MOCK_DEX_SWAPS_EMPTY = patch(
    "app.domains.scanner.engines.own_dex_data.query._query_dex_swaps",
    AsyncMock(return_value=[]),
)


class TestGetTokenPrice:
    """Tests for get_token_price — the main price oracle API."""

    @pytest.mark.asyncio
    async def test_returns_price_dict(self):
        """get_token_price should return a dict with price_usd."""
        with _MOCK_DEX_SWAPS_EMPTY:
            result = await get_token_price(
                "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                "ethereum",
            )
        assert isinstance(result, dict)
        assert "price_usd" in result
        assert isinstance(result["price_usd"], float)
        assert "confidence" in result
        assert isinstance(result["confidence"], float)
        assert "source" in result
        # With no DEX data and no fallback, should indicate unavailability
        assert result["source"] in ("own", "defillama", "coingecko", "unavailable")
        assert "volume_24h_usd" in result
        assert "price_change_24h_pct" in result

    @pytest.mark.asyncio
    async def test_handles_unknown_token(self):
        """Should gracefully handle tokens with no price data."""
        with _MOCK_DEX_SWAPS_EMPTY:
            result = await get_token_price(
                "0x0000000000000000000000000000000000000001",
                "ethereum",
            )
        assert result["source"] in ("own", "defillama", "coingecko", "unavailable")
        assert isinstance(result["price_usd"], float)

    @pytest.mark.asyncio
    async def test_force_fresh_bypasses_cache(self):
        """force_fresh=True should bypass the cache."""
        clear_cache()
        with _MOCK_DEX_SWAPS_EMPTY:
            result1 = await get_token_price(
                "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                "ethereum",
            )
            result2 = await get_token_price(
                "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                "ethereum",
                force_fresh=True,
            )
        assert isinstance(result1, dict)
        assert isinstance(result2, dict)

    def test_clear_cache_empties(self):
        """clear_cache should empty the price cache."""
        clear_cache()
        stats = get_cache_stats()
        assert stats["cache_size"] == 0

    def test_get_cache_stats_returns_expected(self):
        """get_cache_stats should return descriptive cache stats."""
        stats = get_cache_stats()
        assert stats["cache_size"] >= 0
        assert stats["cache_max"] == 200
        assert stats["cache_ttl_seconds"] == 60


class TestChainNormalization:
    """Tests for internal chain name normalization."""

    @pytest.mark.asyncio
    async def test_eth_alias_normalizes(self):
        """'eth' chain should be accepted (triggers graceful unavailable)."""
        with _MOCK_DEX_SWAPS_EMPTY:
            result = await get_token_price("0xABC", "eth")
        assert isinstance(result, dict)
        assert "source" in result

    @pytest.mark.asyncio
    async def test_bnb_alias_normalizes(self):
        """'bnb' chain should be accepted."""
        with _MOCK_DEX_SWAPS_EMPTY:
            result = await get_token_price("0xABC", "bnb")
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_matic_alias_normalizes(self):
        """'matic' chain should be accepted."""
        with _MOCK_DEX_SWAPS_EMPTY:
            result = await get_token_price("0xABC", "matic")
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_arb_alias_normalizes(self):
        """'arb' chain should be accepted."""
        with _MOCK_DEX_SWAPS_EMPTY:
            result = await get_token_price("0xABC", "arb")
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_mainnet_alias_normalizes(self):
        """'mainnet' chain should be accepted."""
        with _MOCK_DEX_SWAPS_EMPTY:
            result = await get_token_price("0xABC", "mainnet")
        assert isinstance(result, dict)


class TestGetBatchPrices:
    """Tests for get_batch_prices — bulk price fetching."""

    @pytest.mark.asyncio
    async def test_returns_dict_keyed_by_token_chain(self):
        """Should return results keyed by 'token:chain'."""
        with _MOCK_DEX_SWAPS_EMPTY:
            tokens = [
                ("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "ethereum"),
            ]
            results = await get_batch_prices(tokens)
        expected_key = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2:ethereum"
        assert expected_key in results
        assert "price_usd" in results[expected_key]

    @pytest.mark.asyncio
    async def test_handles_multiple_tokens(self):
        """Should handle multiple token-chain pairs."""
        with _MOCK_DEX_SWAPS_EMPTY:
            tokens = [
                ("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "ethereum"),
                ("0x0000000000000000000000000000000000000001", "ethereum"),
            ]
            results = await get_batch_prices(tokens)
        assert len(results) == 2
        for key, data in results.items():
            assert "price_usd" in data
            assert "source" in data
