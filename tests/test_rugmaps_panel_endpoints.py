"""Tests for the rugmaps/rugcharts panel endpoints added 2026-08-31.

Covers:
  - GET /api/v1/rugmaps/scan — structured red_flags from the SENTINEL scan
  - GET /api/v1/rugmaps/rug-warning — warnings mapped from red flags
  - GET /api/v1/rugmaps/deployer-tokens — wallet_memory deployer history
  - GET /api/v1/rugcharts/plugins/all — composed plugin analysis

External engines are mocked at the import boundary; response shapes are
exercised end-to-end through the FastAPI app.
"""

from __future__ import annotations

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


def _fake_scan_result(safety: int = 30, flags: list[str] | None = None):
    """Minimal stand-in for the SENTINEL ScanResult."""
    from app.domains.scanner.models import ScanResult

    return ScanResult(
        token_address="0xdead",
        chain="ethereum",
        symbol="SCAM",
        name="Scam Token",
        safety_score=safety,
        risk_flags=flags or [],
        confidence=60,
        modules_run=[{"module": "goplus", "status": "success"}],
    )


class TestRugmapsScan:
    def test_scan_returns_structured_red_flags(self, client: TestClient) -> None:
        result = _fake_scan_result(safety=20, flags=["honeypot", "bundled_launch", "unknown_new_flag"])
        with patch("app.domains.scanner.service.scan_token", new=AsyncMock(return_value=result)):
            resp = client.get("/api/v1/rugmaps/scan", params={"token": "0xdead", "chain": "ethereum"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        assert body["risk_score"] == 80
        assert body["risk_level"] == "critical"
        assert body["symbol"] == "SCAM"
        flags = body["red_flags"]
        assert isinstance(flags, list)
        assert flags
        for flag in flags:
            assert set(flag) >= {"id", "category", "severity", "title", "description"}
        # honeypot (critical) sorts before bundled_launch (high)
        assert flags[0]["id"] == "honeypot"
        assert flags[0]["severity"] == "critical"

    def test_scan_maps_risk_levels(self, client: TestClient) -> None:
        result = _fake_scan_result(safety=90, flags=[])
        with patch("app.domains.scanner.service.scan_token", new=AsyncMock(return_value=result)):
            body = client.get("/api/v1/rugmaps/scan", params={"token": "0xdead"}).json()
        assert body["risk_level"] == "low"
        assert body["red_flags"] == []

    def test_scan_engine_failure_is_reported_not_fabricated(self, client: TestClient) -> None:
        with patch("app.domains.scanner.service.scan_token", new=AsyncMock(side_effect=RuntimeError("boom"))):
            resp = client.get("/api/v1/rugmaps/scan", params={"token": "0xdead"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "scan_failed"
        assert body["risk_score"] is None
        assert body["red_flags"] == []


class TestRugmapsRugWarning:
    def test_warnings_mapped_from_flags(self, client: TestClient) -> None:
        result = _fake_scan_result(safety=10, flags=["honeypot", "bundled_launch"])
        with patch("app.domains.scanner.service.scan_token", new=AsyncMock(return_value=result)):
            resp = client.get("/api/v1/rugmaps/rug-warning", params={"chain": "ethereum", "token": "0xdead"})
        assert resp.status_code == 200
        body = resp.json()
        levels = {w["level"] for w in body["warnings"]}
        assert "CRITICAL" in levels
        assert "WARNING" in levels
        for w in body["warnings"]:
            assert set(w) >= {"id", "level", "category", "title", "description", "confidence", "detected_at"}

    def test_clean_token_has_no_warnings(self, client: TestClient) -> None:
        result = _fake_scan_result(safety=95, flags=[])
        with patch("app.domains.scanner.service.scan_token", new=AsyncMock(return_value=result)):
            body = client.get("/api/v1/rugmaps/rug-warning", params={"chain": "eth", "token": "0xdead"}).json()
        assert body["warnings"] == []
        assert body["status"] == "completed"


class TestRugmapsDeployerTokens:
    def test_returns_history_within_window(self, client: TestClient) -> None:
        import time

        now = time.time()
        history = [
            {"chain": "ethereum", "address": "0xaaa", "symbol": "AAA", "launched_at": now - 86400, "peak_mcap": 5.0},
            {"chain": "ethereum", "address": "0xbbb", "symbol": "BBB", "launched_at": now - 90 * 86400},
        ]

        class _FakeStorage:
            async def get_deployer_history(self, address: str) -> list[dict]:
                return history

        with patch("app.wallet_memory.storage.get_storage", return_value=_FakeStorage()):
            resp = client.get(
                "/api/v1/rugmaps/deployer-tokens",
                params={"deployer": "0xdeployer123456", "days": 30, "chain": "ethereum"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == "wallet_memory"
        assert [t["address"] for t in body["tokens"]] == ["0xaaa"]
        assert body["tokens"][0]["symbol"] == "AAA"

    def test_storage_failure_returns_empty_not_error(self, client: TestClient) -> None:
        with patch("app.wallet_memory.storage.get_storage", side_effect=RuntimeError("clickhouse down")):
            resp = client.get("/api/v1/rugmaps/deployer-tokens", params={"deployer": "0xdeployer123456"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["tokens"] == []
        assert body["source"] == "unavailable"


class TestRugchartsPluginsAll:
    def test_plugins_composed_from_helpers(self, client: TestClient) -> None:
        routes = "app.routers.rugcharts.routes"
        with (
            patch(f"{routes}._fetch_whale_trades_clickhouse", new=AsyncMock(return_value=[])),
            patch(
                f"{routes}._fetch_pair_info",
                new=AsyncMock(return_value={"liquidity_usd": 50_000.0, "volume_24h": 100_000.0}),
            ),
            patch(f"{routes}._fetch_ohlcv_clickhouse", new=AsyncMock(return_value=[])),
            patch(f"{routes}._fetch_holder_stats", new=AsyncMock(return_value={"top10_share": 0.4})),
        ):
            resp = client.get("/api/v1/rugcharts/plugins/all", params={"token_address": "0xdead", "chain": "ethereum"})
        assert resp.status_code == 200
        body = resp.json()
        plugins = body["plugins"]
        assert plugins["wallet_flow"]["count"] == 0
        assert "error" not in plugins["wallet_flow"]
        assert plugins["token_health"]["liquidity_usd"] == 50_000.0
        # Unimplemented plugins are explicit, never fake data
        assert plugins["contract_risk"] == {"error": "not_available"}
        assert "wallet_flow" in body["available"]
        assert "contract_risk" not in body["available"]

    def test_helper_failure_becomes_plugin_error(self, client: TestClient) -> None:
        routes = "app.routers.rugcharts.routes"
        with (
            patch(f"{routes}._fetch_whale_trades_clickhouse", new=AsyncMock(return_value=[])),
            patch(f"{routes}._fetch_pair_info", new=AsyncMock(side_effect=RuntimeError("gecko down"))),
            patch(f"{routes}._fetch_ohlcv_clickhouse", new=AsyncMock(return_value=[])),
            patch(f"{routes}._fetch_holder_stats", new=AsyncMock(return_value=None)),
        ):
            resp = client.get("/api/v1/rugcharts/plugins/all", params={"token_address": "0xdead", "chain": "eth"})
        assert resp.status_code == 200
        plugins = resp.json()["plugins"]
        assert "error" in plugins["risk_heatmap"]
        assert plugins["supply_donut"] == {"error": "no holder data available"}
