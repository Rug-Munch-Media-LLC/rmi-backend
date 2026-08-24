"""RugCharts WIN-10/11 tests: aggregate bot-volume breakdown + WS stats frames.

Oracles are hand-computed:
- 600/1000 total volume labeled bot → bot_volume_pct == 60.0 exactly
- buy 700 / sell 300 → buy_pressure == 0.7 exactly
- suspicious = bot+bundle+sniper only; whale counts as organic.
"""

from __future__ import annotations

import json
import os

os.environ["ENVIRONMENT"] = "dev"
os.environ["RMI_AUTH_TOKEN"] = "test-auth-token-32-chars-long!!"
os.environ["RMI_INTERNAL_KEY"] = "test-internal-key-32-chars-long!!!"
os.environ["JWT_SECRET"] = "a" * 32

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


def _trade(time: int, vol: float, side: str = "buy", wallet: str = "w") -> dict:
    return {
        "time": time,
        "price_usd": 0.01,
        "volume_usd": vol,
        "side": side,
        "tx_hash": f"raw-{time}-{vol}-{wallet}",
        "wallet": wallet,
        "chain": "ethereum",
    }


def _labeled(label: str, vol: float, side: str = "buy", time: int = 1000) -> dict:
    return {
        "time": time,
        "price_usd": 0.01,
        "volume_usd": vol,
        "side": side,
        "tx_hash": f"tx-{label}-{vol}-{side}-{time}",
        "wallet": f"w-{label}",
        "wallet_label": label,
        "label_confidence": 75,
        "chain": "ethereum",
    }


class TestVolumeBreakdown:
    def test_golden_percentages(self) -> None:
        from app.routers.rugcharts.classifier import volume_breakdown

        trades = [
            _labeled("bot", 600.0),  # bot share
            _labeled("whale", 250.0),  # organic
            _labeled("swap", 150.0, "sell"),  # organic
        ]
        out = volume_breakdown(trades)
        assert out["bot_volume_pct"] == pytest.approx(60.0)
        assert out["suspicious_volume_pct"] == pytest.approx(60.0)
        assert out["organic_volume_pct"] == pytest.approx(40.0)
        assert out["total_volume_usd"] == pytest.approx(1000.0)
        # buy 600+250=850, sell 150 → 0.85
        assert out["buy_pressure"] == pytest.approx(0.85)
        assert out["confidence"] == "low"  # 3 trades < 20

    def test_suspicious_includes_bundle_and_sniper_not_whale(self) -> None:
        from app.routers.rugcharts.classifier import volume_breakdown

        trades = [
            _labeled("bundle", 200.0),
            _labeled("sniper", 100.0),
            _labeled("whale", 700.0),
        ]
        out = volume_breakdown(trades)
        assert out["suspicious_volume_pct"] == pytest.approx(30.0)
        assert out["organic_volume_pct"] == pytest.approx(70.0)

    def test_empty_is_honest_none(self) -> None:
        from app.routers.rugcharts.classifier import volume_breakdown

        out = volume_breakdown([])
        assert out["bot_volume_pct"] is None
        assert out["buy_pressure"] is None
        assert out["confidence"] == "none"

    def test_confidence_tiers(self) -> None:
        from app.routers.rugcharts.classifier import volume_breakdown

        mid = [_labeled("swap", 10.0, time=i) for i in range(50)]
        assert volume_breakdown(mid)["confidence"] == "medium"
        big = [_labeled("swap", 10.0, time=i) for i in range(120)]
        assert volume_breakdown(big)["confidence"] == "high"

    def test_label_breakdown_shape(self) -> None:
        from app.routers.rugcharts.classifier import volume_breakdown

        out = volume_breakdown([_labeled("bot", 400.0), _labeled("bot", 100.0)])
        entry = out["label_breakdown"]["bot"]
        assert entry["trades"] == 2
        assert entry["volume_usd"] == pytest.approx(500.0)
        assert entry["volume_pct"] == pytest.approx(100.0)


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


class TestVolumeAnalysisEndpoint:
    def test_endpoint_returns_breakdown_and_bias(self, client: TestClient, monkeypatch) -> None:
        import app.routers.rugcharts.routes as routes

        # Raw UNLABELED trades that genuinely classify: 6 machine-gun trades
        # (60s apart, $100 each) → bot; one $250 whale; one organic sell.
        trades = (
            [_trade(1000 + i * 60, 100.0, wallet="botter") for i in range(6)]
            + [_trade(2000, 250.0, wallet="whale")]
            + [_trade(2100, 150.0, "sell", wallet="human")]
        )
        monkeypatch.setattr(routes, "_fetch_recent_trades_clickhouse", AsyncMock(return_value=trades))

        resp = client.get("/api/v1/rugcharts/volume-analysis/ethereum/0xABC?limit=100")
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["bot_volume_pct"] == pytest.approx(60.0)  # 600/1000
        assert data["sample"]["trades_analyzed"] == 8
        bias = data["predictive_bias"]
        assert bias["bias"] in {"BULLISH", "BEARISH", "NEUTRAL"}
        assert bias["method"] == "transparent_rule_fusion"
        assert all("evidence" in f for f in bias["factors"])

    def test_endpoint_empty_trades_still_200(self, client: TestClient, monkeypatch) -> None:
        import app.routers.rugcharts.routes as routes

        monkeypatch.setattr(routes, "_fetch_recent_trades_clickhouse", AsyncMock(return_value=[]))
        resp = client.get("/api/v1/rugcharts/volume-analysis/solana/MINT")
        assert resp.status_code == 200
        assert resp.json()["data"]["confidence"] == "none"


class TestWsStatsFrames:
    def test_stats_frame_pushed_periodically(self, client: TestClient, monkeypatch) -> None:
        import app.routers.rugcharts.ws as ws_mod

        trades = [_labeled("bot", 800.0, time=1700000000 + i * 60) for i in range(6)]
        monkeypatch.setattr(ws_mod, "_fetch_recent_trades_clickhouse", AsyncMock(return_value=trades))
        # Fast-forward time: 50ms polls, stats every 2nd poll.
        monkeypatch.setattr(ws_mod, "POLL_INTERVAL_S", 0.05)
        monkeypatch.setattr(ws_mod, "STATS_EVERY_N_POLLS", 2)

        with client.websocket_connect("/ws/trades?chain=ethereum&token=0xSTATS") as ws:
            hello = json.loads(ws.receive_text())
            assert hello["type"] == "hello"

            saw_stats = False
            for _ in range(6):  # hello + trades/stats mix; stats lands by poll#2
                msg = json.loads(ws.receive_text())
                if msg["type"] == "stats":
                    saw_stats = True
                    assert msg["stats"]["bot_volume_pct"] == pytest.approx(100.0)
                    assert msg["sample"]["trades_analyzed"] == 6
                    break
                if msg["type"] == "trades":
                    continue
            assert saw_stats, "no stats frame within window"
