"""Unit tests for the technical_analysis tool (``tools/ta.py``).

Covers:
  * the reused indicator math (RSI-14 / EMA-9/21 / MACD) on synthetic OHLCV,
  * the pure verdict + summary functions over mocked analysis digests,
  * the tool's source cascade at the mocked httpx boundary:
    backend RugCharts candles -> GeckoTerminal direct -> honest DexScreener
    snapshot when no candles exist (indicators are never fabricated).

No network I/O: ``httpx.AsyncClient`` is faked at the ``_client`` boundary
(the same pattern as ``tests/domains/test_rugmunchbot_tools.py``).
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from app.domains.telegram.rugmunchbot import tools
from app.domains.telegram.rugmunchbot.ta_indicators import ema, macd, rsi
from app.domains.telegram.rugmunchbot.tools import _client, registry
from app.domains.telegram.rugmunchbot.tools.ta import _summary_text, _verdict, technical_analysis

EVM = "0x6982508145454Ce325dDbE47a25d4ec3d2311933"


# ── Synthetic OHLCV helpers (mock data, no network) ────────────────────────


def _candle(i: int, close: float, volume: float = 1000.0) -> dict[str, Any]:
    return {
        "time": i,
        "open": close * 0.99,
        "high": close * 1.02,
        "low": close * 0.98,
        "close": close,
        "volume": volume,
    }


def _uptrend(n: int = 60, start: float = 1.0, factor: float = 1.01) -> list[dict[str, Any]]:
    return [_candle(i, start * (factor**i), volume=1000 + i * 100) for i in range(n)]


def _downtrend(n: int = 60, start: float = 1.0, factor: float = 0.99) -> list[dict[str, Any]]:
    return [_candle(i, start * (factor**i), volume=1000) for i in range(n)]


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
        "marketCap": 1_250_000.0,
        "fdv": 2_500_000.0,
        "url": "https://dexscreener.com/ethereum/0xPAIR",
    }


# ── Fake httpx boundary (mirrors test_rugmunchbot_tools._FakeAsyncClient) ──


class _FakeAsyncClient:
    """httpx.AsyncClient fake — URL-substring -> canned JSON body."""

    _responses: ClassVar[dict[str, Any]] = {}
    _status: ClassVar[int] = 200
    _raise: ClassVar[Exception | None] = None

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, Any] | None = None) -> Any:
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
    _FakeAsyncClient._responses = {}
    _FakeAsyncClient._status = 200
    _FakeAsyncClient._raise = None
    monkeypatch.setattr(_client.httpx, "AsyncClient", _FakeAsyncClient)
    registry.clear_cache()
    yield
    registry.clear_cache()


# ── Reused indicator math on mock OHLCV (pure, no network) ─────────────────


class TestReusedIndicatorMath:
    def test_rsi_ema_macd_on_uptrend(self) -> None:
        # Accelerating rise: monotonic (RSI=100), EMA9 > EMA21, MACD hist > 0.
        closes = [1.0 + 0.01 * i + 0.001 * i * i for i in range(60)]
        assert rsi(closes, 14) == 100.0
        e9_series = ema(closes, 9)
        e21_series = ema(closes, 21)
        e9 = e9_series[-1]
        e21 = e21_series[-1]
        assert e9 is not None
        assert e21 is not None
        assert e9 > e21
        m = macd(closes)
        assert m["histogram"][-1] is not None
        assert m["histogram"][-1] > 0

    def test_rsi_on_downtrend(self) -> None:
        closes = [float(100 - i) for i in range(20)]
        assert rsi(closes, 14) == 0.0

    def test_short_series_none(self) -> None:
        assert rsi([1.0, 2.0, 3.0], 14) is None
        assert ema([1.0, 2.0], 9) == [None, None]


# ── Pure verdict + summary functions ────────────────────────────────────────


def _analysis(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "candles": 60,
        "price": 1.2,
        "change_pct": 5.0,
        "high": 1.5,
        "low": 0.9,
        "trend": "NEUTRAL",
        "ema9": 1.1,
        "ema21": 1.1,
        "ema_cross": "NEUTRAL",
        "rsi": 50.0,
        "rsi_label": "Neutral",
        "macd_line": 0.0,
        "macd_signal": 0.0,
        "macd_hist": 0.0,
        "macd_label": "Flat",
        "volume_ratio": 1.0,
        "volume_trend": "Steady",
        "support": 0.95,
        "resistance": 1.45,
    }
    base.update(overrides)
    return base


class TestVerdict:
    def test_bullish(self) -> None:
        v = _verdict(
            _analysis(
                trend="BULLISH",
                ema_cross="BULLISH CROSS",
                macd_label="Bullish",
                rsi=62.0,
                rsi_label="Neutral",
            )
        )
        assert v["bias"] == "bullish"
        assert v["score"] >= 2
        assert v["one_liner"].startswith("BULLISH")

    def test_bearish(self) -> None:
        v = _verdict(
            _analysis(
                trend="BEARISH",
                ema_cross="BEARISH CROSS",
                macd_label="Bearish",
                rsi=38.0,
                rsi_label="Neutral",
            )
        )
        assert v["bias"] == "bearish"
        assert v["score"] <= -2
        assert v["one_liner"].startswith("BEARISH")

    def test_neutral_when_conflicting(self) -> None:
        v = _verdict(_analysis(trend="BULLISH", ema_cross="NEUTRAL", macd_label="Bearish", rsi=50.0))
        assert v["bias"] == "neutral"

    def test_overbought_reported_as_risk_not_tilt(self) -> None:
        v = _verdict(_analysis(trend="BULLISH", ema_cross="BULLISH CROSS", macd_label="Bullish", rsi=88.0))
        assert v["bias"] == "bullish"
        assert any("overbought" in r for r in v["reasons"])

    def test_missing_indicators_neutral(self) -> None:
        v = _verdict(_analysis(trend="NEUTRAL", ema_cross="NEUTRAL", macd_label="N/A", rsi=None, rsi_label="N/A"))
        assert v["bias"] == "neutral"


class TestSummaryText:
    def test_contains_key_metrics(self) -> None:
        text = _summary_text(_analysis())
        assert "RSI-14" in text
        assert "EMA9" in text
        assert "EMA21" in text
        assert "MACD" in text
        assert "S/R" in text
        assert "trend" in text

    def test_none_safe(self) -> None:
        text = _summary_text(
            _analysis(ema9=None, ema21=None, rsi=None, rsi_label="N/A", macd_label="N/A", support=None, resistance=None)
        )
        assert "RSI-14 n/a" in text
        assert "EMA n/a" in text
        assert "S/R n/a" in text


# ── Tool cascade with the fake HTTP boundary ────────────────────────────────


class TestTechnicalAnalysisTool:
    async def test_uptrend_bullish_from_backend(self) -> None:
        _FakeAsyncClient._responses = {
            "api.dexscreener.com": {"pairs": [_pair()]},
            "/api/v1/rugcharts/ohlcv/": _backend_body(_uptrend()),
        }
        result = await technical_analysis(address=EVM, chain="ethereum")
        assert result.ok
        d = result.data
        assert d["indicators_available"] is True
        ind = d["indicators"]
        assert ind["rsi_14"] == 100.0
        assert ind["trend"] == "BULLISH"
        assert ind["ema_9"] is not None
        assert ind["ema_21"] is not None
        assert ind["ema_9"] > ind["ema_21"]
        assert ind["macd_label"] == "Bullish"
        assert d["verdict"]["bias"] == "bullish"
        assert "BULLISH" in d["verdict"]["one_liner"]
        assert "RSI-14" in d["summary_text"]
        assert result.source.startswith("backend:")
        # Richer market context is surfaced for the chat LLM.
        m = d["market"]
        assert m["price_usd"] == 1.23
        assert m["change_24h"] == 5.2
        assert m["change_1h"] == 0.5
        assert m["volume_24h"] == 100_000.0
        assert m["liquidity_usd"] == 50_000.0
        assert m["market_cap"] == 1_250_000.0
        assert m["fdv"] == 2_500_000.0
        assert m["change_window_pct"] is not None
        assert m["window_high"] > m["price_usd"]  # candle high above live price
        assert m["window_low"] < m["price_usd"]
        assert m["distance_from_window_high_pct"] > 0
        assert "ath" not in {k.lower() for k in m}  # never fabricate an ATH

    async def test_downtrend_bearish_from_backend(self) -> None:
        _FakeAsyncClient._responses = {
            "api.dexscreener.com": {"pairs": [_pair()]},
            "/api/v1/rugcharts/ohlcv/": _backend_body(_downtrend()),
        }
        result = await technical_analysis(address=EVM, chain="ethereum")
        assert result.ok
        d = result.data
        assert d["indicators"]["rsi_14"] == 0.0
        assert d["indicators"]["trend"] == "BEARISH"
        assert d["verdict"]["bias"] == "bearish"

    async def test_geckoterminal_fallback_when_backend_empty(self) -> None:
        _FakeAsyncClient._responses = {
            "api.dexscreener.com": {"pairs": [_pair()]},
            "/api/v1/rugcharts/ohlcv/": _backend_body([]),
            "api.geckoterminal.com": _gecko_body(_uptrend()),
        }
        result = await technical_analysis(address=EVM, chain="ethereum")
        assert result.ok
        assert result.data["indicators_available"] is True
        assert result.data["indicators"]["trend"] == "BULLISH"
        assert result.source.startswith("geckoterminal:direct")

    async def test_no_candles_snapshot_only_no_fabrication(self) -> None:
        _FakeAsyncClient._responses = {
            "api.dexscreener.com": {"pairs": [_pair()]},
            # backend + GeckoTerminal both 404 -> no candle source
        }
        result = await technical_analysis(address=EVM, chain="ethereum")
        assert result.ok
        d = result.data
        assert d["indicators_available"] is False
        assert d["indicators"] is None
        assert d["snapshot"]["price_usd"] == 1.23
        assert d["snapshot"]["change_24h"] == 5.2
        assert d["snapshot"]["market_cap"] == 1_250_000.0
        assert d["snapshot"]["fdv"] == 2_500_000.0
        assert d["market"]["market_cap"] == 1_250_000.0
        assert "change_window_pct" not in d["market"]  # no candles → no window data
        assert d["verdict"]["bias"] == "insufficient_data"
        assert "rsi" not in d  # never fabricate indicators
        assert result.symbols == ["TST"]

    async def test_short_flat_series_neutral_no_rsi(self) -> None:
        _FakeAsyncClient._responses = {
            "api.dexscreener.com": {"pairs": [_pair()]},
            "/api/v1/rugcharts/ohlcv/": _backend_body([_candle(i, 1.0) for i in range(5)]),
        }
        result = await technical_analysis(address=EVM, chain="ethereum")
        assert result.ok
        d = result.data
        assert d["indicators_available"] is True
        assert d["indicators"]["rsi_14"] is None
        assert d["indicators"]["trend"] == "NEUTRAL"
        assert d["verdict"]["bias"] == "neutral"

    async def test_symbol_resolution_prefers_exact_symbol(self) -> None:
        _FakeAsyncClient._responses = {
            "api.dexscreener.com": {
                "pairs": [_pair(symbol="TST", volume=1_000.0), _pair(symbol="WIF", volume=1_000_000.0)]
            },
            "/api/v1/rugcharts/ohlcv/": _backend_body(_uptrend()),
        }
        result = await technical_analysis(symbol="TST")
        assert result.ok
        assert result.data["symbol"] == "TST"

    async def test_unresolvable_pair_ok_false(self) -> None:
        _FakeAsyncClient._responses = {}  # DexScreener 404
        result = await technical_analysis(address=EVM, chain="ethereum")
        assert result.ok is False
        assert "could not resolve" in (result.error or "")

    def test_registered_in_shared_registry(self) -> None:
        assert "technical_analysis" in registry.names
        assert registry._ttls.get("technical_analysis") == 120.0


# ── tools/__init__ imports the module (registration is additive) ───────────


class TestAdditiveRegistration:
    def test_tools_package_exports_registry_with_ta(self) -> None:
        assert tools.registry is registry
        assert "technical_analysis" in tools.registry.names
