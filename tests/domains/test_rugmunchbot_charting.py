"""Unit tests for the chart tool (``tools/charting.py``).

Covers:
  * the ASCII renderers (sparkline, candle-direction row, price ladder) on
    synthetic closes — pure functions, no network;
  * the timeframe slicing (24 hourly candles / 7 daily candles);
  * the tool's source cascade at the mocked httpx boundary: backend RugCharts
    candles -> GeckoTerminal direct -> honest DexScreener snapshot when no
    candles exist (the chart is never fabricated);
  * no-pair -> ok=False.

No network I/O: ``httpx.AsyncClient`` is faked at the ``_client`` boundary
(the same pattern as ``tests/domains/test_rugmunchbot_ta_tool.py``).
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from app.domains.telegram.rugmunchbot.tools import _client, registry
from app.domains.telegram.rugmunchbot.tools.charting import (
    _candle_direction_row,
    _price_ladder,
    _slice_candles,
    _sparkline,
    chart,
)

EVM = "0x6982508145454Ce325dDbE47a25d4ec3d2311933"


# ── Synthetic OHLCV helpers (mock data, no network) ────────────────────────


def _candle(i: int, close: float, volume: float = 1000.0, up: bool = True) -> dict[str, Any]:
    o = close * 0.99 if up else close * 1.01
    return {
        "time": i,
        "open": o,
        "high": max(o, close) * 1.02,
        "low": min(o, close) * 0.98,
        "close": close,
        "volume": volume,
    }


def _uptrend(n: int = 30, start: float = 1.0, factor: float = 1.01) -> list[dict[str, Any]]:
    return [_candle(i, start * (factor**i), volume=1000 + i * 100) for i in range(n)]


def _mixed(n: int = 30, start: float = 1.0) -> list[dict[str, Any]]:
    return [_candle(i, start * (1.0 + 0.02 * (1 if i % 2 == 0 else -1)), up=i % 2 == 0) for i in range(n)]


def _backend_body(candles: list[dict[str, Any]]) -> dict[str, Any]:
    """The envelope the backend /api/v1/rugcharts/ohlcv/{chain}/{pair} returns."""
    return {
        "success": True,
        "data": {
            "candles": candles,
            "summary": {"current_price": candles[-1]["close"] if candles else 0, "candle_count": len(candles)},
            "pair_info": {},
            "authenticity": {},
            "fusion": {},
            "timeframe": "1h",
            "chain": "ethereum",
            "fallback_used": not bool(candles),
        },
    }


def _gecko_body(candles: list[dict[str, Any]]) -> dict[str, Any]:
    """GeckoTerminal ohlcv payload — ohlcv_list is NEWEST-first (reversed)."""
    rows = [[c["time"], c["open"], c["high"], c["low"], c["close"], c["volume"]] for c in reversed(candles)]
    return {"data": {"id": "1", "type": "ohlcv", "attributes": {"ohlcv_list": rows}}}


def _pair(symbol: str = "TST", volume: float = 100_000.0) -> dict[str, Any]:
    return {
        "pairAddress": f"0xPAIR_{symbol}",
        "chainId": "ethereum",
        "dexId": "uniswap-v2",
        "baseToken": {"address": EVM, "symbol": symbol, "name": "TestCoin"},
        "priceUsd": "1.23",
        "priceChange": {"h1": 0.5, "h6": 1.0, "h24": 5.2},
        "volume": {"h24": volume},
        "liquidity": {"usd": 50_000.0},
        "url": "https://dexscreener.com/ethereum/0xPAIR",
    }


# ── Fake httpx boundary (mirrors test_rugmunchbot_tools) ──────────────────


class _FakeAsyncClient:
    """httpx.AsyncClient fake — URL-substring -> canned JSON body."""

    _responses: ClassVar[dict[str, Any]] = {}
    _status: ClassVar[int] = 200
    _raise: ClassVar[Exception | None] = None
    calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, Any] | None = None) -> Any:
        _FakeAsyncClient.calls.append({"method": "GET", "url": url, "params": params or {}, "headers": headers or {}})
        for prefix, body in _FakeAsyncClient._responses.items():
            if prefix in url:
                if _FakeAsyncClient._raise is not None:
                    raise _FakeAsyncClient._raise

                class _Resp:
                    status_code = _FakeAsyncClient._status

                    def json(self, _body: Any = body) -> Any:
                        return _body

                return _Resp()

        class _Resp404:
            status_code = 404

            def json(self) -> Any:
                return {"error": "not found"}

        return _Resp404()

    async def post(self, url: str, json: dict[str, Any] | None = None, headers: dict[str, Any] | None = None) -> Any:
        raise AssertionError(f"unexpected POST {url}")


@pytest.fixture(autouse=True)
def _fake_http_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    """No live network; isolated registry TTL cache."""
    from app.domains.telegram.rugmunchbot.tools.budget import budget as budget_tracker

    _FakeAsyncClient._responses = {}
    _FakeAsyncClient._status = 200
    _FakeAsyncClient._raise = None
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(_client.httpx, "AsyncClient", _FakeAsyncClient)
    registry.clear_cache()
    budget_tracker.reset()
    yield
    registry.clear_cache()
    budget_tracker.reset()


# ── Pure renderers ─────────────────────────────────────────────────────────


class TestRenderers:
    def test_sparkline_uptrend_uses_ladder_chars(self) -> None:
        closes = [float(c["close"]) for c in _uptrend(24, start=1.0)]
        spark = _sparkline(closes)
        assert spark, "expected a non-empty sparkline"
        assert len(spark) == 24
        # Monotonic rise -> the ladder must climb (last char > first char
        # when they differ).
        assert all(ch in "_.,-=*#" for ch in spark)

    def test_sparkline_flat_series(self) -> None:
        # Flat series -> the middle of the ASCII ladder ("-" at index 3 of
        # "_.,-=*#") for every sampled point.
        spark = _sparkline([1.0, 1.0, 1.0, 1.0])
        assert spark == "----"

    def test_sparkline_respects_width(self) -> None:
        closes = [float(c["close"]) for c in _uptrend(60)]
        assert len(_sparkline(closes, width=20)) == 20

    def test_candle_direction_row(self) -> None:
        candles = _mixed(6, start=1.0)
        row = _candle_direction_row(candles)
        assert row == "+-+-+-"  # alternates up/down
        assert set(row) <= {"+", "-", "=", "."}

    def test_price_ladder_rows_and_labels(self) -> None:
        closes = [1.0, 1.1, 1.2, 1.05, 0.95]
        ladder = _price_ladder(closes, width=10, height=4)
        lines = ladder.split("\n")
        assert len(lines) == 4
        assert "1.2" in lines[0]  # top label = high
        assert "0.95" in lines[-1]  # bottom label = low
        assert all("*" in line or "." in line for line in lines)

    def test_slice_candles_by_timeframe(self) -> None:
        candles = _uptrend(40)
        assert len(_slice_candles(candles, "24h")) == 24
        assert len(_slice_candles(candles, "7d")) == 7
        assert _slice_candles([], "24h") == []


# ── chart tool: cascade + honest degradation ──────────────────────────────


class TestChartTool:
    async def test_chart_with_backend_candles(self) -> None:
        candles = _uptrend(30)
        _FakeAsyncClient._responses = {
            "api.dexscreener.com/latest/dex/tokens": {"pairs": [_pair("TST")]},
            "/api/v1/rugcharts/ohlcv/ethereum/": _backend_body(candles),
        }
        result = await chart(address=EVM)
        assert result.ok
        data = result.data
        assert data["symbol"] == "TST"
        assert data["timeframe"] == "24h"
        assert data["candles_shown"] == 24  # sliced to the 24h window
        chart_text = data["chart_text"]
        assert "$TST" in chart_text
        assert "24h chart" in chart_text
        assert data["sparkline"]  # ASCII sparkline present
        assert data["candle_directions"]  # direction row present
        assert "RSI-14" in chart_text  # TA digest present
        assert "last " in chart_text  # levels line present
        assert result.symbol_set == {"TST"}
        assert result.address_set == {EVM}
        assert "backend:/api/v1/rugcharts/ohlcv" in (result.source or "")

    async def test_chart_7d_timeframe_uses_daily_candles(self) -> None:
        candles = _uptrend(30)
        _FakeAsyncClient._responses = {
            "api.dexscreener.com/latest/dex/tokens": {"pairs": [_pair("TST")]},
            "/api/v1/rugcharts/ohlcv/ethereum/": _backend_body(candles),
        }
        result = await chart(address=EVM, timeframe="7d")
        assert result.ok
        data = result.data
        assert data["timeframe"] == "7d"
        assert data["candle_unit"] == "1d"
        assert data["candles_shown"] == 7
        assert "7d chart" in data["chart_text"]

    async def test_chart_gecko_fallback(self) -> None:
        candles = _uptrend(30)
        # Backend route returns nothing -> GeckoTerminal direct serves candles.
        _FakeAsyncClient._responses = {
            "api.dexscreener.com/latest/dex/tokens": {"pairs": [_pair("TST")]},
            "api.geckoterminal.com/api/v2/networks/eth/pools/0xPAIR_TST/ohlcv/hour": _gecko_body(candles),
        }
        result = await chart(address=EVM)
        assert result.ok
        assert result.data["candles_shown"] == 24
        assert "geckoterminal:direct" in (result.source or "")

    async def test_chart_no_candles_returns_honest_snapshot(self) -> None:
        # DexScreener pair resolves but no candle source returns data.
        _FakeAsyncClient._responses = {
            "api.dexscreener.com/latest/dex/tokens": {"pairs": [_pair("TST")]},
        }
        result = await chart(address=EVM)
        assert result.ok  # usable answer, not a hard failure
        data = result.data
        assert data["indicators_available"] is False
        assert "No OHLCV candle history" in data["chart_text"]
        assert data["snapshot"]["volume_24h"] == 100_000.0  # REAL snapshot numbers
        assert result.symbol_set == {"TST"}

    async def test_chart_no_pair_ok_false(self) -> None:
        result = await chart(address=EVM)  # no DexScreener response -> 404
        assert not result.ok
        assert "could not resolve" in (result.error or "")

    async def test_chart_rate_limited_ok_false(self) -> None:
        _FakeAsyncClient._responses = {"api.dexscreener.com/latest/dex/tokens": {}}
        _FakeAsyncClient._status = 429
        result = await chart(address=EVM)
        assert not result.ok

    async def test_chart_invalid_timeframe_defaults_to_24h(self) -> None:
        candles = _uptrend(30)
        _FakeAsyncClient._responses = {
            "api.dexscreener.com/latest/dex/tokens": {"pairs": [_pair("TST")]},
            "/api/v1/rugcharts/ohlcv/ethereum/": _backend_body(candles),
        }
        result = await chart(address=EVM, timeframe="bogus")
        assert result.ok
        assert result.data["timeframe"] == "24h"
