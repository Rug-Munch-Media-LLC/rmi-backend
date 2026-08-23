"""Pure unit tests for the chat tool-use layer (live data grounding).

Covers ``app/domains/chat_tools.py`` + the ``chat.py`` integration helpers:
  - ``_detect_tool_intent``: classifier → price/scan/ta/market/trace/None.
  - ``_extract_price_symbol`` / ``_extract_first_address`` / ``_chain_for_address``.
  - Executors (``_exec_price`` / ``_exec_scan`` / ``_exec_ta`` / ``_exec_market``):
    fail-open → None when the underlying fetch raises; format output when
    data is present.
  - ``_run_tool_for_intent``: dispatch + 8s timeout, returns None on failure.
  - Route integration (``_fetch_tool_context`` + ``_inject_tool_result``): the
    tool result is injected into the system context and omitted when the
    executor returns None.

No network, DB, or LLM calls — all data modules are mocked.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from app.domains import chat_tools
from app.domains.chat_tools import (
    _chain_for_address,
    _detect_tool_intent,
    _exec_market,
    _exec_price,
    _exec_scan,
    _exec_ta,
    _extract_first_address,
    _extract_price_symbol,
    _run_tool_for_intent,
)
from app.routers.chat import _fetch_tool_context, _inject_tool_result

_EVM = "0x" + "a" * 40
_SOL = "D8pom1W4v6j8kQkfQq2mNnXxYyZzAaBbCcDdEeFfGgHh"  # 44-char base58-like


def _make_candles(n: int = 20, start: float = 100.0) -> list[dict]:
    return [
        {
            "timestamp": i,
            "open": start + i,
            "high": start + i + 1,
            "low": start + i - 1,
            "close": start + i,
            "volume": 100.0,
            "trades": 5,
        }
        for i in range(n)
    ]


# ── 1. Intent classifier ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        # price — phrase + symbol/name, or a lone $SYMBOL
        ("what is the price of solana", "price"),
        ("price of $PEPE", "price"),
        ("how much is bitcoin", "price"),
        ("what's $DOGE worth", "price"),
        ("$BTC", "price"),
        ("btc price", "price"),
        ("how much is " + _EVM, "price"),  # address + explicit price phrase
        # scan — security keywords + address/symbol; a bare address without a
        # security keyword or forensics intent is now None (trace owns forensics)
        (_EVM, None),
        (_SOL, None),
        (f"scan this token {_EVM}", "scan"),
        (f"is {_EVM} a honeypot", "scan"),
        (f"rug pull check on {_EVM}", "scan"),
        ("is $PEPE a rug", "scan"),
        # ta — explicit technical-analysis keywords
        ("rsi of btc", "ta"),
        ("show me the macd chart for eth", "ta"),
        ("support and resistance for sol", "ta"),
        ("what does the technical analysis say about btc", "ta"),
        # market
        ("how's the market", "market"),
        ("market cap of bitcoin", "market"),
        ("fear and greed index", "market"),
        # None — no clear intent
        ("what is a honeypot", None),
        ("hello there", None),
        ("what is bitcoin", None),
        ("tell me about ethereum", None),
        ("thanks!", None),
        ("", None),
    ],
)
def test_detect_tool_intent_classifies(message: str, expected: str | None) -> None:
    assert _detect_tool_intent(message) == expected


# ── 2. Extraction helpers ─────────────────────────────────────────────


def test_extract_price_symbol_prefers_dollar_ticker() -> None:
    assert _extract_price_symbol("price of $PEPE") == "PEPE"
    assert _extract_price_symbol("what is $CRM worth") == "CRM"


def test_extract_price_symbol_known_names() -> None:
    assert _extract_price_symbol("how much is bitcoin") == "BTC"
    assert _extract_price_symbol("solana price") == "SOL"
    assert _extract_price_symbol("tell me about ethereum") == "ETH"


def test_extract_price_symbol_none_when_unknown() -> None:
    assert _extract_price_symbol("hello there") is None
    assert _extract_price_symbol("") is None


def test_extract_first_address() -> None:
    assert _extract_first_address(f"scan {_EVM}") == _EVM
    assert _extract_first_address(f"check {_SOL}") == _SOL
    assert _extract_first_address("no address here") is None


def test_chain_for_address() -> None:
    assert _chain_for_address(_EVM) == "ethereum"
    assert _chain_for_address(_SOL) == "solana"


# ── 3. Executors fail open (never raise) ──────────────────────────────


async def test_exec_price_returns_none_when_fetch_raises() -> None:
    """All price sources failing → executor returns None (fail-open).

    2026-08-23: the fallback chain grew (coingecko → dexscreener →
    coincap/cmc). Mocking only coingecko leaked a LIVE coinmarketcap
    call into unit tests. Every source in the chain must be neutralized.
    """
    with (
        mock.patch("app.domains.chat_tools._extract_price_symbol", return_value="BTC"),
        mock.patch("app.coingecko_connector.get_coingecko_connector", side_effect=RuntimeError("boom")),
        mock.patch("app.databus.provider_implementations.price._dexscreener_price", side_effect=RuntimeError("boom")),
        mock.patch("app.domains.chat_tools.price._fetch_coincap_price", side_effect=RuntimeError("boom")),
        mock.patch("app.domains.chat_tools.price._fetch_cmc_price", side_effect=RuntimeError("boom")),
    ):
        assert await _exec_price("price of btc") is None


async def test_exec_price_returns_none_when_no_price_data() -> None:
    """Every source returning nothing → None (no live fallback leakage)."""
    conn = mock.MagicMock()
    conn.get_token_price = mock.AsyncMock(return_value=None)
    with (
        mock.patch("app.domains.chat_tools._extract_price_symbol", return_value="BTC"),
        mock.patch("app.coingecko_connector.get_coingecko_connector", return_value=conn),
        mock.patch("app.databus.provider_implementations.price._dexscreener_price", return_value=None),
        mock.patch("app.domains.chat_tools.price._fetch_coincap_price", return_value=None),
        mock.patch("app.domains.chat_tools.price._fetch_cmc_price", return_value=None),
    ):
        assert await _exec_price("price of btc") is None


async def test_exec_price_formats_live_line() -> None:
    conn = mock.MagicMock()
    conn.get_token_price = mock.AsyncMock(return_value={"usd": 67000.5, "usd_24h_change": 1.25})
    with (
        mock.patch("app.domains.chat_tools._extract_price_symbol", return_value="BTC"),
        mock.patch("app.coingecko_connector.get_coingecko_connector", return_value=conn),
    ):
        text = await _exec_price("price of btc")
    assert text is not None
    assert "BTC" in text
    assert "67,000" in text
    assert "+1.2%" in text


async def test_exec_scan_returns_none_when_scanner_raises() -> None:
    with (
        mock.patch("app.domains.chat_tools._extract_first_address", return_value=_EVM),
        mock.patch("app.domains.scanner.service.scan.scan_token", new=mock.AsyncMock(side_effect=RuntimeError("boom"))),
    ):
        assert await _exec_scan(f"scan {_EVM}") is None


async def test_exec_scan_formats_risk_summary() -> None:
    fake_result = SimpleNamespace(safety_score=23, risk_flags=["honeypot", "concentrated_holders"])
    with (
        mock.patch("app.domains.chat_tools._extract_first_address", return_value=_EVM),
        mock.patch("app.domains.scanner.service.scan.scan_token", new=mock.AsyncMock(return_value=fake_result)),
        mock.patch("app.domains.scanner.service.scan._risk_level_from_safety", return_value="HIGH RISK"),
    ):
        text = await _exec_scan(f"scan {_EVM}")
    assert text is not None
    assert "23/100" in text
    assert "HIGH RISK" in text
    assert "honeypot" in text


async def test_exec_ta_returns_none_when_fetch_raises() -> None:
    with (
        mock.patch("app.domains.chat_tools._extract_price_symbol", return_value="BTC"),
        mock.patch("app.databus.ohlcv_engine.fetch_ohlcv", new=mock.AsyncMock(side_effect=RuntimeError("boom"))),
    ):
        assert await _exec_ta("rsi of btc") is None


async def test_exec_ta_uses_ta_summary_when_available() -> None:
    fake_summary = {"rsi": 60.5, "signal": "bullish"}
    with (
        mock.patch("app.domains.chat_tools._extract_price_symbol", return_value="BTC"),
        mock.patch(
            "app.databus.ohlcv_engine.fetch_ohlcv",
            new=mock.AsyncMock(
                return_value={
                    "candles": _make_candles(),
                    "summary": {"current_price": 110.0, "price_change_pct": 10.0},
                }
            ),
        ),
        mock.patch("app.domains.chat_tools.ta_summary", return_value=fake_summary),
        mock.patch("app.domains.chat_tools.ta_summary_text", return_value="RSI 60.5 (neutral) · signal bullish"),
    ):
        text = await _exec_ta("rsi of btc")
    assert text == "RSI 60.5 (neutral) · signal bullish"


async def test_exec_ta_inline_fallback_without_ta_domain() -> None:
    with (
        mock.patch("app.domains.chat_tools._extract_price_symbol", return_value="BTC"),
        mock.patch(
            "app.databus.ohlcv_engine.fetch_ohlcv",
            new=mock.AsyncMock(
                return_value={
                    "candles": _make_candles(),
                    "summary": {"current_price": 110.0, "price_change_pct": 10.0},
                }
            ),
        ),
        mock.patch("app.domains.chat_tools.ta_summary", None),
        mock.patch("app.domains.chat_tools.ta_summary_text", None),
    ):
        text = await _exec_ta("rsi of btc")
    assert text is not None
    assert "RSI" in text
    assert "110" in text


async def test_exec_market_formats_snapshot() -> None:
    ctx = {
        "btc_price": 67000.0,
        "btc_change_24h": 1.2,
        "eth_price": 3500.0,
        "eth_change_24h": -0.5,
        "sol_price": 150.0,
        "sol_change_24h": 3.1,
    }
    with (
        mock.patch("app.routers.chat._fetch_market_context", new=mock.AsyncMock(return_value=ctx)),
        mock.patch("app.domains.chat_tools._fetch_fear_greed", new=mock.AsyncMock(return_value="72/100 (Greed)")),
    ):
        text = await _exec_market()
    assert text is not None
    assert "BTC" in text
    assert "67,000" in text
    assert "ETH" in text
    assert "SOL" in text
    assert "Greed" in text


async def test_exec_market_returns_none_on_empty_context() -> None:
    with (
        mock.patch("app.routers.chat._fetch_market_context", new=mock.AsyncMock(return_value={})),
        mock.patch("app.domains.chat_tools._fetch_fear_greed", new=mock.AsyncMock(return_value=None)),
    ):
        assert await _exec_market() is None


# ── 4. Dispatcher ─────────────────────────────────────────────────────


async def test_run_tool_unknown_intent_returns_none() -> None:
    assert await _run_tool_for_intent("hello", "bogus") is None


async def test_run_tool_dispatches_to_executor() -> None:
    fake = mock.AsyncMock(return_value="BTC is $67,000")
    with mock.patch.dict(chat_tools._EXECUTORS, {"price": fake}):
        result = await _run_tool_for_intent("price of btc", "price")
    assert result == "BTC is $67,000"
    fake.assert_awaited_once_with("price of btc")


async def test_run_tool_fails_open_on_executor_error() -> None:
    fake = mock.AsyncMock(side_effect=RuntimeError("boom"))
    with mock.patch.dict(chat_tools._EXECUTORS, {"price": fake}):
        assert await _run_tool_for_intent("price of btc", "price") is None


async def test_run_tool_fails_open_on_empty_result() -> None:
    fake = mock.AsyncMock(return_value="   ")
    with mock.patch.dict(chat_tools._EXECUTORS, {"price": fake}):
        assert await _run_tool_for_intent("price of btc", "price") is None


# ── 5. Route integration (fake route path) ────────────────────────────


def test_inject_tool_result_appends_block_last() -> None:
    ctx = _inject_tool_result("RAG Context:\nsome hits", "BTC is $67,000")
    assert "TOOL RESULT (live data):" in ctx
    assert "BTC is $67,000" in ctx
    assert ctx.index("TOOL RESULT") > ctx.index("RAG Context")


def test_inject_tool_result_omits_block_when_none() -> None:
    base = "RAG Context:\nsome hits"
    assert _inject_tool_result(base, None) == base
    assert _inject_tool_result(base, "") == base
    assert _inject_tool_result(base, "   ") == base


def test_inject_tool_result_into_empty_context() -> None:
    ctx = _inject_tool_result("", "BTC is $67,000")
    assert ctx.startswith("TOOL RESULT (live data):")


async def test_route_integration_injects_tool_result_into_system_context() -> None:
    with (
        mock.patch("app.routers.chat._detect_tool_intent", return_value="price"),
        mock.patch("app.routers.chat._run_tool_for_intent", new=mock.AsyncMock(return_value="BTC is $67,000")),
    ):
        tool_result = await _fetch_tool_context("price of btc")
    assert tool_result == "BTC is $67,000"
    ctx = _inject_tool_result("RAG Context:\nsome hits", tool_result)
    assert "TOOL RESULT (live data):" in ctx
    assert "BTC is $67,000" in ctx


async def test_route_integration_omits_when_executor_returns_none() -> None:
    with (
        mock.patch("app.routers.chat._detect_tool_intent", return_value="price"),
        mock.patch("app.routers.chat._run_tool_for_intent", new=mock.AsyncMock(return_value=None)),
    ):
        tool_result = await _fetch_tool_context("price of btc")
    assert tool_result is None
    assert _inject_tool_result("RAG Context:\nsome hits", tool_result) == "RAG Context:\nsome hits"


async def test_route_integration_no_intent_skips_executor() -> None:
    with (
        mock.patch("app.routers.chat._detect_tool_intent", return_value=None),
        mock.patch("app.routers.chat._run_tool_for_intent", new=mock.AsyncMock()) as executor,
    ):
        assert await _fetch_tool_context("what is a honeypot") is None
    executor.assert_not_awaited()
