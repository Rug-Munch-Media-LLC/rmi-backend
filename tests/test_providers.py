"""Test DataBus provider edge cases — empty inputs, invalid formats, errors,
timeout handling, and provider-specific behavior.

All external APIs are mocked; no real network calls.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def mock_httpx_client():
    """Yield a mock that patches ``httpx.AsyncClient`` in a provider module."""
    with patch("app.databus.provider_implementations.price.httpx.AsyncClient") as m:
        yield m


@pytest.fixture
def mock_free_httpx():
    """Patch httpx.AsyncClient for free_data module."""
    with patch("app.databus.provider_implementations.free_data.httpx.AsyncClient") as m:
        yield m


@pytest.fixture
def mock_arkham_httpx():
    """Patch httpx.AsyncClient for arkham module."""
    with patch("app.databus.provider_implementations.arkham.httpx.AsyncClient") as m:
        yield m


@pytest.fixture
def mock_moralis_httpx():
    """Patch httpx.AsyncClient for moralis module."""
    with patch("app.databus.provider_implementations.moralis.httpx.AsyncClient") as m:
        yield m


@pytest.fixture
def mock_alchemy_httpx():
    """Patch httpx.AsyncClient for alchemy module."""
    with patch("app.databus.provider_implementations.alchemy.httpx.AsyncClient") as m:
        yield m


# ═══════════════════════════════════════════════════════════════════════
# 1. Price providers
# ═══════════════════════════════════════════════════════════════════════


class TestPriceProviders:
    """Edge cases for price-provider functions."""

    @pytest.mark.asyncio
    async def test_dexscreener_price_empty_token(self):
        """Empty token returns None without making HTTP calls."""
        from app.databus.provider_implementations.price import _dexscreener_price

        with patch("app.databus.provider_implementations.price.httpx.AsyncClient") as mock_http:
            result = await _dexscreener_price(token="")
            assert result is None
            mock_http.assert_not_called()

    @pytest.mark.asyncio
    async def test_dexscreener_price_no_kwargs(self):
        """No kwargs returns None."""
        from app.databus.provider_implementations.price import _dexscreener_price

        with patch("app.databus.provider_implementations.price.httpx.AsyncClient") as mock_http:
            result = await _dexscreener_price()
            assert result is None
            mock_http.assert_not_called()

    @pytest.mark.asyncio
    async def test_dexscreener_price_success(self, mock_httpx_client):
        """Successful 200 response returns JSON."""
        from app.databus.provider_implementations.price import _dexscreener_price

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"pairs": [{"priceUsd": "1.23"}]}
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_httpx_client.return_value.__aenter__.return_value = mock_client

        result = await _dexscreener_price(token="So11111111111111111111111111111111111111112")
        assert result is not None
        assert result["pairs"][0]["priceUsd"] == "1.23"

    @pytest.mark.asyncio
    async def test_dexscreener_price_http_error(self, mock_httpx_client):
        """Non-200 response returns None."""
        from app.databus.provider_implementations.price import _dexscreener_price

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_httpx_client.return_value.__aenter__.return_value = mock_client

        result = await _dexscreener_price(token="So11111111111111111111111111111111111111112")
        assert result is None

    @pytest.mark.asyncio
    async def test_dexscreener_price_timeout(self, mock_httpx_client):
        """Timeout exception returns None (graceful degradation)."""
        from app.databus.provider_implementations.price import _dexscreener_price

        mock_client = AsyncMock()
        mock_client.get.side_effect = TimeoutError("timeout")
        mock_httpx_client.return_value.__aenter__.return_value = mock_client

        result = await _dexscreener_price(token="So11111111111111111111111111111111111111112")
        assert result is None

    @pytest.mark.asyncio
    async def test_birdeye_price_empty_address(self):
        """Empty address returns None immediately."""
        from app.databus.provider_implementations.price import _birdeye_price

        with patch("app.databus.provider_implementations.price.httpx.AsyncClient") as mock_http:
            result = await _birdeye_price(address="")
            assert result is None
            mock_http.assert_not_called()

    @pytest.mark.asyncio
    async def test_birdeye_price_success(self, mock_httpx_client):
        """Successful birdeye response returns price dict."""
        from app.databus.provider_implementations.price import _birdeye_price

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"value": 1.23}}
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_httpx_client.return_value.__aenter__.return_value = mock_client

        result = await _birdeye_price(address="So11111111111111111111111111111111111111112")
        assert result is not None
        assert result["price"] == 1.23
        assert result["source"] == "birdeye"

    @pytest.mark.asyncio
    async def test_birdeye_price_no_data_field(self, mock_httpx_client):
        """Birdeye response without data.value field returns price=None."""
        from app.databus.provider_implementations.price import _birdeye_price

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {}}
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_httpx_client.return_value.__aenter__.return_value = mock_client

        result = await _birdeye_price(address="So11111111111111111111111111111111111111112")
        assert result is not None
        assert result["price"] is None

    @pytest.mark.asyncio
    async def test_coingecko_price_no_api_key(self, mock_httpx_client):
        """CoinGecko works without API key (free tier)."""
        from app.databus.provider_implementations.price import _coingecko_price

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"usd": 1.23}
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_httpx_client.return_value.__aenter__.return_value = mock_client

        result = await _coingecko_price(token="ethereum", api_key="")
        assert result is not None

    @pytest.mark.asyncio
    async def test_moralis_price_no_api_key(self, mock_httpx_client):
        """Moralis without API key still makes request (key may be empty)."""
        from app.databus.provider_implementations.price import _moralis_price

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_httpx_client.return_value.__aenter__.return_value = mock_client

        result = await _moralis_price(token="0xabc", api_key="")
        assert result is None  # 401 → None


# ═══════════════════════════════════════════════════════════════════════
# 2. Free data providers
# ═══════════════════════════════════════════════════════════════════════


class TestFreeDataProviders:
    """Edge cases for free fallback providers."""

    @pytest.mark.asyncio
    async def test_dexscreener_token_metadata_empty(self, mock_free_httpx):
        """Empty mint returns None."""
        from app.databus.provider_implementations.free_data import _dexscreener_token_metadata

        result = await _dexscreener_token_metadata(mint="")
        assert result is None
        mock_free_httpx.assert_not_called()

    @pytest.mark.asyncio
    async def test_dexscreener_token_metadata_no_pairs(self, mock_free_httpx):
        """Response with no pairs returns None."""
        from app.databus.provider_implementations.free_data import _dexscreener_token_metadata

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"pairs": []}
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_free_httpx.return_value.__aenter__.return_value = mock_client

        result = await _dexscreener_token_metadata(
            mint="So11111111111111111111111111111111111111112"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_dexscreener_holders_empty(self, mock_free_httpx):
        """Empty token for holders returns None."""
        from app.databus.provider_implementations.free_data import _dexscreener_holders

        result = await _dexscreener_holders(mint="")
        assert result is None

    @pytest.mark.asyncio
    async def test_dexscreener_trades_empty(self, mock_free_httpx):
        """Empty token for trades returns None."""
        from app.databus.provider_implementations.free_data import _dexscreener_trades

        result = await _dexscreener_trades(token="")
        assert result is None

    @pytest.mark.asyncio
    async def test_etherscan_tx_trace_empty(self, mock_free_httpx):
        """Empty tx_hash returns None."""
        from app.databus.provider_implementations.free_data import _etherscan_tx_trace

        result = await _etherscan_tx_trace(tx_hash="")
        assert result is None

    @pytest.mark.asyncio
    async def test_etherscan_tx_trace_success(self, mock_free_httpx):
        """Successful etherscan trace returns tx data."""
        from app.databus.provider_implementations.free_data import _etherscan_tx_trace

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": {"hash": "0xabc"}}
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_free_httpx.return_value.__aenter__.return_value = mock_client

        result = await _etherscan_tx_trace(tx_hash="0xabc123", network="ethereum")
        assert result is not None
        assert result["tx_hash"] == "0xabc123"

    @pytest.mark.asyncio
    async def test_etherscan_tx_trace_unknown_network(self, mock_free_httpx):
        """Unknown network defaults to etherscan."""
        from app.databus.provider_implementations.free_data import _etherscan_tx_trace

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": {"hash": "0xabc"}}
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_free_httpx.return_value.__aenter__.return_value = mock_client

        result = await _etherscan_tx_trace(tx_hash="0xabc", network="unknown-chain")
        assert result is not None  # Falls back to etherscan.io

    @pytest.mark.asyncio
    async def test_etherscan_tx_trace_no_result(self, mock_free_httpx):
        """Response without result field returns None."""
        from app.databus.provider_implementations.free_data import _etherscan_tx_trace

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"message": "No transactions found"}
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_free_httpx.return_value.__aenter__.return_value = mock_client

        result = await _etherscan_tx_trace(tx_hash="0xabc")
        assert result is None


# ═══════════════════════════════════════════════════════════════════════
# 3. Arkham provider
# ═══════════════════════════════════════════════════════════════════════


class TestArkhamProviders:
    """Edge cases for Arkham Intelligence providers."""

    @pytest.mark.asyncio
    async def test_arkham_entity_empty(self, mock_arkham_httpx):
        """Empty address returns None."""
        from app.databus.provider_implementations.arkham import _arkham_entity

        result = await _arkham_entity(address="", api_key="key123")
        assert result is None
        mock_arkham_httpx.assert_not_called()

    @pytest.mark.asyncio
    async def test_arkham_entity_no_key(self, mock_arkham_httpx):
        """Missing api_key returns None."""
        from app.databus.provider_implementations.arkham import _arkham_entity

        result = await _arkham_entity(address="0xabc", api_key="")
        assert result is None
        mock_arkham_httpx.assert_not_called()

    @pytest.mark.asyncio
    async def test_arkham_counterparties_no_key(self, mock_arkham_httpx):
        """Missing api_key for counterparties returns None."""
        from app.databus.provider_implementations.arkham import _arkham_counterparties

        result = await _arkham_counterparties(address="0xabc", api_key="")
        assert result is None

    @pytest.mark.asyncio
    async def test_arkham_intel_search_no_query(self, mock_arkham_httpx):
        """Empty query for intel search returns None."""
        from app.databus.provider_implementations.arkham import _arkham_intel_search

        result = await _arkham_intel_search(query="", api_key="key")
        assert result is None

    @pytest.mark.asyncio
    async def test_arkham_portfolio_no_address(self, mock_arkham_httpx):
        """Empty address for portfolio returns None."""
        from app.databus.provider_implementations.arkham import _arkham_portfolio

        result = await _arkham_portfolio(address="", api_key="key")
        assert result is None

    @pytest.mark.asyncio
    async def test_arkham_transfers_timeout(self, mock_arkham_httpx):
        """Timeout during Arkham transfer call returns None gracefully."""
        from app.databus.provider_implementations.arkham import _arkham_transfers

        mock_client = AsyncMock()
        mock_client.get.side_effect = TimeoutError("connection timeout")
        mock_arkham_httpx.return_value.__aenter__.return_value = mock_client

        result = await _arkham_transfers(address="0xabc", api_key="key")
        assert result is None

    @pytest.mark.asyncio
    async def test_arkham_entity_rate_limit(self, mock_arkham_httpx):
        """429 rate-limit response returns None."""
        from app.databus.provider_implementations.arkham import _arkham_entity

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_arkham_httpx.return_value.__aenter__.return_value = mock_client

        result = await _arkham_entity(address="0xabc", api_key="key")
        assert result is None


# ═══════════════════════════════════════════════════════════════════════
# 4. Moralis provider
# ═══════════════════════════════════════════════════════════════════════


class TestMoralisProviders:
    """Edge cases for Moralis providers."""

    @pytest.mark.asyncio
    async def test_moralis_wallet_tokens_empty(self, mock_moralis_httpx):
        """Empty address for wallet tokens returns None."""
        from app.databus.provider_implementations.moralis import _moralis_wallet_tokens

        result = await _moralis_wallet_tokens(address="", api_key="key")
        assert result is None
        mock_moralis_httpx.assert_not_called()

    @pytest.mark.asyncio
    async def test_moralis_wallet_nfts_no_key(self, mock_moralis_httpx):
        """Missing api_key for NFTs returns None."""
        from app.databus.provider_implementations.moralis import _moralis_wallet_nfts

        result = await _moralis_wallet_nfts(address="0xabc", api_key="")
        assert result is None

    @pytest.mark.asyncio
    async def test_moralis_wallet_transactions_default_chain(self, mock_moralis_httpx):
        """wallet_transactions works with default chain."""
        from app.databus.provider_implementations.moralis import _moralis_wallet_transactions

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": []}
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_moralis_httpx.return_value.__aenter__.return_value = mock_client

        result = await _moralis_wallet_transactions(address="0xabc", api_key="key")
        assert result is not None
        assert result["chain"] == "eth"  # default

    @pytest.mark.asyncio
    async def test_moralis_token_price_no_key(self, mock_moralis_httpx):
        """Moralis token price without api_key is still attempted."""
        from app.databus.provider_implementations.price import _moralis_price

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"usdPrice": 1.23}
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_moralis_httpx.return_value.__aenter__.return_value = mock_client

        result = await _moralis_price(token="0xabc", api_key="")
        assert result is not None

    @pytest.mark.asyncio
    async def test_moralis_token_metadata_no_contract(self, mock_moralis_httpx):
        """Empty address for token metadata returns None."""
        from app.databus.provider_implementations.moralis import _moralis_token_metadata

        result = await _moralis_token_metadata(address="", api_key="key")
        assert result is None

    @pytest.mark.asyncio
    async def test_moralis_search_tokens_no_query(self, mock_moralis_httpx):
        """Empty query for search returns None."""
        from app.databus.provider_implementations.moralis import _moralis_search_tokens

        result = await _moralis_search_tokens(query="")
        assert result is None

    @pytest.mark.asyncio
    async def test_moralis_net_worth_success(self, mock_moralis_httpx):
        """wallet_net_worth returns parsed response."""
        from app.databus.provider_implementations.moralis import _moralis_wallet_net_worth

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"totalNetworthUsd": "1234.56"}
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_moralis_httpx.return_value.__aenter__.return_value = mock_client

        result = await _moralis_wallet_net_worth(address="0xabc", api_key="key")
        assert result is not None
        assert result["address"] == "0xabc"
        assert result["net_worth"]["totalNetworthUsd"] == "1234.56"


# ═══════════════════════════════════════════════════════════════════════
# 5. Alchemy provider
# ═══════════════════════════════════════════════════════════════════════


class TestAlchemyProviders:
    """Edge cases for Alchemy providers."""

    @pytest.mark.asyncio
    async def test_alchemy_balances_empty(self, mock_alchemy_httpx):
        """Empty address for token balances returns None."""
        from app.databus.provider_implementations.alchemy import _alchemy_token_balances

        result = await _alchemy_token_balances(address="", api_key="key")
        assert result is None
        mock_alchemy_httpx.assert_not_called()

    @pytest.mark.asyncio
    async def test_alchemy_balances_no_key(self, mock_alchemy_httpx):
        """Missing api_key returns None."""
        from app.databus.provider_implementations.alchemy import _alchemy_token_balances

        result = await _alchemy_token_balances(address="0xabc", api_key="")
        assert result is None

    @pytest.mark.asyncio
    async def test_alchemy_metadata_empty(self, mock_alchemy_httpx):
        """Empty contract for metadata returns None."""
        from app.databus.provider_implementations.alchemy import _alchemy_token_metadata

        result = await _alchemy_token_metadata(contract="", api_key="key")
        assert result is None

    @pytest.mark.asyncio
    async def test_alchemy_balances_success(self, mock_alchemy_httpx):
        """Successful alchemy response returns balances."""
        from app.databus.provider_implementations.alchemy import _alchemy_token_balances

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "result": {"address": "0xabc", "tokenBalances": []},
            "id": 1,
        }
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_alchemy_httpx.return_value.__aenter__.return_value = mock_client

        result = await _alchemy_token_balances(address="0xabc", api_key="key")
        assert result is not None
        assert result["address"] == "0xabc"

    @pytest.mark.asyncio
    async def test_alchemy_balances_no_result(self, mock_alchemy_httpx):
        """Alchemy response without 'result' returns None."""
        from app.databus.provider_implementations.alchemy import _alchemy_token_balances

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"jsonrpc": "2.0", "error": {"message": "bad request"}}
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_alchemy_httpx.return_value.__aenter__.return_value = mock_client

        result = await _alchemy_token_balances(address="0xabc", api_key="key")
        assert result is None


# ═══════════════════════════════════════════════════════════════════════
# 6. News / social providers
# ═══════════════════════════════════════════════════════════════════════


class TestNewsProviders:
    """Edge cases for news and social providers."""

    @pytest.mark.asyncio
    async def test_messari_news_no_key(self):
        """Missing Messari API key returns None."""
        from app.databus.provider_implementations.news import _messari_news

        with patch("app.databus.provider_implementations.news.httpx.AsyncClient") as mock_http:
            with patch.dict("os.environ", {"MESSARI_API_KEY": ""}, clear=False):
                result = await _messari_news(api_key="")
            assert result is None
            mock_http.assert_not_called()

    @pytest.mark.asyncio
    async def test_messari_news_success(self):
        """Successful Messari response returns articles."""
        from app.databus.provider_implementations.news import _messari_news

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {
                    "title": "Bitcoin rally",
                    "url": "https://example.com",
                    "source": {"sourceName": "CoinDesk"},
                    "publishTime": "2026-01-01T00:00:00Z",
                    "description": "Bitcoin is up",
                    "assets": [{"symbol": "BTC"}],
                    "sentiment": [{"sentiment": 0.5}],
                    "category": "markets",
                }
            ]
        }
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        with patch("app.databus.provider_implementations.news.httpx.AsyncClient") as mock_http:
            mock_http.return_value.__aenter__.return_value = mock_client
            with patch.dict("os.environ", {"MESSARI_API_KEY": ""}, clear=False):
                result = await _messari_news(limit=10, api_key="valid-key")
        assert result is not None
        assert result["count"] == 1
        assert result["articles"][0]["title"] == "Bitcoin rally"

    @pytest.mark.asyncio
    async def test_messari_news_empty_data(self):
        """Messari response without 'data' returns None."""
        from app.databus.provider_implementations.news import _messari_news

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        with patch("app.databus.provider_implementations.news.httpx.AsyncClient") as mock_http:
            mock_http.return_value.__aenter__.return_value = mock_client
            result = await _messari_news(limit=10, api_key="valid-key")
        assert result is None

    @pytest.mark.asyncio
    async def test_coindesk_news_no_key(self):
        """Missing CoinDesk API key returns None."""
        from app.databus.provider_implementations.news import _coindesk_news

        with patch("app.databus.provider_implementations.news.httpx.AsyncClient") as mock_http:
            result = await _coindesk_news(api_key="")
            assert result is None
            mock_http.assert_not_called()

    @pytest.mark.asyncio
    async def test_santiment_no_key(self):
        """Missing Santiment API key returns None."""
        from app.databus.provider_implementations.news import _santiment_dev_activity

        with patch("app.databus.provider_implementations.news.httpx.AsyncClient") as mock_http:
            result = await _santiment_dev_activity(project_slug="bitcoin", api_key="")
            assert result is None
            mock_http.assert_not_called()

    @pytest.mark.asyncio
    async def test_virustotal_no_key(self):
        """Missing VirusTotal key returns None."""
        from app.databus.provider_implementations.news import _virustotal_url_scan

        with patch("app.databus.provider_implementations.news.httpx.AsyncClient") as mock_http:
            result = await _virustotal_url_scan(url="https://example.com", api_key="")
            assert result is None
            mock_http.assert_not_called()

    @pytest.mark.asyncio
    async def test_dune_early_buyers_no_token(self):
        """Missing token address for Dune returns None."""
        from app.databus.provider_implementations.news import _dune_early_buyers

        with patch("app.databus.provider_implementations.news.httpx.AsyncClient") as mock_http:
            result = await _dune_early_buyers(token_address="", api_key="key")
            assert result is None
            mock_http.assert_not_called()

    @pytest.mark.asyncio
    async def test_dune_early_buyers_success(self):
        """Successful Dune response returns early buyers."""
        from app.databus.provider_implementations.news import _dune_early_buyers

        mock_exec_response = MagicMock()
        mock_exec_response.status_code = 200
        mock_exec_response.json.return_value = {"execution_id": "exec_123"}

        mock_result_response = MagicMock()
        mock_result_response.status_code = 200
        mock_result_response.json.return_value = {
            "state": "QUERY_STATE_COMPLETED",
            "result": {"rows": [{"buyer": "0xabc", "amount": 100}]},
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_exec_response
        mock_client.get.return_value = mock_result_response

        with patch("app.databus.provider_implementations.news.httpx.AsyncClient") as mock_http:
            mock_http.return_value.__aenter__.return_value = mock_client
            result = await _dune_early_buyers(
                token_address="0xabc123", chain="ethereum", api_key="key"
            )
        assert result is not None
        assert result["source"] == "dune"


# ═══════════════════════════════════════════════════════════════════════
# 7. MCP bridge and passthrough
# ═══════════════════════════════════════════════════════════════════════


class TestMCPBridgeProvider:
    """Edge cases for MCP bridge provider."""

    @pytest.mark.asyncio
    async def test_mcp_bridge_handles_missing_tool(self):
        """MCP bridge handles a missing tool gracefully."""
        from app.databus.provider_implementations.mcp_bridge import _mcp_bridge

        with patch(
            "app.databus.provider_implementations.mcp_bridge.httpx.AsyncClient"
        ) as mock_http:
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_http.return_value.__aenter__.return_value = mock_client

            result = await _mcp_bridge(server="test", tool="nonexistent", args={})
            assert result is None
