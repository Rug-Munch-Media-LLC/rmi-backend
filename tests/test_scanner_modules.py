"""Tests for scanner module functions with mocked API responses.

Covers the 9+ core scanner modules that power token security analysis.
Each test mocks external HTTP calls to ensure deterministic, fast execution.
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domains.scanner.modules._core import _DEXSCREENER_CACHE


@pytest.fixture(autouse=True)
def _clear_dexscreener_cache() -> None:
    """Clear the process-wide DexScreener cache before each test.

    ``_fetch_dexscreener`` caches results per (token, chain) for the process
    lifetime. Earlier test modules call the real fetcher for ``0xtoken``
    (e.g. tests/unit/domain/scanner/test_modules.py) which can cache ``None``
    under ``0xtoken:ethereum``; that stale entry defeats the
    ``_fetch_with_fallback`` patch these tests rely on (the honeypot
    fallback would return the cached value instead of the mocked pairs).
    """
    _DEXSCREENER_CACHE.clear()
    yield
    _DEXSCREENER_CACHE.clear()


def _mock_httpx_response(data: dict, status: int = 200):
    """Create a mock httpx response. resp.json() is sync in httpx."""
    m = MagicMock()
    m.status_code = status
    m.json.return_value = data
    return m


# ── 1. GoPlus Security ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_goplus_security_honeypot_detected():
    """GoPlus correctly flags a honeypot token."""
    mock_response = {
        "code": 1,
        "message": "OK",
        "result": {
            "0x1234567890abcdef1234567890abcdef12345678": {
                "is_honeypot": "1",
                "is_open_source": "1",
                "is_proxy": "0",
                "is_mintable": "0",
                "is_blacklisted": "0",
                "is_whitelisted": "0",
                "can_take_back_ownership": "0",
                "hidden_owner": "0",
                "owner_address": "0xdead000000000000000000000000000000000000",
                "buy_tax": "0",
                "sell_tax": "0",
                "holder_count": "100",
                "total_supply": "1000000",
                "selfdestruct": "0",
                "transfer_pausable": "0",
                "trading_cooldown": "0",
                "owner_change_balance": "0",
            }
        },
    }

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_get.return_value = _mock_httpx_response(mock_response)

        from app.domains.scanner.modules import _check_goplus_security

        result = await _check_goplus_security("0x1234567890abcdef1234567890abcdef12345678", "ethereum")

        assert result is not None
        assert result["check"] == "goplus"
        assert result["result"] == "scanned"
        assert result["is_honeypot"] is True
        assert result["risk_score"] >= 50
        assert result["source"] == "goplus"


@pytest.mark.asyncio
async def test_goplus_security_clean_token():
    """GoPlus correctly reports a clean token with low risk."""
    mock_response = {
        "code": 1,
        "message": "OK",
        "result": {
            "0xabcdef1234567890abcdef1234567890abcdef12": {
                "is_honeypot": "0",
                "is_open_source": "1",
                "is_proxy": "0",
                "is_mintable": "0",
                "is_blacklisted": "0",
                "is_whitelisted": "0",
                "can_take_back_ownership": "0",
                "hidden_owner": "0",
                "owner_address": "0xdead000000000000000000000000000000000000",
                "buy_tax": "0",
                "sell_tax": "0",
                "holder_count": "5000",
                "total_supply": "1000000000",
                "selfdestruct": "0",
                "transfer_pausable": "0",
                "trading_cooldown": "0",
                "owner_change_balance": "0",
            }
        },
    }

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_get.return_value = _mock_httpx_response(mock_response)

        from app.domains.scanner.modules import _check_goplus_security

        result = await _check_goplus_security("0xabcdef1234567890abcdef1234567890abcdef12", "ethereum")

        assert result is not None
        assert result["result"] == "scanned"
        assert result["is_honeypot"] is False
        assert result["risk_score"] < 30


@pytest.mark.asyncio
async def test_goplus_security_http_error():
    """GoPlus returns error result on HTTP failure."""
    with patch("httpx.AsyncClient.get") as mock_get:
        mock_get.return_value = _mock_httpx_response({}, status=429)

        from app.domains.scanner.modules import _check_goplus_security

        result = await _check_goplus_security("0xdead000000000000000000000000000000000000", "ethereum")

        assert result is not None
        assert result["check"] == "goplus"
        assert result["result"] in ("error", "pending")


@pytest.mark.asyncio
async def test_goplus_security_timeout():
    """GoPlus returns error on timeout."""
    with patch("httpx.AsyncClient.get") as mock_get:
        import httpx

        mock_get.side_effect = httpx.TimeoutException("Connection timed out")

        from app.domains.scanner.modules import _check_goplus_security

        result = await _check_goplus_security("0xdead000000000000000000000000000000000000", "ethereum")

        assert result is not None
        assert result["check"] == "goplus"
        assert result["result"] == "error"


# ── 2. Honeypot Detection ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_honeypot_high_risk():
    """Honeypot detection identifies high-risk token via DexScreener fallback."""
    mock_dexscreener = {
        "pairs": [
            {
                "chainId": "ethereum",
                "dexId": "uniswap",
                "pairAddress": "0xpair",
                "baseToken": {"address": "0xtoken", "name": "ScamToken", "symbol": "SCAM"},
                "quoteToken": {"address": "0xUSDC", "name": "USDC", "symbol": "USDC"},
                "priceUsd": "0.001",
                "txns": {"h24": {"buys": 150, "sells": 0}},
                "volume": {"h24": 1000},
                "liquidity": {"usd": 50},
                "priceChange": {"h24": -95},
                "pairCreatedAt": 1700000000000,
            }
        ]
    }

    with (
        patch("app.domains.scanner.modules._core._fetch_with_fallback") as mock_fallback,
        patch("app.domains.scanner.modules._core.PREFER_OWN_DATA", False),
    ):
        mock_fallback.return_value = (mock_dexscreener, "https://api.dexscreener.com/")
        # Force ImportError by making HoneypotDetector import fail
        import builtins

        _real_import = builtins.__import__

        def _mock_import(name, *args, **kwargs):
            if name in (
                "app.domains.detectors.smart_contract_honeypot_detector",
                "app.smart_contract_honeypot_detector",
            ):
                raise ImportError(f"No module named {name!r}")
            return _real_import(name, *args, **kwargs)

        builtins.__import__ = _mock_import
        try:
            from app.domains.scanner.modules import _check_honeypot_is

            result = await _check_honeypot_is("0xtoken", "ethereum")

            assert result is not None
            assert result["check"] == "honeypot"
            assert result["is_honeypot"] is True
            assert result["risk_score"] >= 50
            assert result["source"] == "dexscreener_fallback"
        finally:
            builtins.__import__ = _real_import


@pytest.mark.asyncio
async def test_honeypot_clean_token():
    """Honeypot detection identifies healthy token via DexScreener."""
    mock_dexscreener = {
        "pairs": [
            {
                "chainId": "ethereum",
                "dexId": "uniswap",
                "pairAddress": "0xpair",
                "baseToken": {"address": "0xtoken", "name": "GoodToken", "symbol": "GOOD"},
                "priceUsd": "1.50",
                "txns": {"h24": {"buys": 500, "sells": 300}},
                "volume": {"h24": 50000},
                "liquidity": {"usd": 250000},
                "priceChange": {"h24": 2.5},
                "pairCreatedAt": 1700000000000,
            }
        ]
    }

    with (
        patch("app.domains.scanner.modules._core._fetch_with_fallback") as mock_fallback,
        patch("app.domains.scanner.modules._core.PREFER_OWN_DATA", False),
    ):
        mock_fallback.return_value = (mock_dexscreener, "https://api.dexscreener.com/")
        # Clear DexScreener cache to avoid pollution from previous tests
        from app.domains.scanner.modules._core import _DEXSCREENER_CACHE

        _DEXSCREENER_CACHE.clear()
        # Remove honeypot detector from cache
        sys.modules.pop("app.domains.detectors.smart_contract_honeypot_detector", None)
        import builtins

        _real_import = builtins.__import__

        def _mock_import(name, *args, **kwargs):
            if name in (
                "app.domains.detectors.smart_contract_honeypot_detector",
                "app.smart_contract_honeypot_detector",
            ):
                raise ImportError(f"No module named {name!r}")
            return _real_import(name, *args, **kwargs)

        builtins.__import__ = _mock_import
        try:
            from app.domains.scanner.modules import _check_honeypot_is

            result = await _check_honeypot_is("0xtoken", "ethereum")
        finally:
            builtins.__import__ = _real_import

        assert result is not None
        assert result["check"] == "honeypot"
        assert result["is_honeypot"] is False
        assert result["risk_score"] < 50


@pytest.mark.asyncio
async def test_honeypot_fallback_new_token_not_flagged():
    """Fresh pair (5 min old) with buys but no sells is NOT flagged honeypot.

    Regression: sells==0 within the first minutes of a launch is normal for
    legitimate tokens, so the fallback must not mass-flag new launches during
    detector outages. Returns no_data (inconclusive) with a mild caution.
    """
    import time as _time

    mock_dexscreener = {
        "pairs": [
            {
                "chainId": "ethereum",
                "dexId": "uniswap",
                "pairAddress": "0xpairnew",
                "baseToken": {"address": "0xnewtoken", "name": "NewToken", "symbol": "NEW"},
                "priceUsd": "0.01",
                "txns": {"h24": {"buys": 20, "sells": 0}},
                "volume": {"h24": 100},
                "liquidity": {"usd": 500},
                "priceChange": {"h24": 5},
                "pairCreatedAt": int((_time.time() - 5 * 60) * 1000),
            }
        ]
    }

    with (
        patch("app.domains.scanner.modules._core._fetch_with_fallback") as mock_fallback,
        patch("app.domains.scanner.modules._core.PREFER_OWN_DATA", False),
    ):
        mock_fallback.return_value = (mock_dexscreener, "https://api.dexscreener.com/")
        from app.domains.scanner.modules._core import _DEXSCREENER_CACHE

        _DEXSCREENER_CACHE.clear()
        import builtins

        _real_import = builtins.__import__

        def _mock_import(name, *args, **kwargs):
            if name in (
                "app.domains.detectors.smart_contract_honeypot_detector",
                "app.smart_contract_honeypot_detector",
            ):
                raise ImportError(f"No module named {name!r}")
            return _real_import(name, *args, **kwargs)

        builtins.__import__ = _mock_import
        try:
            from app.domains.scanner.modules import _check_honeypot_is

            result = await _check_honeypot_is("0xnewtoken", "ethereum")
        finally:
            builtins.__import__ = _real_import

        assert result is not None
        assert result["check"] == "honeypot"
        assert result["result"] == "no_data"
        assert result["is_honeypot"] is False
        assert result["risk_score"] < 40
        assert result["pair_age_hours"] < 0.5


@pytest.mark.asyncio
async def test_honeypot_fallback_old_token_flagged():
    """Mature pair (2h old) with buys but no sells IS flagged honeypot."""
    import time as _time

    mock_dexscreener = {
        "pairs": [
            {
                "chainId": "ethereum",
                "dexId": "uniswap",
                "pairAddress": "0xpairold",
                "baseToken": {"address": "0xoldtoken", "name": "OldScam", "symbol": "OLD"},
                "priceUsd": "0.001",
                "txns": {"h24": {"buys": 20, "sells": 0}},
                "volume": {"h24": 100},
                "liquidity": {"usd": 500},
                "priceChange": {"h24": 5},
                "pairCreatedAt": int((_time.time() - 2 * 60 * 60) * 1000),
            }
        ]
    }

    with (
        patch("app.domains.scanner.modules._core._fetch_with_fallback") as mock_fallback,
        patch("app.domains.scanner.modules._core.PREFER_OWN_DATA", False),
    ):
        mock_fallback.return_value = (mock_dexscreener, "https://api.dexscreener.com/")
        from app.domains.scanner.modules._core import _DEXSCREENER_CACHE

        _DEXSCREENER_CACHE.clear()
        import builtins

        _real_import = builtins.__import__

        def _mock_import(name, *args, **kwargs):
            if name in (
                "app.domains.detectors.smart_contract_honeypot_detector",
                "app.smart_contract_honeypot_detector",
            ):
                raise ImportError(f"No module named {name!r}")
            return _real_import(name, *args, **kwargs)

        builtins.__import__ = _mock_import
        try:
            from app.domains.scanner.modules import _check_honeypot_is

            result = await _check_honeypot_is("0xoldtoken", "ethereum")
        finally:
            builtins.__import__ = _real_import

        assert result is not None
        assert result["check"] == "honeypot"
        assert result["result"] == "scanned"
        assert result["is_honeypot"] is True
        assert result["risk_score"] >= 50
        assert result["pair_age_hours"] > 0.5


# ── 3. Copycat Detection ──────────────────────────────────────────────


def test_copycat_exact_symbol():
    """Exact top symbol match returns clean."""
    # Need to call async, but this function is synchronous internally
    # Let's test the underlying helpers directly


@pytest.mark.asyncio
async def test_copycat_exact_top_symbol():
    """Exact match to a top symbol returns clean."""
    from app.domains.scanner.modules import _check_copycat

    result = await _check_copycat("ETH", "Ethereum")

    assert result is not None
    assert result["result"] == "clean"
    assert result["match_type"] == "exact_symbol"


@pytest.mark.asyncio
async def test_copycat_similar_symbol():
    """Symbol similar to top coin returns high_risk."""
    from app.domains.scanner.modules import _check_copycat

    result = await _check_copycat("EHT", "Some Ethereum Copy")

    assert result is not None
    assert result["result"] == "high_risk"
    # The exact similar_to depends on iteration order of top symbols set
    # "EHT" has Levenshtein distance 2 from both "ETH" and "VET"
    assert result["distance"] == 2
    assert result["match_type"] == "symbol_similarity"


@pytest.mark.asyncio
async def test_copycat_unique_symbol():
    """Unique symbol returns clean only if name doesn't match any top symbol."""
    from app.domains.scanner.modules import _check_copycat

    # "XYZ123" itself is unique but "Completely Unique Token" contains "UNI"
    # so name_similarity triggers via exact substring match (name_squatting).
    result = await _check_copycat("XYZ123", "Completely Unique Token")

    # Name contains "UNI" → medium_risk via name_squatting (exact substring match)
    assert result is not None
    assert result["result"] == "medium_risk"
    assert result["match_type"] in ("name_similarity", "name_squatting")


# ── 4. Levenshtein Distance ────────────────────────────────────────────


class TestLevenshtein:
    """Tests for the Levenshtein distance implementation."""

    def test_empty_strings(self):
        from app.domains.scanner.modules import _levenshtein_distance

        assert _levenshtein_distance("", "") == 0

    def test_identical_strings(self):
        from app.domains.scanner.modules import _levenshtein_distance

        assert _levenshtein_distance("hello", "hello") == 0

    def test_one_insertion(self):
        from app.domains.scanner.modules import _levenshtein_distance

        assert _levenshtein_distance("ETH", "ETTH") == 1

    def test_one_substitution(self):
        from app.domains.scanner.modules import _levenshtein_distance

        assert _levenshtein_distance("ETH", "ETT") == 1

    def test_completely_different(self):
        from app.domains.scanner.modules import _levenshtein_distance

        assert _levenshtein_distance("ABC", "XYZ") == 3

    def test_eth_vs_eeth(self):
        """Copycat detection distance check."""
        from app.domains.scanner.modules import _levenshtein_distance

        assert _levenshtein_distance("EETH", "ETH") == 1


# ── 5. Holder Data ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_holder_data_solana():
    """Holder data fetches correctly for Solana tokens."""
    mock_rpc_response = {
        "jsonrpc": "2.0",
        "result": {
            "value": [
                {"address": "Addr1", "amount": "500000", "decimals": 6},
                {"address": "Addr2", "amount": "300000", "decimals": 6},
                {"address": "Addr3", "amount": "200000", "decimals": 6},
            ]
        },
        "id": 1,
    }

    with (
        patch("app.domains.scanner.modules.holders._solana_rpc_call") as mock_rpc,
        patch("app.domains.scanner.modules._core.PREFER_OWN_DATA", False),
    ):
        mock_rpc.return_value = mock_rpc_response

        from app.domains.scanner.modules import _get_holder_data

        result = await _get_holder_data("So11111111111111111111111111111111111111112", "solana")

        assert result is not None
        assert result["check"] == "holders"
        assert result["result"] == "scanned"
        assert result["holder_count"] == 3
        assert len(result["top_holders"]) == 3
        assert result["source"] == "solana_rpc"


@pytest.mark.asyncio
async def test_holder_data_evm():
    """Holder data fetches correctly for EVM tokens via DexScreener."""
    mock_dexscreener = {
        "pairs": [
            {
                "chainId": "ethereum",
                "dexId": "uniswap",
                "baseToken": {"address": "0xtoken", "name": "Token", "symbol": "TKN"},
                "priceUsd": "1.0",
                "holders": 2500,
                "liquidity": {"usd": 100000},
            }
        ]
    }

    with (
        patch("app.domains.scanner.modules._core._fetch_with_fallback") as mock_fallback,
        patch("app.domains.scanner.modules.holders._fetch_json") as mock_fetch_json,
        patch("app.domains.scanner.modules._core.PREFER_OWN_DATA", False),
    ):
        mock_fallback.return_value = (mock_dexscreener, "https://api.dexscreener.com/")
        mock_fetch_json.return_value = {}
        # Clear DexScreener cache to avoid pollution from previous tests
        from app.domains.scanner.modules._core import _DEXSCREENER_CACHE

        _DEXSCREENER_CACHE.clear()

        from app.domains.scanner.modules import _get_holder_data

        result = await _get_holder_data("0xtoken", "ethereum")

        assert result is not None
        assert result["check"] == "holders"
        assert result["result"] == "scanned"
        assert result["holder_count"] == 2500


# ── 6. Deployer Information ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deployer_info_with_explorer():
    """Deployer info fetches correctly from block explorer."""
    mock_explorer = {
        "status": "1",
        "message": "OK",
        "result": [{"contractCreator": "0xDeployerAddress", "txHash": "0xtx"}],
    }

    with (
        patch("app.domains.scanner.modules.deployer._fetch_json") as mock_fetch,
        patch("app.domains.scanner.modules._core._fetch_with_fallback") as mock_fallback,
        patch("app.domains.scanner.modules._core.PREFER_OWN_DATA", False),
    ):
        mock_fetch.return_value = mock_explorer
        mock_fallback.return_value = ({}, "")

        from app.domains.scanner.modules import _get_deployer_info

        result = await _get_deployer_info("0xtoken", "ethereum")

        assert result is not None
        assert result["check"] == "deployer"
        assert result["deployer_address"] == "0xDeployerAddress"


# ── 7. Contract Verification ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_contract_verified_explorer():
    """Contract verification detects verified source on explorer."""
    mock_response = {
        "status": "1",
        "message": "OK",
        "result": [
            {
                "SourceCode": "pragma solidity ^0.8.0; ...",
                "ABI": "[...]",
                "CompilerVersion": "v0.8.20+commit.a1b79de6",
                "OptimizationUsed": "1",
            }
        ],
    }
    with patch(
        "app.domains.scanner.modules.social.community._fetch_json",
        new_callable=lambda: AsyncMock(return_value=mock_response),
    ):
        from app.domains.scanner.modules import _check_contract_verification

        result = await _check_contract_verification("0xtoken", "ethereum")

        assert result is not None
        assert result["check"] == "verified"
        assert result["is_verified"] is True
        assert result["source"] == "explorer"


@pytest.mark.asyncio
async def test_contract_not_verified():
    """Contract verification returns unverified when no source."""
    mock_response = {
        "status": "1",
        "message": "OK",
        "result": [
            {
                "SourceCode": "",
                "ABI": "",
                "CompilerVersion": "",
                "OptimizationUsed": "0",
            }
        ],
    }

    with patch(
        "app.domains.scanner.modules.social.community._fetch_json",
        new_callable=lambda: AsyncMock(return_value=mock_response),
    ):
        from app.domains.scanner.modules import _check_contract_verification

        result = await _check_contract_verification("0xtoken", "ethereum")

        assert result is not None
        assert result["check"] == "verified"
        assert result["is_verified"] is False


# ── 8. Price Consensus ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_price_consensus_with_data():
    """Price consensus fetches and reports price from DexScreener."""
    mock_dexscreener = {
        "pairs": [
            {
                "chainId": "ethereum",
                "dexId": "uniswap",
                "pairAddress": "0xpair",
                "priceUsd": "1.50",
                "volume": {"h24": 50000},
                "liquidity": {"usd": 250000},
                "fdv": 15000000,
                "priceChange": {"h24": 2.5},
            }
        ]
    }

    with (
        patch("app.domains.scanner.modules._core._fetch_with_fallback") as mock_fallback,
        patch("app.domains.scanner.modules._core.PREFER_OWN_DATA", False),
        patch("app.domains.scanner.modules.market.price.PREFER_OWN_DATA", False),
    ):
        mock_fallback.return_value = (mock_dexscreener, "https://api.dexscreener.com/")
        # Clear DexScreener cache to avoid pollution
        from app.domains.scanner.modules._core import _DEXSCREENER_CACHE

        _DEXSCREENER_CACHE.clear()

        from app.domains.scanner.modules import _get_price_consensus

        result = await _get_price_consensus("0xtoken", "ethereum")

        assert result is not None
        assert result["check"] == "price"
        assert result["price_usd"] == 1.50
        assert result["price_consensus"] is True
        assert "dexscreener" in result["sources"]


# ── 9. Spam Check ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_spam_check_not_spam():
    """Spam registry returns clean for unknown tokens."""
    with patch("app.spam_registry.get_spam_registry") as mock_registry:
        # _check_spam calls check_token synchronously (not awaited)
        mock_registry_instance = MagicMock()
        mock_registry_instance.check_token.return_value = {
            "is_spam": False,
            "is_sanctioned": False,
            "is_known_scam": False,
            "sources_checked": ["registry"],
        }
        mock_registry.return_value = mock_registry_instance

        from app.domains.scanner.modules import _check_spam

        result = await _check_spam("0xtoken", "ethereum")

        assert result is not None
        assert result["check"] == "spam"
        assert result["result"] == "scanned"
        assert result["is_spam"] is False


# ── 10. Liquidity Lock Check ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_liquidity_lock_dexscreener():
    """Liquidity lock check returns data from DexScreener fallback."""
    mock_dexscreener = {
        "pairs": [
            {
                "chainId": "ethereum",
                "dexId": "uniswap",
                "pairAddress": "0xpair",
                "liquidity": {"usd": 50000},
                "pairCreatedAt": 1700000000000,
            }
        ]
    }

    # The HoneypotDetector module exists, so we patch analyze_token to raise
    # an exception, forcing the function to use the DexScreener fallback.
    with (
        patch("app.domains.scanner.modules._core._fetch_with_fallback") as mock_fallback,
        patch(
            "app.domains.detectors.smart_contract_honeypot_detector.HoneypotDetector.analyze_token",
            side_effect=ImportError("No RPC available in test"),
        ),
        patch("app.domains.scanner.modules._core.PREFER_OWN_DATA", False),
        patch("app.domains.scanner.modules.holders.PREFER_OWN_DATA", False),
        patch("app.domains.scanner.modules.holders._gmgn_token_info", AsyncMock(return_value=None)),
    ):
        mock_fallback.return_value = (mock_dexscreener, "https://api.dexscreener.com/")
        # Clear DexScreener cache to avoid pollution
        from app.domains.scanner.modules._core import _DEXSCREENER_CACHE

        _DEXSCREENER_CACHE.clear()

        from app.domains.scanner.modules import _check_liquidity_lock_multi

        result = await _check_liquidity_lock_multi("0xtoken", "ethereum")

        assert result is not None
        assert result["check"] == "liquidity"
        assert result["liquidity_usd"] == 50000
        assert result["pool_age_hours"] > 0


# ── Legacy stubs return pending ────────────────────────────────────────


@pytest.mark.parametrize(
    ("module_func", "func_kwargs"),
    [
        ("_check_blockscout", {"token_address": "0xtoken", "chain": "ethereum"}),
        ("_check_token_sniffer", {"token_address": "0xtoken", "chain": "ethereum"}),
        ("_check_defillama_context", {"token_address": "0xtoken", "chain": "ethereum"}),
        ("_check_coingecko_price", {"token_address": "0xtoken", "chain": "ethereum"}),
        # _verify_mint_consensus lives in market.blockchain but is not re-exported
        # from app.domains.scanner.modules — skip in this test
    ],
)
@pytest.mark.asyncio
async def test_legacy_stub_returns_pending(module_func, func_kwargs):
    """Legacy stub modules return a result status."""
    import importlib

    modules_mod = importlib.import_module("app.domains.scanner.modules")
    func = getattr(modules_mod, module_func)

    result = await func(**func_kwargs)

    assert result is not None
    # Some stubs/wrappers now return "scanned" after implementing real logic
    assert result.get("result") in ("pending", "unavailable", "scanned")


# ── Solana RPC Helpers ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_solana_rpc_call_success():
    """Solana RPC call succeeds with first endpoint."""
    mock_data = {"jsonrpc": "2.0", "result": {"value": 123}, "id": 1}

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_post.return_value = _mock_httpx_response(mock_data)

        from app.domains.scanner.modules import _solana_rpc_call

        result = await _solana_rpc_call("getBalance", ["some_address"])

        assert result is not None
        assert result["result"]["value"] == 123


@pytest.mark.asyncio
async def test_solana_rpc_call_all_fail():
    """Solana RPC call returns None when all endpoints fail."""
    with patch("httpx.AsyncClient.post") as mock_post:
        import httpx

        mock_post.side_effect = httpx.TimeoutException("All endpoints timed out")

        from app.domains.scanner.modules import _solana_rpc_call

        result = await _solana_rpc_call("getBalance", ["some_address"])

        assert result is None


# ── Top symbols set ────────────────────────────────────────────────────


def test_top_symbols_contains_major_coins():
    """Top symbols set includes major cryptocurrency tickers."""
    from app.domains.scanner.modules import _get_top_symbols

    symbols = _get_top_symbols()

    assert "ETH" in symbols
    assert "BTC" in symbols
    assert "SOL" in symbols
    assert "USDT" in symbols
    assert "BNB" in symbols
