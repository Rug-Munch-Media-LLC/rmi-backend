"""Tests for the GMGN DataBus provider chain module.

GMGN public API endpoints are verified against open-source usage; responses
are mocked here. No real network calls.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_gmgn_http():
    """Patch httpx.AsyncClient inside chains.gmgn."""
    with patch("app.domains.databus.providers.chains.gmgn.httpx.AsyncClient") as m:
        yield m


def _mock_200(mock_http, payload: dict):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = payload
    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    mock_http.return_value.__aenter__.return_value = mock_client
    return mock_client


class TestGmgnTokenInfo:
    """_gmgn_token_info — token overview endpoint."""

    @pytest.mark.asyncio
    async def test_empty_mint_returns_none(self, mock_gmgn_http):
        from app.domains.databus.providers.chains.gmgn import _gmgn_token_info

        result = await _gmgn_token_info(mint="", chain="solana")
        assert result is None
        mock_gmgn_http.assert_not_called()

    @pytest.mark.asyncio
    async def test_success_parses_pump_detail(self, mock_gmgn_http):
        from app.domains.databus.providers.chains.gmgn import _gmgn_token_info

        _mock_200(
            mock_gmgn_http,
            {
                "code": 0,
                "data": {
                    "token": {
                        "address": "TOKEN123",
                        "symbol": "PUMP",
                        "name": "Pump Token",
                        "price": 0.00123,
                        "market_cap": 12345,
                        "fdv": 50000,
                        "liquidity": 8000,
                        "holder_count": 250,
                        "creation_timestamp": 1723000000,
                        "launchpad": "pump",
                        "launchpad_progress": 42,
                        "creator_address": "CREATOR1",
                        "is_honeypot": False,
                        "top_10_holder_rate": 0.55,
                        "pool_info": {"address": "POOL1"},
                    }
                },
            },
        )
        result = await _gmgn_token_info(mint="TOKEN123", chain="solana")
        assert result is not None
        assert result["symbol"] == "PUMP"
        assert result["chain"] == "sol"
        assert result["holder_count"] == 250
        assert result["launchpad"] == "pump"
        assert result["source"] == "gmgn"

    @pytest.mark.asyncio
    async def test_nonzero_code_returns_none(self, mock_gmgn_http):
        from app.domains.databus.providers.chains.gmgn import _gmgn_token_info

        _mock_200(mock_gmgn_http, {"code": 400, "msg": "invalid address"})
        result = await _gmgn_token_info(mint="BAD", chain="solana")
        assert result is None

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self, mock_gmgn_http):
        from app.domains.databus.providers.chains.gmgn import _gmgn_token_info

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_gmgn_http.return_value.__aenter__.return_value = mock_client

        result = await _gmgn_token_info(mint="TOKEN", chain="solana")
        assert result is None


class TestGmgnWalletTxHistory:
    """_gmgn_wallet_tx_history — wallet smart-money stats."""

    @pytest.mark.asyncio
    async def test_empty_address_returns_none(self, mock_gmgn_http):
        from app.domains.databus.providers.chains.gmgn import _gmgn_wallet_tx_history

        result = await _gmgn_wallet_tx_history(address="", chain="solana")
        assert result is None

    @pytest.mark.asyncio
    async def test_success_parses_wallet_stats(self, mock_gmgn_http):
        from app.domains.databus.providers.chains.gmgn import _gmgn_wallet_tx_history

        _mock_200(
            mock_gmgn_http,
            {
                "code": 0,
                "data": {
                    "total_profit_pnl": 0.25,
                    "realized_profit_7d": 150.5,
                    "realized_profit_30d": 400.0,
                    "winrate": 0.62,
                    "sol_balance": 3.5,
                    "buy_7d": 12,
                    "buy_30d": 40,
                    "avg_holding_peroid": 3600,
                    "tags": ["smart_money", "sniping"],
                },
            },
        )
        result = await _gmgn_wallet_tx_history(address="WALLET1", chain="solana")
        assert result is not None
        assert result["wallet"] == "WALLET1"
        assert result["winrate"] == 0.62
        assert result["tags"] == ["smart_money", "sniping"]
        assert result["period"] == "7d"


class TestGmgnTokenHolders:
    """_gmgn_token_holders — top holder list."""

    @pytest.mark.asyncio
    async def test_success_parses_holders(self, mock_gmgn_http):
        from app.domains.databus.providers.chains.gmgn import _gmgn_token_holders

        _mock_200(
            mock_gmgn_http,
            {
                "data": {
                    "list": [
                        {
                            "address": "HOLDER1",
                            "amount_percentage": 12.5,
                            "cost_cur": 1000,
                            "realized_profit": 500,
                            "unrealized_profit": 200,
                            "profit_change": 2.5,
                            "buy_tx_count_cur": 3,
                            "sell_tx_count_cur": 1,
                            "insider": True,
                            "is_smart_dealer": True,
                        },
                        {
                            "address": "HOLDER2",
                            "amount_percentage": 3.2,
                            "cost_cur": 50,
                            "realized_profit": 0,
                            "unrealized_profit": 10,
                            "profit_change": 1.1,
                            "buy_tx_count_cur": 8,
                            "sell_tx_count_cur": 2,
                            "insider": False,
                        },
                    ]
                }
            },
        )
        result = await _gmgn_token_holders(mint="TOKEN", chain="solana", limit=100)
        assert result is not None
        assert result["total"] == 2
        assert result["holders"][0]["is_insider"] is True
        assert result["holders"][0]["is_smart_money"] is True
        assert result["holders"][1]["is_insider"] is False


class TestGmgnNewPairs:
    """_gmgn_new_pairs / _gmgn_bonded_pairs — launch feed sources."""

    @pytest.mark.asyncio
    async def test_new_pairs_parses_rank(self, mock_gmgn_http):
        from app.domains.databus.providers.chains.gmgn import _gmgn_new_pairs

        _mock_200(
            mock_gmgn_http,
            {
                "code": 0,
                "data": {
                    "rank": [
                        {
                            "address": "NEWTOKEN1",
                            "symbol": "NEW",
                            "name": "New Token",
                            "price": 0.0001,
                            "market_cap": 5000,
                            "liquidity": 1000,
                            "holder_count": 30,
                            "creation_timestamp": 1723000000,
                            "launchpad": "pump",
                            "is_honeypot": False,
                            "creator_address": "CREATOR",
                        }
                    ]
                },
            },
        )
        result = await _gmgn_new_pairs(chain="solana", limit=50)
        assert result is not None
        assert result["data"]["count"] == 1
        launch = result["data"]["launches"][0]
        assert launch["address"] == "NEWTOKEN1"
        assert launch["launchpad"] == "pump"
        assert launch["source"] == "gmgn"

    @pytest.mark.asyncio
    async def test_bonded_pairs_parses_base_address(self, mock_gmgn_http):
        from app.domains.databus.providers.chains.gmgn import _gmgn_bonded_pairs

        _mock_200(
            mock_gmgn_http,
            {
                "code": 0,
                "data": {
                    "pairs": [
                        {
                            "base_address": "BONDED1",
                            "address": "PAIRADDR1",
                            "symbol": "BOND",
                            "name": "Bonded Token",
                            "price": 0.01,
                            "market_cap": 100000,
                            "liquidity": 50000,
                            "open_timestamp": 1723001000,
                            "launchpad": "pump",
                        }
                    ]
                },
            },
        )
        result = await _gmgn_bonded_pairs(chain="solana", limit=50)
        assert result is not None
        launch = result["data"]["launches"][0]
        assert launch["address"] == "BONDED1"
        assert launch["pair_address"] == "PAIRADDR1"
        assert launch["launchpad"] == "pump"


class TestGmgnTokenTrades:
    """_gmgn_token_trades — trade history."""

    @pytest.mark.asyncio
    async def test_success_parses_history(self, mock_gmgn_http):
        from app.domains.databus.providers.chains.gmgn import _gmgn_token_trades

        _mock_200(
            mock_gmgn_http,
            {
                "code": 0,
                "data": {
                    "history": [
                        {
                            "tx_hash": "TX1",
                            "timestamp": 1723000000,
                            "event": "buy",
                            "maker": "WALLET1",
                            "price": 0.001,
                            "amount": 1000,
                            "total": 1.0,
                            "profit": 0.0,
                        }
                    ]
                },
            },
        )
        result = await _gmgn_token_trades(mint="TOKEN", chain="solana", limit=100)
        assert result is not None
        assert result["data"]["count"] == 1
        trade = result["data"]["trades"][0]
        assert trade["tx_hash"] == "TX1"
        assert trade["is_buy"] is True
