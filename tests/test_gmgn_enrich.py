"""Tests for GMGN scan-report enrichment (app/domains/scanner/gmgn_enrich.py).

Pure unit tests — the GMGN provider is mocked; no network, no DB. Covers:
  * enrich_scan_with_gmgn attaches a normalized ``gmgn`` dict on success
  * fail-open: provider failure / timeout returns the scan result unchanged
  * the /scan formatter renders the Smart Money section only when present

The mocked provider payloads mirror the actual return shapes of
``app/databus/gmgn_provider.py`` (get_holder_analysis / get_smart_money).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.domains.scanner.gmgn_enrich import enrich_scan_with_gmgn

TOKEN = "0x1234567890abcdef1234567890abcdef12345678"

HOLDERS_PAYLOAD = {
    "address": TOKEN,
    "chain": "solana",
    "total_holders": 100,
    "top_10_pct": None,
    "top_holders": [
        {
            "address": "0x1111111111111111111111111111111111111111",
            "amount_percentage": 12.5,
            "realized_profit": 500,
            "unrealized_profit": 200,
            "profit_change": 2.5,
            "is_insider": True,
            "is_smart_money": True,
        },
        {
            "address": "0x2222222222222222222222222222222222222222",
            "amount_percentage": 3.2,
            "realized_profit": 0,
            "unrealized_profit": 10,
            "profit_change": 1.1,
            "is_insider": False,
            "is_smart_money": False,
        },
    ],
    "sniper_count": 3,
    "insider_count": 1,
    "fresh_wallet_pct": 0,
    "source": "gmgn",
    "timestamp": "2026-08-13T00:00:00+00:00",
}

SMART_MONEY_PAYLOAD = {
    "address": TOKEN,
    "chain": "solana",
    "smart_money_activity": [
        {
            "wallet": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "wallet_tags": ["Smart Money"],
            "buy_7d": 5,
            "sell_7d": 1,
        },
        {
            "wallet": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "wallet_tags": ["Smart Money"],
            "buy_7d": 1,
            "sell_7d": 4,
        },
    ],
    "kol_trades": [],
    "smart_money_count": 2,
    "kol_count": 0,
    "buy_pressure": 0,
    "total_profit_pnl": 123.4,
    "winrate": 0.62,
    "sol_balance": 3.5,
    "buy_7d": 6,
    "tags": [],
    "source": "gmgn",
    "timestamp": "2026-08-13T00:00:00+00:00",
}


@pytest.fixture
def scan_result() -> dict[str, Any]:
    """A minimal scan-result dict in the legacy formatter shape."""
    return {
        "address": TOKEN,
        "chain": "solana",
        "name": "Test Token",
        "symbol": "TST",
        "risk_score": 30,
        "top_holders": [],
        "socials": {},
        "bundle_detected": False,
        "errors": [],
    }


def _mock_provider(**overrides) -> AsyncMock:
    """Provider returning HOLDERS_PAYLOAD / SMART_MONEY_PAYLOAD by default."""
    provider = AsyncMock()
    provider.get_holder_analysis.return_value = HOLDERS_PAYLOAD
    provider.get_smart_money.return_value = SMART_MONEY_PAYLOAD
    for attr, value in overrides.items():
        setattr(provider, attr, value)
    return provider


@pytest.fixture(autouse=True)
def gmgn_provider_mock():
    """Patch get_gmgn_provider for every test in this module."""
    patcher = patch("app.domains.scanner.gmgn_enrich.get_gmgn_provider")
    with patcher as m:
        m.return_value = _mock_provider()
        yield m


# ── enrich_scan_with_gmgn ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enrich_attaches_gmgn_on_success(scan_result):
    result = await enrich_scan_with_gmgn(scan_result, TOKEN, "solana")

    assert result is scan_result  # same object, mutated in place
    gmgn = result["gmgn"]

    # holders — normalized from get_holder_analysis
    holders = gmgn["holders"]
    assert holders["top_holder_pct"] == pytest.approx(15.7)  # 12.5 + 3.2 (top-10 sum)
    assert holders["holder_count"] == 100
    assert holders["insider_count"] == 1
    assert holders["sniper_count"] == 3
    assert holders["source"] == "gmgn"

    # smart_money — normalized from get_smart_money
    smart = gmgn["smart_money"]
    assert smart["net_bought"] == pytest.approx(1.0)  # (5-1) + (1-4)
    assert smart["smart_money_count"] == 2
    assert len(smart["top_smart_money_wallets"]) == 2
    w1, w2 = smart["top_smart_money_wallets"]
    assert w1["direction"] == "BUY"
    assert w1["address"] == "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert w1["net"] == pytest.approx(4.0)
    assert w2["direction"] == "SELL"
    assert w2["net"] == pytest.approx(-3.0)


@pytest.mark.asyncio
async def test_enrich_returns_unchanged_on_provider_failure(scan_result, gmgn_provider_mock):
    provider = gmgn_provider_mock
    # provider.get_gmgn_provider() returns the AsyncMock — make both fetches raise.
    provider.return_value.get_holder_analysis.side_effect = RuntimeError("gmgn down")
    provider.return_value.get_smart_money.side_effect = RuntimeError("gmgn down")

    result = await enrich_scan_with_gmgn(scan_result, TOKEN, "solana")

    assert result is scan_result
    assert "gmgn" not in result


@pytest.mark.asyncio
async def test_enrich_returns_unchanged_when_provider_singleton_raises(scan_result, gmgn_provider_mock):
    gmgn_provider_mock.side_effect = RuntimeError("singleton init failed")

    result = await enrich_scan_with_gmgn(scan_result, TOKEN, "solana")

    assert result is scan_result
    assert "gmgn" not in result


@pytest.mark.asyncio
async def test_enrich_returns_unchanged_on_timeout(scan_result, gmgn_provider_mock):
    async def _slow(*args, **kwargs):
        await asyncio.sleep(5)
        return HOLDERS_PAYLOAD

    gmgn_provider_mock.return_value.get_holder_analysis.side_effect = _slow
    gmgn_provider_mock.return_value.get_smart_money.side_effect = _slow

    with patch("app.domains.scanner.gmgn_enrich._GMGN_TIMEOUT_S", 0.05):
        result = await enrich_scan_with_gmgn(scan_result, TOKEN, "solana")

    assert result is scan_result
    assert "gmgn" not in result


# ── format_scan_report rendering ─────────────────────────────────────────


def _render(result: dict[str, Any]) -> str:
    from app.domains.telegram.rugmunchbot.formatters._sentinel import format_scan_report

    return format_scan_report(result)


@pytest.mark.asyncio
async def test_renderer_emits_smart_money_section_when_gmgn_present(scan_result):
    await enrich_scan_with_gmgn(scan_result, TOKEN, "solana")

    report = _render(scan_result)

    assert "Smart Money / Holders" in report
    assert "Top-10 Supply: <b>15.7%</b>" in report
    assert "Holders: <b>100</b>" in report
    assert "Smart-Money Net: <b>+1</b>" in report
    # 1-3 notable smart-money wallet moves, short address + direction
    assert "0xaaaa...aaaa BUY" in report
    assert "0xbbbb...bbbb SELL" in report


@pytest.mark.asyncio
async def test_renderer_omits_smart_money_section_when_absent(scan_result):
    report = _render(scan_result)

    assert "Smart Money / Holders" not in report
    assert "Top-10 Supply" not in report
    assert "Smart-Money Net" not in report
