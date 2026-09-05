"""RugCharts WIN-1/2/4/5 route-level tests.

Covers the audit fixes end-to-end at the API boundary:
- WIN-1 honesty: /ohlcv reports the ACTUAL serving source
- Holder ladder: own ClickHouse first, scanner fallback, honest None
- WIN-4: /ws/trades streams labeled trades; rejects missing token
- WIN-5: AI endpoint payload cap + per-client rate-limit rejection path
"""

from __future__ import annotations

import asyncio
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
from starlette.websockets import WebSocketDisconnect


def _candles(n: int = 5) -> list[dict]:
    return [
        {
            "time": 1_700_000_000 + i * 3600,
            "open": 1.0,
            "high": 1.1,
            "low": 0.9,
            "close": 1.05,
            "volume": 1000.0,
        }
        for i in range(n)
    ]


@pytest.fixture(scope="module")
def app():
    from app.factory import create_app

    return create_app()


@pytest.fixture(autouse=True)
def _allow_global_rate_limits():
    from app.core.rate_limiter import UnifiedRateLimiter

    async def _allow(self, key, rate=1.0, burst=5.0, tokens=1.0):
        return (True, {"X-RateLimit-Limit": int(burst), "X-RateLimit-Remaining": 1, "X-RateLimit-Reset": 0})

    with patch.object(UnifiedRateLimiter, "acquire", _allow):
        yield


@pytest.fixture
def client(app):
    return TestClient(app)


# ── WIN-1: honest source provenance ──────────────────────────────────────


class TestOhlcvSourceHonesty:
    def test_clickhouse_source_reported_when_ch_serves(self, client: TestClient, monkeypatch) -> None:
        from app.routers.rugcharts.routes import ohlcv as routes

        monkeypatch.setattr(routes, "_fetch_ohlcv_clickhouse", AsyncMock(return_value=_candles(4)))
        monkeypatch.setattr(routes, "_fetch_ohlcv_gecko", AsyncMock(return_value=[]))
        monkeypatch.setattr(routes, "_fetch_pair_info", AsyncMock(return_value={}))

        resp = client.get("/api/v1/rugcharts/ohlcv/base/0xabc?timeframe=1h")
        data = resp.json()["data"]
        assert data["source"] == "clickhouse"
        assert data["fallback_used"] is False

    def test_gecko_source_reported_on_fallback(self, client: TestClient, monkeypatch) -> None:
        from app.routers.rugcharts.routes import ohlcv as routes

        monkeypatch.setattr(routes, "_fetch_ohlcv_clickhouse", AsyncMock(return_value=[]))
        monkeypatch.setattr(routes, "_fetch_ohlcv_gecko", AsyncMock(return_value=_candles(3)))
        monkeypatch.setattr(routes, "_fetch_pair_info", AsyncMock(return_value={}))

        resp = client.get("/api/v1/rugcharts/ohlcv/eth/0xdef?timeframe=15m")
        data = resp.json()["data"]
        assert data["source"] == "geckoterminal"
        # The old bug: fallback_used was False whenever ANY candles existed.
        assert data["fallback_used"] is False  # candles exist → not a total failure
        assert data["candles"][0]["time"] == 1_700_000_000

    def test_none_source_when_everything_fails(self, client: TestClient, monkeypatch) -> None:
        from app.routers.rugcharts.routes import ohlcv as routes

        monkeypatch.setattr(routes, "_fetch_ohlcv_clickhouse", AsyncMock(return_value=[]))
        monkeypatch.setattr(routes, "_fetch_ohlcv_gecko", AsyncMock(return_value=[]))
        monkeypatch.setattr(routes, "_fetch_pair_info", AsyncMock(return_value={}))

        resp = client.get("/api/v1/rugcharts/ohlcv/base/0xdead?timeframe=1h")
        data = resp.json()["data"]
        assert data["source"] == "none"
        assert data["fallback_used"] is True
        assert data["candles"] == []


# ── Holder ladder: own indexer first ─────────────────────────────────────


class TestHolderLadder:
    def test_analyze_uses_clickhouse_tier_for_evm(self, client: TestClient, monkeypatch) -> None:
        import app.routers.rugcharts.helpers as helpers
        from app.routers.rugcharts.routes import trending as routes

        pair = {
            "name": "T/WETH",
            "symbol": "T",
            "base_token_price_usd": 1.0,
            "liquidity_usd": 50_000.0,
            "volume_24h": 10_000.0,
            "txns_24h_buys": 10,
            "txns_24h_sells": 5,
            "fdv": 100_000.0,
            "price_change_24h": 1.0,
            "pool_created_at": "",
        }
        monkeypatch.setattr(routes, "_fetch_pair_info", AsyncMock(return_value=pair))

        async def fake_ch(sql, params):
            if "ORDER BY balance DESC" in sql:
                return [("0xwhale", 60), ("0xa", 20), ("0xb", 20)]
            return [(100, 3)]  # sum(balance), count()

        monkeypatch.setattr(helpers, "_ch_query", fake_ch)

        resp = client.get("/api/v1/rugcharts/analyze/0xTOKEN?chain=base")
        body = resp.json()
        assert body["top_holder_pct"] == pytest.approx(60.0)
        assert body["top10_holder_pct"] == pytest.approx(100.0)
        assert body["holder_data_source"] == "chain_indexer.clickhouse"

    def test_analyze_null_holders_when_all_tiers_fail(self, client: TestClient, monkeypatch) -> None:
        import app.routers.rugcharts.helpers as helpers
        from app.routers.rugcharts.routes import trending as routes

        monkeypatch.setattr(routes, "_fetch_pair_info", AsyncMock(return_value={}))

        async def fail(*a, **k):
            return None

        monkeypatch.setattr(helpers, "_holder_stats_from_clickhouse", fail)
        monkeypatch.setattr(helpers, "_holder_stats_from_scanner_api", fail)

        resp = client.get("/api/v1/rugcharts/analyze/0xGHOST?chain=base")
        body = resp.json()
        assert body["top_holder_pct"] is None  # unknown — never fabricated zero
        assert body["holder_data_source"] is None

    def test_solana_ladder_order(self, monkeypatch) -> None:
        """Solana tries SPL index → scanner → Helius, in that order."""
        from app.routers.rugcharts import helpers

        calls: list[str] = []

        async def spl(_t):
            calls.append("spl")
            return None

        async def scanner(chain, token):
            calls.append("scanner")
            return {
                "source": "scanner_service.x",
                "top_holder_pct": 1.0,
                "top10_holder_pct": 5.0,
                "sampled_accounts": 0,
                "metric": "m",
            }

        async def helius(_t):
            calls.append("helius")
            return {"source": "helius"}

        monkeypatch.setattr(helpers, "_holder_stats_from_solana_transfers", spl)
        monkeypatch.setattr(helpers, "_holder_stats_from_scanner_api", scanner)
        monkeypatch.setattr(helpers, "_holder_stats_from_helius", helius)

        stats = asyncio.run(helpers._fetch_holder_stats("MINT", "solana"))
        assert calls == ["spl", "scanner"]  # helius never reached
        assert stats is not None
        assert stats["source"].startswith("scanner_service")


# ── WIN-4: live trade tape WebSocket ─────────────────────────────────────


class TestWsTrades:
    def test_ws_streams_labeled_trades(self, client: TestClient, monkeypatch) -> None:
        import app.routers.rugcharts.ws as ws_mod

        trades = [
            {
                "time": 1700000000,
                "price_usd": 0.01,
                "volume_usd": 20_000.0,
                "side": "buy",
                "tx_hash": "0xw1",
                "wallet": "w1",
                "chain": "base",
            },
            {
                "time": 1700000060,
                "price_usd": 0.01,
                "volume_usd": 50.0,
                "side": "sell",
                "tx_hash": "0xs1",
                "wallet": "s1",
                "chain": "base",
            },
        ]
        monkeypatch.setattr(ws_mod, "_fetch_recent_trades_clickhouse", AsyncMock(return_value=trades))

        with client.websocket_connect("/ws/trades?chain=base&token=0xABC") as ws:
            hello = json.loads(ws.receive_text())
            assert hello["type"] == "hello"
            frame = json.loads(ws.receive_text())
            assert frame["type"] == "trades"
            labels = {t["tx_hash"]: t["wallet_label"] for t in frame["trades"]}
            assert labels["0xw1"] == "whale"

    def test_ws_rejects_missing_token(self, client: TestClient) -> None:
        # Server closes (4400) before accept → handshake fails.
        with (
            pytest.raises((WebSocketDisconnect, RuntimeError), match=r".*") as _exc,
            client.websocket_connect("/ws/trades?chain=base"),
        ):
            pass

    def test_ws_stats_endpoint(self, client: TestClient) -> None:
        resp = client.get("/ws/trades/stats")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ── WIN-5: AI endpoint guards ────────────────────────────────────────────


class TestAiEndpointGuards:
    def test_payload_cap_rejects_oversized_candle_array(self, client: TestClient) -> None:
        candles = [{"time": i, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0} for i in range(1501)]
        resp = client.post(
            "/api/v1/ai/chart-analysis",
            json={"chain": "solana", "candles": candles},
        )
        assert resp.status_code == 422  # pydantic max_length guard

    def test_rate_limit_rejection_returns_429(self, client: TestClient) -> None:
        from app.api.v1.ai import chart_analysis as chart_mod

        async def deny(key, rate=1.0, burst=5.0, tokens=1.0):  # instance attr → no self
            return (False, {"X-RateLimit-Limit": "5", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1"})

        with patch.object(chart_mod._llm_limiter, "acquire", deny):
            resp = client.post(
                "/api/v1/ai/chart-analysis",
                json={"chain": "solana", "candles": []},
            )
        assert resp.status_code == 429
