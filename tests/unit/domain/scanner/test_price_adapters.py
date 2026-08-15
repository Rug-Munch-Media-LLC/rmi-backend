"""Unit tests for app/domains/scanner/engines/_price_adapters.py — free API adapters.

Covers the pure pair-parsing helpers (price + decimals extraction), the
DexScreener tier (chain filtering, liquidity ranking, missing pairs), the
DeFiLlama / GeckoTerminal / ParaSwap / 1inch adapters (httpx mocked), the
CoinGecko tier (databus provider mocked), and native-coin fallbacks. No
network access.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from app.domains.scanner.engines._price_adapters import (
    _CHAIN_MAP,
    _coingecko_price,
    _decimals_from_dexscreener,
    _defillama_price,
    _dexscreener_price,
    _geckoterminal_price,
    _native_coingecko,
    _native_defillama,
    _oneinch_price,
    _pair_decimals_for_token,
    _pair_price_for_token,
    _paraswap_price,
)


def _pair(
    base_addr: str, quote_addr: str, price_usd: float, price_native: float, base_decimals=18, quote_decimals=6
) -> dict:
    return {
        "baseToken": {"address": base_addr, "decimals": base_decimals, "symbol": "B"},
        "quoteToken": {"address": quote_addr, "decimals": quote_decimals, "symbol": "Q"},
        "priceUsd": str(price_usd),
        "priceNative": str(price_native),
    }


class TestChainMap:
    def test_chain_normalization(self) -> None:
        assert _CHAIN_MAP["eth"] == "ethereum"
        assert _CHAIN_MAP["mainnet"] == "ethereum"
        assert _CHAIN_MAP["bnb"] == "bsc"
        assert _CHAIN_MAP["matic"] == "polygon"
        assert _CHAIN_MAP["op"] == "optimism"
        assert _CHAIN_MAP["avax"] == "avalanche"
        assert _CHAIN_MAP["ftm"] == "fantom"


class TestPairPriceForToken:
    def test_base_token_price(self) -> None:
        pair = _pair("0xbase", "0xquote", 2.5, 1.0)
        assert _pair_price_for_token(pair, "0xbase") == 2.5

    def test_quote_token_price_derived(self) -> None:
        pair = _pair("0xbase", "0xquote", 2.0, 4.0)  # 1 base = $2 = 4 quote → 1 quote = $0.5
        assert _pair_price_for_token(pair, "0xquote") == 0.5

    def test_unknown_token_zero(self) -> None:
        pair = _pair("0xbase", "0xquote", 2.0, 4.0)
        assert _pair_price_for_token(pair, "0xother") == 0.0

    def test_missing_pair_sides_zero(self) -> None:
        assert _pair_price_for_token({}, "0xbase") == 0.0

    def test_non_numeric_price_zero(self) -> None:
        pair = {"baseToken": {"address": "0xbase"}, "quoteToken": {"address": "0xquote"}, "priceUsd": "not-a-number"}
        assert _pair_price_for_token(pair, "0xbase") == 0.0

    def test_case_insensitive_match(self) -> None:
        pair = _pair("0xABC", "0xquote", 3.0, 1.0)
        assert _pair_price_for_token(pair, "0xabc") == 3.0


class TestPairDecimalsForToken:
    def test_base_decimals(self) -> None:
        pair = _pair("0xbase", "0xquote", 1.0, 1.0, base_decimals=18)
        assert _pair_decimals_for_token(pair, "0xbase") == 18

    def test_quote_decimals(self) -> None:
        pair = _pair("0xbase", "0xquote", 1.0, 1.0, quote_decimals=6)
        assert _pair_decimals_for_token(pair, "0xquote") == 6

    def test_missing_returns_none(self) -> None:
        pair = {
            "baseToken": {"address": "0xbase"},
            "quoteToken": {"address": "0xquote"},
        }
        assert _pair_decimals_for_token(pair, "0xbase") is None

    def test_invalid_decimals_returns_none(self) -> None:
        pair = {"baseToken": {"address": "0xbase", "decimals": "x"}, "quoteToken": {}}
        assert _pair_decimals_for_token(pair, "0xbase") is None


class TestDexscreenerPrice:
    @pytest.mark.asyncio
    async def test_best_liquidity_pair_selected(self) -> None:
        async def fake_ds(mint: str, token: str):
            return {
                "pairs": [
                    {
                        **_pair("0xbase", "0xquote", 1.0, 1.0),
                        "chainId": "ethereum",
                        "liquidity": {"usd": 1000},
                        "volume": {"h24": 10},
                        "fdv": 5000,
                        "priceChange": {"h24": 1.0},
                    },
                    {
                        **_pair("0xbase", "0xquote", 1.5, 1.0),
                        "chainId": "ethereum",
                        "liquidity": {"usd": 50000},
                        "volume": {"h24": 20},
                        "fdv": 6000,
                        "priceChange": {"h24": 2.0},
                    },
                ]
            }

        with patch(
            "app.domains.databus.providers.chains.dexscreener._dexscreener_price",
            new=fake_ds,
        ):
            result = await _dexscreener_price("0xbase", "ethereum")
        assert result is not None
        assert result["price_usd"] == 1.5  # higher-liquidity pair wins
        assert result["confidence"] == 0.7
        assert result["symbol"] == "B"

    @pytest.mark.asyncio
    async def test_wrong_chain_pair_filtered(self) -> None:
        async def fake_ds(mint: str, token: str):
            return {
                "pairs": [
                    {
                        **_pair("0xbase", "0xquote", 9.0, 1.0),
                        "chainId": "pulsechain",
                        "liquidity": {"usd": 999999},
                        "volume": {"h24": 0},
                        "fdv": 0,
                        "priceChange": {"h24": 0},
                    },
                ]
            }

        with patch(
            "app.domains.databus.providers.chains.dexscreener._dexscreener_price",
            new=fake_ds,
        ):
            result = await _dexscreener_price("0xbase", "ethereum")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_pairs_returns_none(self) -> None:
        async def fake_ds(mint: str, token: str):
            return {"pairs": []}

        with patch("app.domains.databus.providers.chains.dexscreener._dexscreener_price", new=fake_ds):
            assert await _dexscreener_price("0xbase", "ethereum") is None

    @pytest.mark.asyncio
    async def test_provider_exception_returns_none(self) -> None:
        async def fake_ds(mint: str, token: str):
            raise httpx.ConnectError("down")

        with patch("app.domains.databus.providers.chains.dexscreener._dexscreener_price", new=fake_ds):
            assert await _dexscreener_price("0xbase", "ethereum") is None

    @pytest.mark.asyncio
    async def test_zero_price_returns_none(self) -> None:
        async def fake_ds(mint: str, token: str):
            return {
                "pairs": [
                    {
                        **_pair("0xbase", "0xother", 0.0, 1.0),
                        "chainId": "ethereum",
                        "liquidity": {"usd": 5000},
                        "volume": {"h24": 0},
                        "fdv": 0,
                        "priceChange": {"h24": 0},
                    },
                ]
            }

        with patch("app.domains.databus.providers.chains.dexscreener._dexscreener_price", new=fake_ds):
            result = await _dexscreener_price("0xbase", "ethereum")
        assert result is None


class TestDecimalsFromDexscreener:
    @pytest.mark.asyncio
    async def test_decimals_extracted(self) -> None:
        with patch(
            "app.domains.scanner.engines._price_adapters._dexscreener_price",
            new=AsyncMock(return_value={"price_usd": 1.0, "decimals": 18}),
        ):
            assert await _decimals_from_dexscreener("0xbase", "ethereum") == 18

    @pytest.mark.asyncio
    async def test_missing_decimals_returns_none(self) -> None:
        with patch(
            "app.domains.scanner.engines._price_adapters._dexscreener_price",
            new=AsyncMock(return_value={"price_usd": 1.0}),
        ):
            assert await _decimals_from_dexscreener("0xbase", "ethereum") is None


class TestCoingeckoPrice:
    @pytest.mark.asyncio
    async def test_databus_provider_result(self) -> None:
        async def fake_cg(token_address: str, chain: str, address: str):
            return {"price_usd": 100.0}

        with patch(
            "app.domains.databus.providers.chains.free_security._free_market_coingecko",
            new=fake_cg,
        ):
            result = await _coingecko_price("0xabc", "ethereum")
        assert result == {"price_usd": 100.0}

    @pytest.mark.asyncio
    async def test_databus_exception_returns_none(self) -> None:
        async def fake_cg(token_address: str, chain: str, address: str):
            raise TimeoutError("slow")

        with patch(
            "app.domains.databus.providers.chains.free_security._free_market_coingecko",
            new=fake_cg,
        ):
            assert await _coingecko_price("0xabc", "ethereum") is None


class TestDefillamaPrice:
    @pytest.mark.asyncio
    async def test_known_prefix_success(self) -> None:
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json = Mock(return_value={"coins": {"ethereum:0xabc": {"price": 1.2, "symbol": "T"}}})
        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
            result = await _defillama_price("0xabc", "ethereum")
        assert result is not None
        assert result["price_usd"] == 1.2

    @pytest.mark.asyncio
    async def test_unknown_chain_returns_none(self) -> None:
        assert await _defillama_price("0xabc", "notachain") is None

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self) -> None:
        with patch(
            "httpx.AsyncClient.get",
            new=AsyncMock(side_effect=httpx.ConnectError("down")),
        ):
            assert await _defillama_price("0xabc", "ethereum") is None

    @pytest.mark.asyncio
    async def test_non_200_returns_none(self) -> None:
        mock_resp = Mock(status_code=500)
        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
            assert await _defillama_price("0xabc", "ethereum") is None


class TestGeckoterminalAndSwaps:
    @pytest.mark.asyncio
    async def test_geckoterminal_http_error_returns_none(self) -> None:
        with patch(
            "httpx.AsyncClient.get",
            new=AsyncMock(side_effect=httpx.ConnectError("down")),
        ):
            assert await _geckoterminal_price("0xabc", "ethereum") is None

    @pytest.mark.asyncio
    async def test_paraswap_http_error_returns_none(self) -> None:
        with (
            patch(
                "app.domains.scanner.engines.price_oracle.get_native_price",
                new=AsyncMock(return_value=2000.0),
            ),
            patch(
                "httpx.AsyncClient.get",
                new=AsyncMock(side_effect=httpx.ConnectError("down")),
            ),
        ):
            assert await _paraswap_price("0xabc", "ethereum") is None

    @pytest.mark.asyncio
    async def test_oneinch_http_error_returns_none(self, monkeypatch) -> None:
        monkeypatch.setenv("ONEINCH_API_KEY", "test-key")

        async def fake_quote(token_address: str, chain: str, address: str, api_key: str):
            raise TimeoutError("slow")

        with patch(
            "app.domains.databus.providers.chains.free_security._free_market_oneinch_quote",
            new=fake_quote,
        ):
            assert await _oneinch_price("0xabc", "ethereum") is None


class TestNativeFallbacks:
    @pytest.mark.asyncio
    async def test_native_coingecko_success(self) -> None:
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json = Mock(return_value={"ethereum": {"usd": 3000.0}})
        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
            assert await _native_coingecko("ethereum") == 3000.0

    @pytest.mark.asyncio
    async def test_native_coingecko_error_returns_zero(self) -> None:
        with patch(
            "httpx.AsyncClient.get",
            new=AsyncMock(side_effect=httpx.ConnectError("down")),
        ):
            assert await _native_coingecko("ethereum") == 0.0

    @pytest.mark.asyncio
    async def test_native_defillama_success(self) -> None:
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json = Mock(return_value={"coins": {"ethereum": {"price": 3100.0}}})
        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
            assert await _native_defillama("ethereum") == 3100.0

    @pytest.mark.asyncio
    async def test_native_defillama_error_returns_zero(self) -> None:
        with patch(
            "httpx.AsyncClient.get",
            new=AsyncMock(side_effect=httpx.ConnectError("down")),
        ):
            assert await _native_defillama("ethereum") == 0.0
