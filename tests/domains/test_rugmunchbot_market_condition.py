"""Tests for the RugMunchBot market_condition tool + verdict engine.

Owned by the tools/market_condition.py work: proves the deterministic
risk-on/risk-off/neutral verdict math (pure, no I/O), that the tool pulls
REAL data backend-first with CoinGecko/DefiLlama/alternative.me fallbacks,
that only REAL numbers ever appear (no fabricated signals), and that the
no-live-data contract (ok=False) holds when every source is unreachable.

Mock boundary: ``httpx.AsyncClient`` is faked at the ``_client`` module
boundary (same pattern as tests/domains/test_rugmunchbot_tools.py) — no live
network I/O, no live APIs.
"""

from __future__ import annotations

import time
from typing import Any, ClassVar

import pytest

from app.domains.telegram.rugmunchbot.tools import _client
from app.domains.telegram.rugmunchbot.tools.market_condition import assess_market_condition, market_condition

_VALID_VERDICTS = {"risk_on", "risk_off", "neutral"}


class _FakeAsyncClient:
    """httpx.AsyncClient class fake — records requests, routes canned bodies.

    Mirrors the class-fake style of ``tests/domains/test_rugmunchbot_tools.py``.
    ``_responses`` maps URL substrings to JSON bodies (dict OR list); an
    unmatched URL returns HTTP 404 (a dead source).
    """

    _responses: ClassVar[dict[str, Any]] = {}
    _status: ClassVar[int] = 200
    calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, Any] | None = None) -> Any:
        _FakeAsyncClient.calls.append({"method": "GET", "url": url, "params": params or {}, "headers": headers or {}})
        return self._respond(url)

    def _respond(self, url: str) -> Any:
        for prefix, body in _FakeAsyncClient._responses.items():
            if prefix in url:

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


@pytest.fixture(autouse=True)
def _fake_http_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the httpx boundary for every test: no live network."""
    _FakeAsyncClient._responses = {}
    _FakeAsyncClient._status = 200
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(_client.httpx, "AsyncClient", _FakeAsyncClient)


# ── Fixture payloads (real shapes, probed 2026-08-14) ──────────────────────


def _backend_prices_payload() -> dict[str, Any]:
    return {
        "prices": {
            "bitcoin": {"usd": 62775, "usd_market_cap": 1.2599e12, "usd_24h_change": 3.5},
            "ethereum": {"usd": 1872.9, "usd_market_cap": 2.2604e11, "usd_24h_change": 2.1},
            "solana": {"usd": 75.49, "usd_market_cap": 4.3995e10, "usd_24h_change": 1.2},
        },
        "timestamp": "2026-08-14T10:15:48Z",
        "source": "coingecko",
    }


def _defillama_chains_payload() -> list[dict[str, Any]]:
    return [
        {"name": "Ethereum", "tvl": 41029550104.6, "tokenSymbol": "ETH"},
        {"name": "BSC", "tvl": 4884533737.3, "tokenSymbol": "BNB"},
        {"name": "Solana", "tvl": 4831609875.7, "tokenSymbol": "SOL"},
        {"name": "Tron", "tvl": 4758919287.1, "tokenSymbol": "TRON"},
        {"name": "Base", "tvl": 4604330396.5, "tokenSymbol": None},
        {"name": "Arbitrum", "tvl": 1235115185.1, "tokenSymbol": "ARB"},
    ]


def _tvl_series(now: int, mid: float, latest: float) -> list[dict[str, Any]]:
    """Three real DefiLlama points: ~8d ago (old), ~4d ago (ref), now (latest).

    ``_tvl_change_7d`` picks the FIRST point at/after the 7d cutoff as the
    reference, so the change is ``(latest - mid) / mid * 100``.
    """
    return [
        {"date": now - 8 * 86400, "tvl": round(mid * 0.98, 2)},
        {"date": now - 4 * 86400, "tvl": mid},
        {"date": now, "tvl": latest},
    ]


def _happy_path_responses() -> None:
    """Every source healthy: backend prices + F&G + breadth, DefiLlama chains + history."""
    now = int(time.time())
    _FakeAsyncClient._responses = {
        "/api/v1/databus/market/prices": _backend_prices_payload(),
        "/api/v1/databus/market/fear-greed": {"value": 65, "classification": "Greed", "source": "alternative.me"},
        "/api/v1/market/trending": {
            "items": [
                {"symbol": "A", "chain": "solana", "change_24h": 5.0},
                {"symbol": "B", "chain": "solana", "change_24h": 3.0},
                {"symbol": "C", "chain": "solana", "change_24h": 1.0},
                {"symbol": "D", "chain": "solana", "change_24h": -2.0},
                {"symbol": "E", "chain": "solana", "change_24h": -4.0},
                {"symbol": "F", "chain": "solana", "change_24h": 0.0},
            ]
        },
        "api.llama.fi/v2/chains": _defillama_chains_payload(),
        "historicalChainTvl/ethereum": _tvl_series(now, 38_500_000_000.0, 40_900_000_000.0),
        "historicalChainTvl/bsc": _tvl_series(now, 4_900_000_000.0, 4_750_000_000.0),
        "historicalChainTvl/solana": _tvl_series(now, 4_700_000_000.0, 4_800_000_000.0),
    }


# ══════════════════════════════════════════════════════════════════════════
# Pure verdict engine — deterministic, no I/O
# ══════════════════════════════════════════════════════════════════════════


class TestAssessMarketCondition:
    def test_risk_on_signals_produce_risk_on_verdict(self) -> None:
        signals = {
            "fear_greed": {"value": 75, "classification": "Greed"},
            "btc": {"change_24h": 3.5},
            "eth": {"change_24h": 2.5},
            "global_market_cap_change_24h": 3.0,
            "breadth": {"pumping": 7, "dumping": 3, "pump_ratio": 0.7},
            "tvl_trend": {"median_change_7d": 6.0, "chains_measured": 3},
        }
        result = assess_market_condition(signals)
        assert result["verdict"] == "risk_on"
        assert result["confidence"] == "high"
        assert result["score"] >= 0.4

    def test_risk_off_signals_produce_risk_off_verdict(self) -> None:
        signals = {
            "fear_greed": {"value": 20, "classification": "Extreme Fear"},
            "btc": {"change_24h": -4.0},
            "eth": {"change_24h": -3.0},
            "global_market_cap_change_24h": -5.0,
            "breadth": {"pumping": 2, "dumping": 8, "pump_ratio": 0.2},
            "tvl_trend": {"median_change_7d": -8.0, "chains_measured": 3},
        }
        result = assess_market_condition(signals)
        assert result["verdict"] == "risk_off"
        assert result["score"] <= -0.4

    def test_mixed_signals_produce_neutral_verdict(self) -> None:
        signals = {
            "fear_greed": {"value": 48, "classification": "Neutral"},
            "btc": {"change_24h": 0.5},
            "eth": {"change_24h": -1.0},
            "breadth": {"pumping": 5, "dumping": 5, "pump_ratio": 0.5},
        }
        result = assess_market_condition(signals)
        assert result["verdict"] == "neutral"
        assert -0.4 < result["score"] < 0.4

    def test_single_signal_confidence_low(self) -> None:
        # A single NEUTRAL signal (fg 45 -> 0 vote) still reports low
        # confidence — one data point is never enough to call a market.
        result = assess_market_condition({"fear_greed": {"value": 45, "classification": "Neutral"}})
        assert result["confidence"] == "low"
        assert result["verdict"] == "neutral"

    def test_empty_signals_never_verdict_risk(self) -> None:
        result = assess_market_condition({})
        assert result == {"verdict": "neutral", "confidence": "low", "score": 0.0, "reasons": []}

    def test_fear_greed_thresholds(self) -> None:
        assert assess_market_condition({"fear_greed": {"value": 55, "classification": "Greed"}})["score"] == 1.0
        assert assess_market_condition({"fear_greed": {"value": 40, "classification": "Fear"}})["score"] == -1.0
        assert assess_market_condition({"fear_greed": {"value": 45, "classification": "Neutral"}})["score"] == 0.0

    def test_momentum_thresholds(self) -> None:
        assert assess_market_condition({"btc": {"change_24h": 2.0}})["score"] == 1.0
        assert assess_market_condition({"btc": {"change_24h": -2.0}})["score"] == -1.0
        assert assess_market_condition({"btc": {"change_24h": 1.9}})["score"] == 0.0

    def test_breadth_thresholds(self) -> None:
        assert assess_market_condition({"breadth": {"pump_ratio": 0.60}})["score"] == 1.0
        assert assess_market_condition({"breadth": {"pump_ratio": 0.40}})["score"] == -1.0
        assert assess_market_condition({"breadth": {"pump_ratio": 0.50}})["score"] == 0.0

    def test_tvl_trend_thresholds(self) -> None:
        assert assess_market_condition({"tvl_trend": {"median_change_7d": 5.0}})["score"] == 1.0
        assert assess_market_condition({"tvl_trend": {"median_change_7d": -5.0}})["score"] == -1.0
        assert assess_market_condition({"tvl_trend": {"median_change_7d": 4.9}})["score"] == 0.0

    def test_reasons_carry_only_real_numbers(self) -> None:
        result = assess_market_condition(
            {
                "fear_greed": {"value": 75, "classification": "Greed"},
                "btc": {"change_24h": 3.5},
                "breadth": {"pumping": 7, "dumping": 3, "pump_ratio": 0.7},
            }
        )
        joined = " | ".join(result["reasons"])
        assert "Fear & Greed 75" in joined
        assert "BTC 24h +3.50%" in joined
        assert "7 up / 3 down" in joined
        # No fabricated narrative words that would imply data we never saw.
        assert "ethereum" not in joined.lower()

    def test_deterministic_same_inputs_same_output(self) -> None:
        signals = {
            "fear_greed": {"value": 30, "classification": "Fear"},
            "btc": {"change_24h": -3.0},
            "eth": {"change_24h": -2.5},
            "breadth": {"pumping": 3, "dumping": 7, "pump_ratio": 0.3},
        }
        first = assess_market_condition(signals)
        second = assess_market_condition(signals)
        assert first == second


# ══════════════════════════════════════════════════════════════════════════
# The tool — backend-first, real numbers only
# ══════════════════════════════════════════════════════════════════════════


class TestMarketConditionTool:
    async def test_happy_path_uses_backend_and_defillama(self) -> None:
        _happy_path_responses()
        result = await market_condition(chain="global", history=[])  # shared ctx tolerated
        assert result.ok, result.error
        data = result.data
        assert data["verdict"] in _VALID_VERDICTS
        # fg 65(+1) btc 3.5(+1) eth 2.1(+1) breadth 0.5(0) tvl ~+2.1(0) -> score 0.6 risk_on
        assert data["verdict"] == "risk_on"
        assert data["score"] == 0.6
        assert data["confidence"] == "high"
        # Real symbols from the price feed — nothing invented.
        assert result.symbol_set == {"BTC", "ETH"}
        # Backend signals present with the real values.
        assert data["signals"]["btc"]["change_24h"] == 3.5
        assert data["signals"]["eth"]["change_24h"] == 2.1
        assert data["signals"]["fear_greed"]["value"] == 65
        assert data["signals"]["breadth"] == {
            "pumping": 3,
            "dumping": 2,
            "flat": 1,
            "total": 6,
            "pump_ratio": 0.5,
            "source": "backend:/api/v1/market/trending",
        }
        # DefiLlama TVL trend computed from the real historical series.
        assert data["signals"]["tvl_trend"]["chains_measured"] == 3
        assert data["signals"]["top_chains_tvl"][0]["name"] == "Ethereum"
        # Sources labelled — every number traceable.
        assert "backend:/api/v1/databus/market/prices" in data["sources"]
        assert any("defillama" in s for s in data["sources"])
        assert data["reasons"]

    async def test_happy_path_is_deterministic(self) -> None:
        _happy_path_responses()
        first = await market_condition()
        second = await market_condition()
        assert first.data["verdict"] == second.data["verdict"]
        assert first.data["score"] == second.data["score"]

    async def test_backend_down_falls_back_to_coingecko_and_alternative(self) -> None:
        # Only external sources available: CoinGecko /global + /coins/markets
        # (BTC/ETH momentum fallback) + alternative.me F&G direct.
        _FakeAsyncClient._responses = {
            "/global": {
                "data": {
                    "total_market_cap": {"usd": 2_500_000_000_000},
                    "market_cap_change_percentage_24h_usd": -0.95,
                    "market_cap_percentage": {"btc": 56.1, "eth": 10.1},
                }
            },
            "alternative.me/fng": {
                "data": [{"value": "29", "value_classification": "Fear", "timestamp": "1786665600"}]
            },
            "/coins/markets": [
                {"id": "bitcoin", "symbol": "btc", "current_price": 62818, "price_change_percentage_24h": -1.5},
                {"id": "ethereum", "symbol": "eth", "current_price": 1874.31, "price_change_percentage_24h": -0.8},
                {"id": "solana", "symbol": "sol", "current_price": 75.54, "price_change_percentage_24h": -0.9},
            ],
        }
        result = await market_condition()
        assert result.ok, result.error
        data = result.data
        assert data["verdict"] in _VALID_VERDICTS
        # Momentum came from the CoinGecko fallback, F&G from alternative.me.
        assert data["signals"]["btc"]["source"] == "coingecko:/coins/markets"
        assert data["signals"]["fear_greed"]["value"] == 29
        assert data["signals"]["fear_greed"]["classification"] == "Fear"
        assert data["signals"]["global_market_cap_change_24h"] == -0.95
        assert result.symbol_set == {"BTC", "ETH"}
        # Backend is probed FIRST (backend-first design) — its 404 is what
        # triggered the fallbacks.
        assert any("localhost" in c["url"] for c in _FakeAsyncClient.calls)

    async def test_all_sources_down_ok_false(self) -> None:
        result = await market_condition()
        assert not result.ok
        assert result.error
        assert result.data is None
        assert result.symbols == []

    async def test_breadth_counts_only_real_changes(self) -> None:
        # Two items have real change_24h; the third is unknown (not counted).
        _FakeAsyncClient._responses = {
            "/api/v1/market/trending": {
                "items": [
                    {"symbol": "A", "chain": "solana", "change_24h": 5.0},
                    {"symbol": "B", "chain": "solana"},  # unknown change — real-data-only
                    {"symbol": "C", "chain": "solana", "change_24h": -3.0},
                ]
            }
        }
        result = await market_condition()
        assert result.ok, result.error
        breadth = result.data["signals"]["breadth"]
        assert breadth["pumping"] == 1
        assert breadth["dumping"] == 1
        assert breadth["flat"] == 0
        assert breadth["total"] == 2
        # Only breadth signal present -> single 0 vote -> neutral, low confidence.
        assert result.data["verdict"] == "neutral"
        assert result.data["confidence"] == "low"

    async def test_http_goes_through_async_client(self) -> None:
        _happy_path_responses()
        await market_condition()
        assert _FakeAsyncClient.calls, "expected HTTP via AsyncClient"
        assert all(c["method"] == "GET" for c in _FakeAsyncClient.calls)
