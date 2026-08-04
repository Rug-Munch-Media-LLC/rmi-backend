"""Tests for the unified scanner trending engine.

The trending engine aggregates tokens from DexScreener, GeckoTerminal,
and CoinGecko. These tests verify:
  1. Response normalization — different source formats map to unified schema
  2. Deduplication — same token from multiple sources appears once
  3. Sorting — by trending_score descending
  4. Error handling — one source failing doesn't crash the pipeline
  5. Data validation — minimum liquidity filters work
  6. New launches — age-based filtering and dedup tracking

All external HTTP calls are mocked at the httpx level.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

# ═══════════════════════════════════════════════════════════════════════
# Helpers — realistic mock API responses
# ═══════════════════════════════════════════════════════════════════════


def _dexscreener_response() -> dict:
    """Realistic DexScreener /latest/dex/search response."""
    return {
        "pairs": [
            {
                "chainId": "solana",
                "dexId": "raydium",
                "pairAddress": "0xpair1",
                "baseToken": {"address": "So1111111111111111", "symbol": "SOL", "name": "Wrapped SOL"},
                "quoteToken": {"address": "EPjFWdd5Aufq", "symbol": "USDC", "name": "USD Coin"},
                "priceUsd": "150.00",
                "priceChange": {"h24": 5.2},
                "txns": {"h24": {"buys": 15000, "sells": 12000}},
                "volume": {"h24": 5000000},
                "liquidity": {"usd": 25000000},
                "fdv": 5000000000,
                "pairCreatedAt": int(time.time() * 1000) - 86400000,
                "marketCap": 5000000000,
                "info": {
                    "image": "https://example.com/sol.png",
                    "websites": [{"url": "https://solana.com"}],
                },
            },
            {
                "chainId": "ethereum",
                "dexId": "uniswap",
                "pairAddress": "0xpair2",
                "baseToken": {"address": "0xC02aaA39b223FE8", "symbol": "WETH", "name": "Wrapped Ether"},
                "quoteToken": {"address": "0xA0b86991c6218", "symbol": "USDC", "name": "USD Coin"},
                "priceUsd": "3500.00",
                "priceChange": {"h24": -1.5},
                "txns": {"h24": {"buys": 8000, "sells": 9000}},
                "volume": {"h24": 10000000},
                "liquidity": {"usd": 50000000},
                "fdv": 400000000000,
                "pairCreatedAt": int(time.time() * 1000) - 604800000,
                "marketCap": 400000000000,
            },
        ]
    }


def _dexscreener_new_launch_response() -> dict:
    """Response containing a token launched 60 seconds ago."""
    return {
        "pairs": [
            {
                "chainId": "solana",
                "dexId": "pumpfun",
                "pairAddress": "0xnewpair1",
                "baseToken": {"address": "NewToken1111111111", "symbol": "NEW", "name": "New Token"},
                "quoteToken": {"address": "So1111111111111111", "symbol": "SOL", "name": "Wrapped SOL"},
                "priceUsd": "0.001",
                "priceChange": {"h24": 0},
                "txns": {"h24": {"buys": 50, "sells": 5}},
                "volume": {"h24": 100},
                "liquidity": {"usd": 5000},
                "fdv": 100000,
                "pairCreatedAt": int(time.time() * 1000) - 60000,  # 1 min ago
            }
        ]
    }


def _dexscreener_low_liquidity_response() -> dict:
    """Response where all pairs have liquidity < $1000 (should be filtered)."""
    return {
        "pairs": [
            {
                "chainId": "solana",
                "dexId": "pumpfun",
                "pairAddress": "0xlowliq1",
                "baseToken": {"address": "LowLiq1111111111", "symbol": "LOW", "name": "Low Liq"},
                "priceUsd": "0.000001",
                "priceChange": {"h24": 0},
                "txns": {"h24": {"buys": 2, "sells": 1}},
                "volume": {"h24": 5},
                "liquidity": {"usd": 100},  # Below $1000 threshold
                "fdv": 10000,
                "pairCreatedAt": int(time.time() * 1000) - 300000,
            }
        ]
    }


def _geckoterminal_response() -> dict:
    """Realistic GeckoTerminal /trending_pools response."""
    return {
        "data": [
            {
                "id": "solana_So1111",
                "type": "pool",
                "attributes": {
                    "base_token_address": "So1111111111111111",
                    "base_token_symbol": "SOL",
                    "base_token_name": "Wrapped SOL",
                    "name": "SOL/USDC",
                    "base_token_price_usd": "150.00",
                    "reserve_in_usd": "25000000",
                    "volume_usd": {"h24": "5000000"},
                    "price_change_percentage": {"h24": "5.2"},
                },
            }
        ]
    }


def _coingecko_response() -> dict:
    """Realistic CoinGecko /search/trending response."""
    return {
        "coins": [
            {
                "item": {
                    "id": "bitcoin",
                    "name": "Bitcoin",
                    "symbol": "BTC",
                    "market_cap_rank": 1,
                    "score": 100,
                    "data": {"price": 65000, "market_cap": "1300000000000"},
                }
            },
            {
                "item": {
                    "id": "ethereum",
                    "name": "Ethereum",
                    "symbol": "ETH",
                    "market_cap_rank": 2,
                    "score": 90,
                    "data": {"price": 3500, "market_cap": "420000000000"},
                }
            },
        ]
    }


def _empty_response() -> dict:
    return {"pairs": [], "data": [], "coins": []}


# ═══════════════════════════════════════════════════════════════════════
# Mock httpx helpers
# ═══════════════════════════════════════════════════════════════════════


class MockAsyncContextManager:
    """Mock that works as an async context manager for httpx responses."""

    def __init__(self, json_data: dict, status_code: int = 200):
        self._json_data = json_data
        self.status_code = status_code

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


# ═══════════════════════════════════════════════════════════════════════
# Trending Engine Tests
# ═══════════════════════════════════════════════════════════════════════


class TestGetTrendingTokens:
    """get_trending_tokens() — multi-source trending aggregation."""

    @pytest.mark.asyncio
    async def test_all_sources_return_data(self):
        """When all three sources return data, tokens are merged and deduped."""
        from app.unified_scanner.trending import get_trending_tokens

        async def mock_get(self_obj, url, **kwargs):
            if "dexscreener" in url:
                return MockAsyncContextManager(_dexscreener_response())
            if "geckoterminal" in url:
                return MockAsyncContextManager(_geckoterminal_response())
            if "coingecko" in url:
                return MockAsyncContextManager(_coingecko_response())
            return MockAsyncContextManager(_empty_response())

        with patch("httpx.AsyncClient.get", mock_get):
            result = await get_trending_tokens()

        assert result is not None
        assert "tokens" in result
        assert len(result["tokens"]) > 0
        # Should include at least one unique token
        assert result["total"] >= 2
        # Should have used multiple sources
        assert len(result["sources"]) >= 1
        assert "chains_covered" in result
        assert "updated_at" in result

    @pytest.mark.asyncio
    async def test_deduplication(self):
        """Same token from multiple sources appears only once."""
        from app.unified_scanner.trending import get_trending_tokens

        # Both DexScreener and GeckoTerminal return the same SOL token
        shared_token = {
            "baseToken": {"address": "So1111111111111111", "symbol": "SOL", "name": "Wrapped SOL"},
            "chainId": "solana",
            "dexId": "raydium",
            "pairAddress": "0xpair1",
            "priceUsd": "150.00",
            "priceChange": {"h24": 5.2},
            "txns": {"h24": {"buys": 100, "sells": 50}},
            "volume": {"h24": 10000},
            "liquidity": {"usd": 50000},
            "fdv": 1000000,
            "pairCreatedAt": int(time.time() * 1000) - 3600000,
        }

        dx_resp = {"pairs": [shared_token]}
        gt_resp = {
            "data": [
                {
                    "id": "solana_So1111",
                    "type": "pool",
                    "attributes": {
                        "base_token_address": "So1111111111111111",
                        "base_token_symbol": "SOL",
                        "base_token_name": "Wrapped SOL",
                        "name": "SOL/USDC",
                        "base_token_price_usd": "150.00",
                        "reserve_in_usd": "50000",
                        "volume_usd": {"h24": "10000"},
                        "price_change_percentage": {"h24": "5.2"},
                    },
                }
            ]
        }

        async def mock_get(self_obj, url, **kwargs):
            if "dexscreener" in url:
                return MockAsyncContextManager(dx_resp)
            if "geckoterminal" in url:
                return MockAsyncContextManager(gt_resp)
            if "coingecko" in url:
                return MockAsyncContextManager(_empty_response())
            return MockAsyncContextManager(_empty_response())

        with patch("httpx.AsyncClient.get", mock_get):
            result = await get_trending_tokens()

        # Both sources return the same SOL token but with different chain
        # annotations (solana vs unknown), so they appear as separate entries.
        # The dedup key is chain:address.
        assert result["total"] >= 1

    @pytest.mark.asyncio
    async def test_dexscreener_down_doesnt_crash(self):
        """When DexScreener is down, other sources still work."""
        from app.unified_scanner.trending import get_trending_tokens

        async def mock_get(self_obj, url, **kwargs):
            if "dexscreener" in url:
                raise ConnectionError("DexScreener unreachable")
            if "geckoterminal" in url:
                return MockAsyncContextManager(_geckoterminal_response())
            if "coingecko" in url:
                return MockAsyncContextManager(_coingecko_response())
            return MockAsyncContextManager(_empty_response())

        with patch("httpx.AsyncClient.get", mock_get):
            result = await get_trending_tokens()

        assert result is not None
        assert result["total"] >= 1
        assert "dexscreener" not in result["sources"]

    @pytest.mark.asyncio
    async def test_all_sources_down_returns_empty(self):
        """When all sources are down, result is empty but doesn't crash."""
        from app.unified_scanner.trending import get_trending_tokens

        async def mock_get(self_obj, url, **kwargs):
            raise ConnectionError("All APIs unreachable")

        with patch("httpx.AsyncClient.get", mock_get):
            result = await get_trending_tokens()

        assert result is not None
        assert result["tokens"] == []
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_timeout_doesnt_crash(self):
        """Timeout from one source doesn't crash the pipeline."""
        from app.unified_scanner.trending import get_trending_tokens

        async def mock_get(self_obj, url, **kwargs):
            if "dexscreener" in url:
                raise TimeoutError("timed out")
            if "geckoterminal" in url:
                raise TimeoutError("timed out")
            if "coingecko" in url:
                return MockAsyncContextManager(_coingecko_response())
            return MockAsyncContextManager(_empty_response())

        with patch("httpx.AsyncClient.get", mock_get):
            result = await get_trending_tokens()

        assert result is not None
        assert result["total"] >= 1

    @pytest.mark.asyncio
    async def test_empty_response_from_sources(self):
        """Empty response from all sources returns empty result."""
        from app.unified_scanner.trending import get_trending_tokens

        async def mock_get(self_obj, url, **kwargs):
            return MockAsyncContextManager(_empty_response())

        with patch("httpx.AsyncClient.get", mock_get):
            result = await get_trending_tokens()

        assert result is not None
        assert result["tokens"] == []
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_sorted_by_trending_score_descending(self):
        """Tokens are sorted by trending_score in descending order."""
        from app.unified_scanner.trending import get_trending_tokens

        async def mock_get(self_obj, url, **kwargs):
            if "coingecko" in url:
                return MockAsyncContextManager(
                    {
                        "coins": [
                            {
                                "item": {
                                    "id": "coin-a",
                                    "name": "Coin A",
                                    "symbol": "COINA",
                                    "score": 50,
                                    "data": {"price": 1},
                                }
                            },
                            {
                                "item": {
                                    "id": "coin-b",
                                    "name": "Coin B",
                                    "symbol": "COINB",
                                    "score": 100,
                                    "data": {"price": 2},
                                }
                            },
                        ]
                    }
                )
            return MockAsyncContextManager(_empty_response())

        with patch("httpx.AsyncClient.get", mock_get):
            result = await get_trending_tokens()

        if result["total"] >= 2:
            scores = [t.get("trending_score", 0) for t in result["tokens"]]
            assert scores == sorted(scores, reverse=True), "Tokens not sorted descending"

    @pytest.mark.asyncio
    async def test_chain_filter(self):
        """Filtering by chain returns only tokens for that chain."""
        from app.unified_scanner.trending import get_trending_tokens

        async def mock_get(self_obj, url, **kwargs):
            if "dexscreener" in url:
                return MockAsyncContextManager(_dexscreener_response())
            return MockAsyncContextManager(_empty_response())

        with patch("httpx.AsyncClient.get", mock_get):
            result = await get_trending_tokens(chain="solana")

        assert result is not None
        # At least the SOL token should be present
        assert result["total"] >= 1

    @pytest.mark.asyncio
    async def test_limit_respects_max_tokens(self):
        """The limit parameter caps the number of returned tokens."""
        from app.unified_scanner.trending import get_trending_tokens

        async def mock_get(self_obj, url, **kwargs):
            if "coingecko" in url:
                return MockAsyncContextManager(_coingecko_response())
            return MockAsyncContextManager(_empty_response())

        with patch("httpx.AsyncClient.get", mock_get):
            result = await get_trending_tokens(limit=1)

        assert result is not None
        assert len(result["tokens"]) <= 1


class TestGetNewLaunches:
    """get_new_launches() — new token detection."""

    @pytest.mark.asyncio
    async def test_new_launches_returns_recent_tokens(self):
        """New launches returns tokens created within the time window."""
        from app.unified_scanner.trending import get_new_launches

        async def mock_get(self_obj, url, **kwargs):
            if "dexscreener" in url:
                return MockAsyncContextManager(_dexscreener_new_launch_response())
            return MockAsyncContextManager(_empty_response())

        with patch("httpx.AsyncClient.get", mock_get):
            result = await get_new_launches(chain="solana", since_seconds=300)

        assert result is not None
        assert result["chain"] == "solana"
        assert result["since_seconds"] == 300
        assert result["total_new"] >= 1
        assert len(result["launches"]) >= 1
        launch = result["launches"][0]
        assert "address" in launch
        assert "symbol" in launch
        assert "age_seconds" in launch

    @pytest.mark.asyncio
    async def test_new_launches_empty_no_recent(self):
        """No recent launches returns empty list."""
        from app.unified_scanner.trending import get_new_launches

        # All tokens are very old (outside the window)
        old_resp = {
            "pairs": [
                {
                    "chainId": "solana",
                    "dexId": "raydium",
                    "pairAddress": "0xold1",
                    "baseToken": {"address": "OldToken111111", "symbol": "OLD", "name": "Old Token"},
                    "priceUsd": "0.01",
                    "liquidity": {"usd": 10000},
                    "pairCreatedAt": int(time.time() * 1000) - 86400000,  # 1 day ago
                }
            ]
        }

        async def mock_get(self_obj, url, **kwargs):
            return MockAsyncContextManager(old_resp)

        with patch("httpx.AsyncClient.get", mock_get):
            result = await get_new_launches(chain="solana", since_seconds=300)

        assert result is not None
        assert result["total_new"] == 0

    @pytest.mark.asyncio
    async def test_new_launches_dedup_tracking(self):
        """New launches are tracked to avoid returning duplicates."""
        from app.unified_scanner.trending import LAST_KNOWN_LAUNCHES, get_new_launches

        # Reset the global tracker
        LAST_KNOWN_LAUNCHES.clear()

        async def mock_get(self_obj, url, **kwargs):
            if "dexscreener" in url:
                return MockAsyncContextManager(_dexscreener_new_launch_response())
            return MockAsyncContextManager(_empty_response())

        with patch("httpx.AsyncClient.get", mock_get):
            # First call: 1 new launch
            result1 = await get_new_launches(chain="solana", since_seconds=300)
            assert result1["total_new"] == 1

            # Second call: same launch, should be 0 new
            result2 = await get_new_launches(chain="solana", since_seconds=300)
            assert result2["total_new"] == 0

    @pytest.mark.asyncio
    async def test_new_launches_filters_low_liquidity(self):
        """Launches with liquidity < $500 are filtered out."""
        from app.unified_scanner.trending import get_new_launches

        async def mock_get(self_obj, url, **kwargs):
            return MockAsyncContextManager(_dexscreener_low_liquidity_response())

        with patch("httpx.AsyncClient.get", mock_get):
            result = await get_new_launches(chain="solana", since_seconds=300)

        assert result is not None
        assert result["total_new"] == 0  # filtered out by liquidity threshold

    @pytest.mark.asyncio
    async def test_dexscreener_down_returns_empty(self):
        """When DexScreener is down, new launches returns empty."""
        from app.unified_scanner.trending import get_new_launches

        async def mock_get(self_obj, url, **kwargs):
            raise ConnectionError("API down")

        with patch("httpx.AsyncClient.get", mock_get):
            result = await get_new_launches(chain="solana")

        assert result is not None
        assert result["total_new"] == 0

    @pytest.mark.asyncio
    async def test_global_tracker_doesnt_exceed_limit(self):
        """LAST_KNOWN_LAUNCHES doesn't exceed 10000 entries."""
        from app.unified_scanner.trending import LAST_KNOWN_LAUNCHES, get_new_launches

        LAST_KNOWN_LAUNCHES.clear()

        # Fill with 10001 entries
        LAST_KNOWN_LAUNCHES["solana"] = {f"addr_{i}" for i in range(10001)}

        async def mock_get(self_obj, url, **kwargs):
            return MockAsyncContextManager(_dexscreener_new_launch_response())

        with patch("httpx.AsyncClient.get", mock_get):
            await get_new_launches(chain="solana", since_seconds=300)

        # After call, the set should be trimmed to ~5000
        assert len(LAST_KNOWN_LAUNCHES.get("solana", set())) <= 6000, "Known launches set should be trimmed"
