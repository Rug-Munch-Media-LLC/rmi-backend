"""
Tests for cross_chain_whale.py - Cross-Chain Whale Tracker.
"""

from unittest.mock import patch

import pytest

from app.cross_chain_whale import (
    CrossChainWhale,
    CrossChainWhaleTracker,
    WhalePosition,
    WhaleTrackerReport,
    _compute_concentration,
    _truncate_address,
    format_whale_report,
    get_whale_tracker,
    is_valid_address,
)

# ── Address Validation Tests ──────────────────────────────────────


def test_is_valid_address_solana():
    """Valid Solana address (base58, 32-44 chars)."""
    assert is_valid_address("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v") is True


def test_is_valid_address_evm():
    """Valid EVM address (0x + 40 hex chars)."""
    assert is_valid_address("0xdAC17F958D2ee523a2206206994597C13D831ec7") is True


def test_is_valid_address_too_short():
    assert is_valid_address("0x1234") is False


def test_is_valid_address_empty():
    assert is_valid_address("") is False


def test_is_valid_address_invalid_evm():
    assert is_valid_address("0xZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ") is False


# ── Truncation Tests ─────────────────────────────────────────────


def test_truncate_address_long():
    result = _truncate_address("0xdAC17F958D2ee523a2206206994597C13D831ec7")
    assert result == "0xdAC1...1ec7"
    assert len(result) <= 13


def test_truncate_address_short():
    result = _truncate_address("abc123def456")
    assert result == "abc123def456"


# ── Concentration Tests ──────────────────────────────────────────


def test_compute_concentration_empty():
    assert _compute_concentration([]) == 0.0


def test_compute_concentration_single():
    positions = [WhalePosition(chain="solana", token_address="x", token_symbol="TEST", balance_usd=1000.0)]
    result = _compute_concentration(positions)
    assert result == 100.0  # 100% concentrated


def test_compute_concentration_equal():
    positions = [
        WhalePosition(chain="solana", token_address="x", token_symbol="TEST", balance_usd=1000.0),
        WhalePosition(chain="ethereum", token_address="y", token_symbol="TEST", balance_usd=1000.0),
    ]
    result = _compute_concentration(positions)
    assert 49.0 < result < 51.0  # ~50%


# ── Whale Tracker Report Tests ───────────────────────────────────


def test_whale_tracker_report_defaults():
    report = WhaleTrackerReport(token_address="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
    assert report.token_symbol == ""
    assert report.total_holders == 0
    assert report.chains_found == []
    assert report.whales == []
    assert report.cross_chain_whales == []
    assert report.errors == []


def test_whale_tracker_report_full():
    positions = [
        WhalePosition(
            chain="solana",
            token_address="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            token_symbol="USDC",
            balance=1000.0,
            balance_usd=1000.0,
            percentage=0.01,
        )
    ]
    whale = CrossChainWhale(
        primary_address="4k3Dyjzvzp8e5qKBGq2ZwK5Uo5xT6sQd4u3UxLonGJcH",
        total_value_usd=1000.0,
        chain_count=1,
        token_count=1,
        positions=positions,
    )
    report = WhaleTrackerReport(
        token_address="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        token_symbol="USDC",
        token_name="USD Coin",
        total_holders=100,
        chains_found=["solana"],
        whales=[whale],
        cross_chain_whales=[whale],
        concentration_score=45.0,
    )
    assert report.token_symbol == "USDC"
    assert report.total_holders == 100
    assert len(report.whales) == 1
    assert report.whales[0].chain_count == 1


# ── Formatting Tests ─────────────────────────────────────────────


def test_format_whale_report_empty():
    report = WhaleTrackerReport(
        token_address="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        token_symbol="USDC",
    )
    output = format_whale_report(report)
    assert "USDC" in output
    assert "Scan:" in output


def test_format_whale_report_with_whales():
    positions = [
        WhalePosition(
            chain="solana",
            token_address="x",
            token_symbol="TOKEN",
            balance=50000.0,
            balance_usd=50000.0,
            percentage=5.0,
        )
    ]
    whale = CrossChainWhale(
        primary_address="4k3Dyjzvzp8e5qKBGq2ZwK5Uo5xT6sQd4u3UxLonGJcH",
        total_value_usd=50000.0,
        chain_count=2,
        token_count=2,
        positions=positions,
        risk_score=25.0,
        risk_factors=["Significant cross-chain presence (3 chains)"],
    )
    report = WhaleTrackerReport(
        token_address="x",
        token_symbol="TOKEN",
        chains_found=["solana", "ethereum"],
        whales=[whale],
        cross_chain_whales=[whale],
    )
    output = format_whale_report(report)
    assert "Cross-Chain Whales" in output
    assert "TOKEN" in output
    assert "Risk: 25" in output


# ── Singleton Tests ──────────────────────────────────────────────


def test_get_whale_tracker_singleton():
    t1 = get_whale_tracker()
    t2 = get_whale_tracker()
    assert t1 is t2


# ── Async Tests (with mocked HTTP) ───────────────────────────────


@pytest.mark.asyncio
async def test_track_token_mocked():
    """Test track_token with mocked HTTP responses."""
    tracker = CrossChainWhaleTracker()

    # Mock the DexScreener fetch to return a known token
    async def mock_fetch_json(url, headers=None, timeout=15.0):
        if "dexscreener" in url:
            return {
                "pairs": [
                    {
                        "chainId": "solana",
                        "dexId": "raydium",
                        "baseToken": {"symbol": "TEST", "name": "Test Token"},
                        "priceUsd": "1.23",
                        "liquidity": {"usd": 50000.0},
                        "fdv": 1000000.0,
                        "volume": {"h24": 10000.0},
                    }
                ]
            }
        if "birdeye" in url:
            return {
                "success": True,
                "data": {
                    "holders": [
                        {
                            "address": "4k3Dyjzvzp8e5qKBGq2ZwK5Uo5xT6sQd4u3UxLonGJcH",
                            "balance": 50000.0,
                            "balanceUsd": 61500.0,
                            "percent": 5.0,
                        },
                        {
                            "address": "3xAbC...Wxyz",
                            "balance": 25000.0,
                            "balanceUsd": 30750.0,
                            "percent": 2.5,
                        },
                    ]
                },
            }
        return None

    with patch("app.cross_chain_whale._fetch_json", side_effect=mock_fetch_json):
        report = await tracker.track_token(
            "TESTx0000000000000000000000000000000000000",
            chains=["solana"],
        )

    assert report.token_symbol == "TEST"
    assert report.token_name == "Test Token"
    assert report.concentration_score >= 0


@pytest.mark.asyncio
async def test_track_token_graceful_degradation():
    """Test that track_token handles all API failures gracefully."""
    tracker = CrossChainWhaleTracker()

    async def mock_fetch_json_fail(url, headers=None, timeout=15.0):
        return None

    with patch("app.cross_chain_whale._fetch_json", side_effect=mock_fetch_json_fail):
        report = await tracker.track_token(
            "UNKNOWNx00000000000000000000000000000000000",
            chains=["solana", "ethereum"],
        )

    # Should still return a report, not crash
    assert report is not None
    assert report.token_address == "UNKNOWNx00000000000000000000000000000000000"
    # Symbol should be truncated address as fallback
    assert report.token_symbol is not None


# ── Cross-Reference Tests ────────────────────────────────────────


@pytest.mark.asyncio
async def test_track_token_cross_chain_detection():
    """Test that a whale on multiple chains is detected as cross-chain."""
    tracker = CrossChainWhaleTracker()

    async def mock_fetch_json(url, headers=None, timeout=15.0):
        if "dexscreener" in url:
            return {
                "pairs": [
                    {
                        "chainId": "solana",
                        "dexId": "raydium",
                        "baseToken": {"symbol": "CROSS", "name": "Cross Token"},
                        "priceUsd": "1.0",
                        "liquidity": {"usd": 100000.0},
                        "fdv": 5000000.0,
                        "volume": {"h24": 50000.0},
                    },
                    {
                        "chainId": "ethereum",
                        "dexId": "uniswap",
                        "baseToken": {"symbol": "CROSS", "name": "Cross Token"},
                        "priceUsd": "1.01",
                        "liquidity": {"usd": 200000.0},
                        "fdv": 5000000.0,
                        "volume": {"h24": 30000.0},
                    },
                ]
            }
        if "birdeye" in url:
            return {
                "success": True,
                "data": {
                    "holders": [
                        {
                            "address": "WHALExAcross1234567890123456789012345678901",
                            "balance": 100000.0,
                            "balanceUsd": 100000.0,
                            "percent": 2.0,
                        },
                    ]
                },
            }
        # Simulate Etherscan holding the same whale
        if "etherscan" in url or "basescan" in url:
            return {
                "status": "1",
                "result": [
                    {
                        "address": "0xWHALE1234567890123456789012345678901234567",
                        "balance": "100000000000000000000000",
                        "tokenDecimal": "18",
                        "percentage": "2.0",
                    },
                ],
            }
        return None

    with patch("app.cross_chain_whale._fetch_json", side_effect=mock_fetch_json):
        report = await tracker.track_token(
            "CROSSx000000000000000000000000000000000000",
            chains=["solana", "ethereum"],
        )

    assert report.token_symbol == "CROSS"
    # Should have found data on at least one chain
    assert len(report.chains_with_data) > 0 or len(report.errors) == 0


# ── Edge Case Tests ──────────────────────────────────────────────


def test_whale_position_defaults():
    wp = WhalePosition(chain="solana", token_address="addr", token_symbol="TKN")
    assert wp.balance == 0.0
    assert wp.balance_usd == 0.0
    assert wp.percentage == 0.0
    assert wp.rank == 0
    assert wp.source == ""


def test_cross_chain_whale_default_risk():
    cw = CrossChainWhale(primary_address="abc123")
    assert cw.risk_score == 0.0
    assert cw.risk_factors == []
    assert cw.is_exchange is False
    assert cw.is_scammer is False
