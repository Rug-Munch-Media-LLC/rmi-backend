"""Tests for the RugCharts backend — app/routers/rugcharts.py + AI chart analysis.

Covers:
  - GET /api/v1/rugcharts/ohlcv/{chain}/{pair} — candle array from the
    ClickHouse path, plus the GeckoTerminal fallback when ClickHouse is empty
  - GET /api/v1/rugcharts/trending — DexScreener trending tokens
  - GET /api/v1/rugcharts/intel/{chain}/{pair} — token intelligence brief
  - POST /api/v1/ai/chart-analysis — AI TA via ``smart_route``

Everything external is mocked at the router module boundary: ClickHouse /
GeckoTerminal / DexScreener fetches (and their Redis cache reads), and the
LLM router for the AI endpoint. The pure DataFusion engine
(``app.core.data_fusion``) and the volume/rug scoring functions run for
real where they don't touch the network, so the response shape is
exercised end-to-end.
"""

from __future__ import annotations

import json
import os

os.environ["ENVIRONMENT"] = "dev"
os.environ["RMI_AUTH_TOKEN"] = "test-auth-token-32-chars-long!!"
os.environ["RMI_INTERNAL_KEY"] = "test-internal-key-32-chars-long!!!"
os.environ["JWT_SECRET"] = "a" * 32
os.environ["CERTSTREAM_ENABLED"] = "false"
os.environ["WEBHOOK_ENGINE_ENABLED"] = "false"

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# ── Fixture data helpers ───────────────────────────────────────────────


def _candles(n: int = 10, start_price: float = 1.0) -> list[dict]:
    """Deterministic OHLCV candle list — steady climb, constant volume.

    A monotonic climb guarantees RSI=100 → OVERBOUGHT and sma7 > sma20 →
    BULLISH, so the intel TA branch is deterministic under test.
    """
    out: list[dict] = []
    price = start_price
    for i in range(n):
        out.append(
            {
                "time": 1_700_000_000 + i * 3600,
                "open": round(price, 6),
                "high": round(price * 1.01, 6),
                "low": round(price * 0.99, 6),
                "close": round(price * 1.005, 6),
                "volume": 1000.0 + i,
            }
        )
        price = round(price * 1.005, 6)
    return out


def _pair_info(**overrides: float | str | int) -> dict:
    """Realistic GeckoTerminal pair-info shape (healthy pool, no flags)."""
    info: dict = {
        "name": "MockToken/WETH",
        "address": "0xabc",
        "base_token_price_usd": 1.005,
        "quote_token_price_usd": 1.0,
        "price_change_24h": 5.0,
        "volume_24h": 100_000.0,
        "volume_6h": 25_000.0,
        "volume_1h": 5_000.0,
        "liquidity_usd": 50_000.0,
        "fdv": 1_000_000.0,
        "market_cap": 900_000.0,
        "txns_24h_buys": 120,
        "txns_24h_sells": 80,
        "txns_6h_buys": 40,
        "txns_6h_sells": 30,
        "txns_1h_buys": 10,
        "txns_1h_sells": 8,
        "dex": "raydium",
        "pool_created_at": "2026-01-01T00:00:00Z",
    }
    info.update(overrides)
    return info


# ── App fixtures (mirrors test_chat_endpoints.py) ──────────────────────


@pytest.fixture(scope="module")
def app():
    """Module-scoped app. TestClient without context manager skips lifespan,
    so no Redis/ClickHouse startup happens."""
    from app.factory import create_app

    return create_app()


@pytest.fixture(autouse=True)
def _allow_global_rate_limits():
    """Open the unified rate limiter so the middleware never 429s tests."""
    from app.core.rate_limiter import UnifiedRateLimiter

    async def _allow(self, key, rate=1.0, burst=5.0, tokens=1.0):
        return (True, {"X-RateLimit-Limit": int(burst), "X-RateLimit-Remaining": 1, "X-RateLimit-Reset": 0})

    with patch.object(UnifiedRateLimiter, "acquire", _allow):
        yield


@pytest.fixture
def client(app):
    """TestClient WITHOUT context manager — no lifespan startup."""
    return TestClient(app)


class TestRugchartsOhlcv:
    def test_ohlcv_returns_candles(self, client: TestClient, monkeypatch) -> None:
        """GET /ohlcv/{chain}/{pair} → candle array from the ClickHouse path.

        With non-empty ClickHouse candles the GeckoTerminal fallback must
        not be called, and ``fallback_used`` must be False.
        """
        import app.routers.rugcharts as rc

        fake_candles = _candles(10)
        monkeypatch.setattr(rc, "_fetch_ohlcv_clickhouse", AsyncMock(return_value=fake_candles))
        gecko = AsyncMock(return_value=[])
        monkeypatch.setattr(rc, "_fetch_ohlcv_gecko", gecko)
        monkeypatch.setattr(rc, "_fetch_pair_info", AsyncMock(return_value=_pair_info()))

        resp = client.get("/api/v1/rugcharts/ohlcv/solana/0xabc?timeframe=1h&limit=10")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert len(data["candles"]) == 10
        first = data["candles"][0]
        assert {"time", "open", "high", "low", "close", "volume"} <= set(first)
        assert first["time"] == 1_700_000_000
        assert data["fallback_used"] is False
        assert data["timeframe"] == "1h"
        assert data["chain"] == "solana"
        assert data["summary"]["candle_count"] == 10
        # ClickHouse path only — GeckoTerminal must stay untouched.
        gecko.assert_not_awaited()

    def test_ohlcv_fallback_to_gecko(self, client: TestClient, monkeypatch) -> None:
        """Empty ClickHouse result → GeckoTerminal fallback is called and
        its candles are returned verbatim."""
        import app.routers.rugcharts as rc

        monkeypatch.setattr(rc, "_fetch_ohlcv_clickhouse", AsyncMock(return_value=[]))
        gecko = AsyncMock(return_value=_candles(5))
        monkeypatch.setattr(rc, "_fetch_ohlcv_gecko", gecko)
        monkeypatch.setattr(rc, "_fetch_pair_info", AsyncMock(return_value=_pair_info()))

        resp = client.get("/api/v1/rugcharts/ohlcv/ethereum/0xdef?timeframe=1h&limit=5")
        assert resp.status_code == 200, resp.text
        gecko.assert_awaited_once()
        data = resp.json()["data"]
        assert len(data["candles"]) == 5
        assert data["candles"][0]["time"] == 1_700_000_000


class TestRugchartsTrending:
    def test_trending_returns_tokens(self, client: TestClient, monkeypatch) -> None:
        """GET /trending → token list, count, chain, and fallback flag."""
        import app.routers.rugcharts as rc

        tokens = [
            {
                "address": "0xAAA",
                "name": "Token A",
                "symbol": "TA",
                "chain": "solana",
                "price_usd": 1.5,
                "volume_24h": 50_000,
                "source": "boosted",
            },
            {
                "address": "0xBBB",
                "name": "Token B",
                "symbol": "TB",
                "chain": "solana",
                "price_usd": 0.5,
                "volume_24h": 10_000,
                "source": "new_launch",
            },
        ]
        monkeypatch.setattr(rc, "_fetch_dexscreener_trending", AsyncMock(return_value=(tokens, False)))

        resp = client.get("/api/v1/rugcharts/trending?chain=solana&limit=10")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["chain"] == "solana"
        assert data["count"] == 2
        assert len(data["tokens"]) == 2
        assert data["tokens"][0]["symbol"] == "TA"
        assert data["tokens"][1]["address"] == "0xBBB"
        assert data["fallback_used"] is False


class TestRugchartsIntel:
    def test_intel_returns_analysis(self, client: TestClient, monkeypatch) -> None:
        """GET /intel/{chain}/{pair} → rug score, volume authenticity,
        real TA, a prediction, and an intel brief."""
        import app.routers.rugcharts as rc

        monkeypatch.setattr(rc, "_fetch_pair_info", AsyncMock(return_value=_pair_info()))
        monkeypatch.setattr(rc, "_fetch_ohlcv_gecko", AsyncMock(return_value=_candles(24)))
        monkeypatch.setattr(
            rc,
            "_compute_rug_score",
            lambda pair_info, vol_auth: {
                "score": 72,
                "level": "DANGER",
                "color": "#ef4444",
                "factors": ["Critically low liquidity (<$10K)"],
            },
        )

        resp = client.get("/api/v1/rugcharts/intel/base/0xabc")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["pair_address"] == "0xabc"
        assert data["chain"] == "base"
        # Mocked rug score flows through the envelope untouched.
        assert data["rug_score"]["score"] == 72
        assert data["rug_score"]["level"] == "DANGER"
        # Real volume-authenticity + TA computed from the mocked inputs.
        assert "authentic_score" in data["volume_authenticity"]
        assert data["technical_analysis"]["trend"] == "BULLISH"
        assert data["technical_analysis"]["rsi_signal"] == "OVERBOUGHT"
        # Monotonic climb + high rug score → DUMP_LIKELY prediction.
        signals = [p["signal"] for p in data["predictions"]]
        assert "DUMP_LIKELY" in signals
        assert data["intel_brief"]["one_liner"]


class TestAiChartAnalysis:
    def test_ai_chart_analysis_returns_trend(self, client: TestClient, monkeypatch) -> None:
        """POST /api/v1/ai/chart-analysis → AI trend from smart_route,
        with computed patterns/levels merged into the response."""
        import app.api.v1.ai.chart_analysis as chart_mod

        ai_json = json.dumps(
            {
                "trend": "BULLISH",
                "trend_strength": 80,
                "confidence": 75,
                "summary": "Strong momentum with higher highs.",
                "key_levels_additional": [{"price": 1.2, "label": "Resistance 3", "strength": "strong"}],
                "patterns_additional": ["Cup and Handle"],
                "risk_assessment": "MEDIUM",
                "trade_signal": "BUY",
                "detailed_analysis": "Price is above all EMAs with a bullish MACD crossover.",
            }
        )

        async def fake_smart_route(prompt: str, task_type: str = "fast", prefer: str = "fast", **kwargs):
            assert "=== TOKEN INFO ===" in prompt
            return {"response": ai_json, "routing": {"provider": "qwen3-coder"}, "provider": "qwen3-coder"}

        monkeypatch.setattr(chart_mod, "smart_route", fake_smart_route)

        candles = [
            {
                "time": 1_700_000_000 + i * 86_400,
                "open": 1.0,
                "high": 1.1,
                "low": 0.9,
                "close": 1.05,
                "volume": 1000.0,
            }
            for i in range(60)
        ]
        resp = client.post(
            "/api/v1/ai/chart-analysis",
            json={
                "chain": "solana",
                "pair": "0xabc",
                "symbol": "BONK",
                "name": "Bonk",
                "timeframe": "1d",
                "current_price": 1.05,
                "volume_24h": 100_000,
                "liquidity_usd": 50_000,
                "fdv": 1_000_000,
                "buys_24h": 120,
                "sells_24h": 80,
                "candles": candles,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        assert body["trend"] == "BULLISH"
        assert body["trend_strength"] == 80
        assert body["confidence"] == 75
        assert body["risk_assessment"] == "MEDIUM"
        assert body["trade_signal"] == "BUY"
        assert body["provider"] == "qwen3-coder"
        # AI-detected pattern merged with computed ones.
        assert "Cup and Handle" in body["patterns_detected"]
        # AI key level appended to computed S/R levels.
        assert any(lvl["label"] == "Resistance 3" for lvl in body["key_levels"])


class TestRugchartsWhales:
    def test_whales_returns_trades_with_side_counts(self, client: TestClient, monkeypatch) -> None:
        """GET /whales/{chain}/{token} → whale trades + buy/sell/unknown counts."""
        import app.routers.rugcharts as rc

        trades = [
            {
                "time": 1_700_000_000,
                "price_usd": 0.05,
                "volume_usd": 25_000.0,
                "side": "buy",
                "tx_hash": "0xaaa",
                "chain": "solana",
            },
            {
                "time": 1_700_003_600,
                "price_usd": 0.04,
                "volume_usd": 15_000.0,
                "side": "sell",
                "tx_hash": "0xbbb",
                "chain": "solana",
            },
            {
                "time": 1_700_007_200,
                "price_usd": 0.045,
                "volume_usd": 12_000.0,
                "side": "unknown",
                "tx_hash": "0xccc",
                "chain": "solana",
            },
        ]
        monkeypatch.setattr(rc, "_fetch_whale_trades_clickhouse", AsyncMock(return_value=trades))

        resp = client.get("/api/v1/rugcharts/whales/solana/0xTOKEN")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["count"] == 3
        assert data["buy_count"] == 1
        assert data["sell_count"] == 1
        assert data["unknown_count"] == 1
        assert data["total_whale_usd"] == 52_000.0
        assert data["whale_trades"][0]["side"] == "buy"
        assert data["whale_trades"][1]["side"] == "sell"
        assert body["source"] == "chain_indexer (ClickHouse dex_events)"

    def test_whales_passes_limit_and_min_usd(self, client: TestClient, monkeypatch) -> None:
        """Query params flow into the ClickHouse helper call."""
        import app.routers.rugcharts as rc

        mock = AsyncMock(return_value=[])
        monkeypatch.setattr(rc, "_fetch_whale_trades_clickhouse", mock)

        client.get("/api/v1/rugcharts/whales/ethereum/0xABC?limit=50&min_usd=50000")
        mock.assert_awaited_once_with("0xABC", "ethereum", 50, 50_000.0)

    def test_whales_empty_clickhouse_returns_empty_overlay(self, client: TestClient, monkeypatch) -> None:
        """No whale rows → empty lists and zero counts (soft overlay)."""
        import app.routers.rugcharts as rc

        monkeypatch.setattr(rc, "_fetch_whale_trades_clickhouse", AsyncMock(return_value=[]))

        resp = client.get("/api/v1/rugcharts/whales/base/0xEMPTY")
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["whale_trades"] == []
        assert data["buy_count"] == 0
        assert data["sell_count"] == 0
        assert data["total_whale_usd"] == 0.0


class TestWhaleSideDerivation:
    """Pure-function tests for the V2 in/out → buy/sell logic."""

    def test_token0_out_is_buy(self) -> None:
        from app.routers.rugcharts_helpers import _whale_side

        # token0 received FROM pool → trader bought token0
        assert _whale_side("0xT", "0xT", "0xWETH", "0", "0", "1000", "0") == "buy"

    def test_token0_in_with_token1_out_is_sell(self) -> None:
        from app.routers.rugcharts_helpers import _whale_side

        # token0 sent INTO pool + token1 received OUT → trader sold token0
        assert _whale_side("0xT", "0xT", "0xWETH", "1000", "0", "0", "1500") == "sell"

    def test_token1_out_is_buy(self) -> None:
        from app.routers.rugcharts_helpers import _whale_side

        assert _whale_side("0xT", "0xWETH", "0xT", "0", "0", "0", "2500") == "buy"

    def test_token1_in_with_token0_out_is_sell(self) -> None:
        from app.routers.rugcharts_helpers import _whale_side

        assert _whale_side("0xT", "0xWETH", "0xT", "0", "1500", "800", "0") == "sell"

    def test_case_insensitive_match(self) -> None:
        from app.routers.rugcharts_helpers import _whale_side

        assert _whale_side("0xt", "0xT", "0xWETH", "0", "0", "500", "0") == "buy"

    def test_no_match_is_unknown(self) -> None:
        from app.routers.rugcharts_helpers import _whale_side

        assert _whale_side("0xOTHER", "0xT", "0xWETH", "100", "0", "0", "0") == "unknown"

    def test_v3_absolute_amounts_are_unknown(self) -> None:
        from app.routers.rugcharts_helpers import _whale_side

        # V3 rows: only amount0/amount1 populated, all positive — no direction.
        assert _whale_side("0xT", "0xWETH", "0xT", "0", "0", "0", "0") == "unknown"
