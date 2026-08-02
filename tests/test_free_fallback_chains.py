"""Tests for free API fallback providers (chains/free_security.py).

Covers the three free fallback chains registered in the DataBus registry:
  - token_security_free  (security.py)
  - wallet_labels_free   (wallet.py)
  - market_price_free    (market.py)

All external APIs are mocked; no real network calls.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domains.databus.providers.registry import build_provider_chains

# ═══════════════════════════════════════════════════════════════════════════
# Registry integration
# ═══════════════════════════════════════════════════════════════════════════


class TestFreeFallbackChainRegistration:
    """The new free fallback chains must be built by the registry."""

    def test_free_chains_registered(self):
        chains = build_provider_chains()
        for name in ("token_security_free", "wallet_labels_free", "market_price_free"):
            assert name in chains, f"missing chain {name}"
            assert chains[name].providers, f"chain {name} has no providers"

    def test_token_security_free_tier_order(self):
        chains = build_provider_chains()
        providers = [p.name for p in chains["token_security_free"].providers]
        assert providers == [
            "free_security_clickhouse",
            "free_security_goplus",
            "free_security_honeypot_is",
            "free_security_rugcheck",
            "free_security_dexscreener",
        ]

    def test_wallet_labels_free_tier_order(self):
        chains = build_provider_chains()
        providers = [p.name for p in chains["wallet_labels_free"].providers]
        assert providers == [
            "clickhouse_wallet_memory",
            "free_blockscout_labels",
            "free_etherscan_labels",
            "dexscreener_top_traders",
            "free_wallet_pattern_heuristics",
        ]

    def test_market_price_free_tier_order(self):
        chains = build_provider_chains()
        providers = [p.name for p in chains["market_price_free"].providers]
        assert providers == [
            "free_market_clickhouse_twap",
            "dexscreener_price_free",
            "free_market_coingecko",
            "free_market_geckoterminal",
            "free_market_paraswap_quote",
            "free_market_oneinch_quote",
        ]

    def test_free_tiers_never_use_paid(self):
        """The free fallback chains must contain zero PAID-tier providers."""
        from app.domains.databus.providers.base import ProviderTier

        chains = build_provider_chains()
        for name in ("token_security_free", "wallet_labels_free", "market_price_free"):
            tiers = [p.tier for p in chains[name].providers]
            assert ProviderTier.PAID not in tiers, f"{name} uses a PAID provider"


# ═══════════════════════════════════════════════════════════════════════════
# Provider edge cases (empty inputs → None, no HTTP calls)
# ═══════════════════════════════════════════════════════════════════════════


class TestFreeSecurityProviders:
    """Edge cases for token security fallback providers."""

    @pytest.mark.asyncio
    @patch("app.domains.databus.providers.chains.free_security.httpx.AsyncClient")
    async def test_goplus_empty_token(self, mock_http):
        from app.domains.databus.providers.chains.free_security import _free_security_goplus

        result = await _free_security_goplus(token_address="", chain="ethereum")
        assert result is None
        mock_http.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.domains.databus.providers.chains.free_security.httpx.AsyncClient")
    async def test_goplus_no_chain(self, mock_http):
        from app.domains.databus.providers.chains.free_security import _free_security_goplus

        result = await _free_security_goplus(token_address="0xabc", chain="")
        assert result is None
        mock_http.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.domains.databus.providers.chains.free_security.httpx.AsyncClient")
    async def test_honeypot_is_evm_only(self, mock_http):
        from app.domains.databus.providers.chains.free_security import _free_security_honeypot_is

        # solana has no EVM chain id → should bail before HTTP
        result = await _free_security_honeypot_is(token_address="0xabc", chain="solana")
        assert result is None
        mock_http.assert_not_called()

    @pytest.mark.asyncio
    async def test_rugcheck_solana_only(self):
        from app.domains.databus.providers.chains.free_security import _free_security_rugcheck

        # non-solana → None, and it must NOT lowercase solana addresses
        result = await _free_security_rugcheck(token_address="0xabc", chain="ethereum")
        assert result is None

    @pytest.mark.asyncio
    @patch("app.domains.databus.providers.chains.free_security.httpx.AsyncClient")
    async def test_dexscreener_empty_token(self, mock_http):
        from app.domains.databus.providers.chains.free_security import _free_security_dexscreener

        result = await _free_security_dexscreener(token_address="")
        assert result is None
        mock_http.assert_not_called()


class TestFreeWalletProviders:
    """Edge cases for wallet/label fallback providers."""

    @pytest.mark.asyncio
    @patch("app.domains.databus.providers.chains.free_security.httpx.AsyncClient")
    async def test_blockscout_empty_address(self, mock_http):
        from app.domains.databus.providers.chains.free_security import _free_blockscout_labels

        result = await _free_blockscout_labels(address="", chain="ethereum")
        assert result is None
        mock_http.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.domains.databus.providers.chains.free_security.httpx.AsyncClient")
    async def test_etherscan_requires_key(self, mock_http):
        from app.domains.databus.providers.chains.free_security import _free_etherscan_labels

        result = await _free_etherscan_labels(address="0xabc", chain="ethereum", api_key="")
        assert result is None
        mock_http.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.domains.databus.providers.chains.free_security.httpx.AsyncClient")
    async def test_pattern_heuristics_empty_address(self, mock_http):
        from app.domains.databus.providers.chains.free_security import _free_wallet_pattern_heuristics

        result = await _free_wallet_pattern_heuristics(address="")
        assert result is None


class TestFreeMarketProviders:
    """Edge cases for market/price fallback providers."""

    @pytest.mark.asyncio
    @patch("app.domains.databus.providers.chains.free_security.httpx.AsyncClient")
    async def test_coingecko_empty_token(self, mock_http):
        from app.domains.databus.providers.chains.free_security import _free_market_coingecko

        result = await _free_market_coingecko(token_address="", chain="ethereum")
        assert result is None
        mock_http.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.domains.databus.providers.chains.free_security.httpx.AsyncClient")
    async def test_geckoterminal_unknown_chain(self, mock_http):
        from app.domains.databus.providers.chains.free_security import _free_market_geckoterminal

        result = await _free_market_geckoterminal(token_address="0xabc", chain="notachain")
        assert result is None
        mock_http.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.domains.databus.providers.chains.free_security.httpx.AsyncClient")
    async def test_paraswap_evm_only(self, mock_http):
        from app.domains.databus.providers.chains.free_security import _free_market_paraswap_quote

        result = await _free_market_paraswap_quote(token_address="0xabc", chain="solana")
        assert result is None
        mock_http.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.domains.databus.providers.chains.free_security.httpx.AsyncClient")
    async def test_oneinch_requires_key(self, mock_http):
        from app.domains.databus.providers.chains.free_security import _free_market_oneinch_quote

        result = await _free_market_oneinch_quote(token_address="0xabc", chain="ethereum", api_key="")
        assert result is None
        mock_http.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# Provider success paths (mocked responses)
# ═══════════════════════════════════════════════════════════════════════════


class TestFreeSecurityProviderParsing:
    """Parsing of real API response shapes (mocked)."""

    @pytest.mark.asyncio
    async def test_goplus_parses_honeypot(self):
        from app.domains.databus.providers.chains.free_security import _free_security_goplus

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": {
                "0xabc": {
                    "is_honeypot": "1",
                    "is_open_source": "1",
                    "buy_tax": "25",
                    "sell_tax": "30",
                    "holder_count": "1200",
                    "total_supply": "1000000",
                }
            }
        }
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client

        with patch("app.domains.databus.providers.chains.free_security.httpx.AsyncClient", return_value=mock_client):
            result = await _free_security_goplus(token_address="0xABC", chain="ethereum")

        assert result is not None
        assert result["is_honeypot"] is True
        assert result["risk_score"] >= 50
        assert result["buy_tax"] == 25.0
        assert result["sell_tax"] == 30.0
        assert result["source"] == "goplus"

    @pytest.mark.asyncio
    async def test_honeypot_is_parses(self):
        from app.domains.databus.providers.chains.free_security import _free_security_honeypot_is

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "token": {"name": "Test", "symbol": "TST", "totalHolders": 500},
            "summary": {"risk": "high", "riskLevel": 3},
            "simulationSuccess": True,
            "honeypotResult": {"isHoneypot": True, "flags": ["honeypot"]},
            "simulationResult": {"buyTax": 15, "sellTax": 40, "transferTax": 0},
        }
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client

        with patch("app.domains.databus.providers.chains.free_security.httpx.AsyncClient", return_value=mock_client):
            result = await _free_security_honeypot_is(token_address="0xabc", chain="ethereum")

        assert result is not None
        assert result["is_honeypot"] is True
        assert result["risk_score"] >= 60
        assert result["source"] == "honeypot.is"

    @pytest.mark.asyncio
    async def test_rugcheck_parses(self):
        from app.domains.databus.providers.chains.free_security import _free_security_rugcheck

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "mint": "So11111111111111111111111111111111111111112",
            "score": 850,
            "score_normalised": 0.85,
            "tokenMeta": {"name": "Test", "symbol": "TST"},
            "token": {"mintAuthority": None, "freezeAuthority": "addr"},
            "risks": [{"name": "Mint authority present", "level": "danger", "description": "can mint", "score": 40}],
        }
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client

        with patch("app.domains.databus.providers.chains.free_security.httpx.AsyncClient", return_value=mock_client):
            result = await _free_security_rugcheck(
                token_address="So11111111111111111111111111111111111111112", chain="solana"
            )

        assert result is not None
        assert result["rugcheck_score"] == 850
        assert len(result["risks"]) == 1
        assert result["source"] == "rugcheck.xyz"

    @pytest.mark.asyncio
    async def test_blockscout_parses_labels(self):
        from app.domains.databus.providers.chains.free_security import _free_blockscout_labels

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "hash": "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
            "name": "Uniswap",
            "is_contract": True,
            "is_verified": True,
            "is_scam": False,
            "ens_domain_name": "uni.eth",
            "public_tags": [{"name": "Uniswap V2"}],
            "private_tags": [],
        }
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client

        with patch("app.domains.databus.providers.chains.free_security.httpx.AsyncClient", return_value=mock_client):
            result = await _free_blockscout_labels(
                address="0x1f9840a85d5af5bf1d1762f925bdaddc4201f984", chain="ethereum"
            )

        assert result is not None
        assert result["name"] == "Uniswap"
        assert "Uniswap" in [t.get("label") for t in result["labels"]]
        assert result["source"] == "blockscout"

    @pytest.mark.asyncio
    async def test_geckoterminal_parses(self):
        from app.domains.databus.providers.chains.free_security import _free_market_geckoterminal

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "attributes": {
                    "price_usd": "1.234",
                    "fdv_usd": "1000000",
                    "total_supply": "1000000",
                    "name": "Test",
                    "symbol": "TST",
                }
            }
        }
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client

        with patch("app.domains.databus.providers.chains.free_security.httpx.AsyncClient", return_value=mock_client):
            result = await _free_market_geckoterminal(token_address="0xabc", chain="ethereum")

        assert result is not None
        assert result["price_usd"] == 1.234
        assert result["source"] == "geckoterminal"

    @pytest.mark.asyncio
    async def test_paraswap_parses(self):
        from app.domains.databus.providers.chains.free_security import _free_market_paraswap_quote

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "priceRoute": {
                "srcToken": "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
                "destToken": "0xabc",
                "srcAmount": "1000000000000000000",
                "destAmount": "2500000000000000000000",
                "bestRoute": [{"swapExchanges": [{"exchange": "uniswapv3"}]}],
            }
        }
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client

        with patch("app.domains.databus.providers.chains.free_security.httpx.AsyncClient", return_value=mock_client):
            result = await _free_market_paraswap_quote(token_address="0xabc", chain="ethereum")

        assert result is not None
        assert result["price_usd"] == 2500.0
        assert result["source"] == "paraswap"
