"""Tests for wash-trading / fake-volume enrichment.

``app/domains/scanner/wash_trading_enrich.py`` — pure unit tests with
injectable fetchers: no network, no DB, no real providers. Covers:
  * self-trade detection (same maker BUY+SELL within a short window)
  * volume concentration flagging (one maker dominating trade volume)
  * legacy cycle-detector wash_score feeding into the flag
  * fail-open contract ({"wash_trading": False} on error / empty payload)
  * wash_signal rendering ("wash trading: 40% self-trades" / "suspicious
    volume" / None)

Trade fixtures mirror the normalized rows returned by
``app/databus/gmgn_provider.py`` ``get_token_trades`` (maker, timestamp in
epoch ms, side, amount_usd).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.domains.scanner.wash_trading_enrich import detect_wash_trading, wash_signal

TOKEN = "So11111111111111111111111111111111111111112"


def _trade(
    maker: str,
    side: str,
    usd: float,
    ts: int,
    **extra: Any,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "tx_hash": f"tx-{maker}-{side}-{ts}",
        "timestamp": ts,
        "side": side,
        "amount_usd": usd,
        "maker": maker,
        "source": "gmgn",
    }
    row.update(extra)
    return row


def _fetchers(
    trades: Any,
    volume: Any = None,
    cycle: Any = None,
) -> dict[str, Any]:
    """Build an injectable fetchers dict (AsyncMock-based)."""
    return {
        "trades": AsyncMock(return_value=trades),
        "volume": AsyncMock(return_value=volume),
        "cycle": AsyncMock(return_value=cycle),
    }


# ── detect_wash_trading: self-trades ──────────────────────────────────────


@pytest.mark.asyncio
async def test_detects_self_trade_round_trip():
    """Same maker BUY+sell within the window flags wash trading with a pct."""
    trades = [
        _trade("A", "buy", 40.0, 1000),
        _trade("A", "sell", 40.0, 2000),  # 1s later — same wallet both sides
        _trade("B", "buy", 20.0, 3000),
    ]
    result = await detect_wash_trading(TOKEN, "solana", fetchers=_fetchers(trades))

    assert set(result) == {"wash_trading", "wash_volume_pct", "self_trade_count", "evidence"}
    assert result["wash_trading"] is True
    assert result["self_trade_count"] == 1
    # wash = min(buy 40, sell 40) = 40; total = 40+40+20 = 100 → 40%
    assert result["wash_volume_pct"] == pytest.approx(40.0)
    assert "round-tripped BUY/SELL" in (result["evidence"] or "")
    assert wash_signal(result) == "wash trading: 40% self-trades"


@pytest.mark.asyncio
async def test_self_trades_outside_window_are_not_matched():
    """A round-trip slower than the window is NOT counted as self-trade."""
    trades = [
        _trade("A", "buy", 40.0, 100_000),
        _trade("A", "sell", 40.0, 500_000),  # 400s later — window is 300s
        _trade("B", "buy", 20.0, 900_000),
    ]
    result = await detect_wash_trading(TOKEN, "solana", fetchers=_fetchers(trades))

    assert result["self_trade_count"] == 0
    assert result["wash_volume_pct"] == 0.0


@pytest.mark.asyncio
async def test_neutral_when_no_self_trades_and_diverse_volume():
    """Distinct makers with low concentration stay unflagged."""
    trades = [
        _trade("A", "buy", 25.0, 1000),
        _trade("B", "buy", 25.0, 2000),
        _trade("C", "sell", 25.0, 3000),
        _trade("D", "sell", 25.0, 4000),
    ]
    result = await detect_wash_trading(TOKEN, "solana", fetchers=_fetchers(trades, cycle=0))

    assert result["wash_trading"] is False
    assert result["self_trade_count"] == 0
    assert result["wash_volume_pct"] == 0.0
    assert wash_signal(result) is None


# ── detect_wash_trading: volume concentration ─────────────────────────────


@pytest.mark.asyncio
async def test_flags_concentration_without_self_trades():
    """One maker owning >40% of trade volume flags "suspicious volume"."""
    trades = [
        _trade("WHALE", "buy", 60.0, 1000),
        _trade("B", "buy", 20.0, 2000),
        _trade("C", "sell", 20.0, 3000),
    ]
    result = await detect_wash_trading(TOKEN, "solana", fetchers=_fetchers(trades))

    assert result["wash_trading"] is True
    assert result["self_trade_count"] == 0
    assert result["wash_volume_pct"] == 0.0
    assert "top maker" in (result["evidence"] or "")
    assert "60%" in (result["evidence"] or "")
    assert wash_signal(result) == "suspicious volume"


# ── detect_wash_trading: legacy cycle detector signal ─────────────────────


@pytest.mark.asyncio
async def test_legacy_cycle_detector_score_flags():
    """wash_score above threshold from the legacy detector flags the token."""
    report = SimpleNamespace(wash_score=30.0)
    result = await detect_wash_trading(TOKEN, "solana", fetchers=_fetchers([], cycle=report))

    assert result["wash_trading"] is True
    assert result["self_trade_count"] == 0
    assert result["wash_volume_pct"] is None
    assert "legacy cycle detector wash_score 30/100" in (result["evidence"] or "")
    assert wash_signal(result) == "suspicious volume"


@pytest.mark.asyncio
async def test_low_cycle_score_does_not_flag():
    report = SimpleNamespace(wash_score=5.0)
    result = await detect_wash_trading(TOKEN, "solana", fetchers=_fetchers([], cycle=report))

    assert result["wash_trading"] is False


# ── detect_wash_trading: fail-open / empty / failure ──────────────────────


@pytest.mark.asyncio
async def test_empty_trades_is_neutral():
    result = await detect_wash_trading(TOKEN, "solana", fetchers=_fetchers([]))

    assert result["wash_trading"] is False
    assert result["self_trade_count"] == 0
    assert result["wash_volume_pct"] is None
    assert wash_signal(result) is None


@pytest.mark.asyncio
async def test_missing_address_is_neutral():
    result = await detect_wash_trading("", "solana", fetchers=_fetchers([]))

    assert result == {"wash_trading": False}


@pytest.mark.asyncio
async def test_fetcher_raise_returns_neutral_dict():
    """Synchronous raise from a fetcher → exact fail-open dict."""

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("provider down")

    result = await detect_wash_trading(
        TOKEN,
        "solana",
        fetchers={"trades": _boom, "volume": _boom, "cycle": _boom},
    )

    assert result == {"wash_trading": False}


@pytest.mark.asyncio
async def test_async_fetcher_failure_keeps_other_signals():
    """One failing fetch degrades gracefully (others still contribute)."""

    async def _raise(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("trades down")

    result = await detect_wash_trading(
        TOKEN,
        "solana",
        fetchers={
            "trades": _raise,
            "volume": AsyncMock(return_value=50_000.0),
            "cycle": AsyncMock(return_value=None),
        },
    )

    assert result["wash_trading"] is False
    assert result["wash_volume_pct"] is None
    assert "24h volume $50,000" in (result["evidence"] or "")


@pytest.mark.asyncio
async def test_volume_source_present_in_evidence():
    trades = [_trade("A", "buy", 25.0, 1000), _trade("B", "sell", 25.0, 2000)]
    result = await detect_wash_trading(TOKEN, "solana", fetchers=_fetchers(trades, volume=123_456.0))

    assert "24h volume $123,456" in (result["evidence"] or "")


# ── wash_signal: pure renderer ────────────────────────────────────────────


def test_wash_signal_neutral_and_error_dicts_return_none():
    assert wash_signal(None) is None
    assert wash_signal({}) is None
    assert wash_signal({"wash_trading": False, "evidence": "no data"}) is None


def test_wash_signal_self_trade_percent():
    result = {"wash_trading": True, "wash_volume_pct": 40.0, "self_trade_count": 1}
    assert wash_signal(result) == "wash trading: 40% self-trades"


def test_wash_signal_self_trade_count_fallback():
    result = {"wash_trading": True, "wash_volume_pct": None, "self_trade_count": 3}
    assert wash_signal(result) == "wash trading: 3 self-trades"


def test_wash_signal_suspicious_volume_when_no_self_trades():
    result = {"wash_trading": True, "wash_volume_pct": 0.0, "self_trade_count": 0}
    assert wash_signal(result) == "suspicious volume"
