"""Tests for the /api/v1/launches/recent launch feed route.

All external tiers are mocked — no real network calls. The route must
cascade own ClickHouse → DexScreener → GMGN and never hard-fail.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    from app.main import app

    headers = {"X-API-Key": "test-auth-token-32-chars-long!!"}
    return TestClient(app, headers=headers)


@pytest.fixture(autouse=True)
def _mock_tiers(monkeypatch):
    """Isolate the route from real external services.

    Own-data (ClickHouse) and DexScreener are mocked to empty; GMGN is
    mocked to return a fixed launch. Bundle enrichment disabled by default.
    """
    import app.routers.v1.launches as L

    async def _fake_ds(chain: str, limit: int, pump_only: bool):
        return []

    async def _fake_gmgn(chain: str, limit: int, pump_only: bool):
        return [
            {
                "token_address": "GmgnTokenAddr1234567890",
                "chain": "solana",
                "name": "Test Pump",
                "symbol": "TPMP",
                "launch_timestamp": "2026-08-09T00:00:00+00:00",
                "liquidity": 5000,
                "holders": 120,
                "bundle_risk": None,
                "is_pump_fun": True,
                "source": "gmgn",
            }
        ]

    async def _fake_enrich(item):
        return item

    monkeypatch.setattr(L, "_own_data_launches", lambda *a, **k: [])
    monkeypatch.setattr(L, "_dexscreener_new_pairs", _fake_ds)
    monkeypatch.setattr(L, "_gmgn_launches", _fake_gmgn)
    monkeypatch.setattr(L, "_enrich_bundle_risk", _fake_enrich)


class TestRecentLaunches:
    def test_returns_gmgn_fallback(self, client):
        resp = client.get("/api/v1/launches/recent", params={"chain": "solana", "limit": 5})
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["sources"] == ["gmgn"]
        item = body["items"][0]
        assert item["token_address"] == "GmgnTokenAddr1234567890"
        assert item["is_pump_fun"] is True
        assert item["source"] == "gmgn"

    def test_limits_respected(self, client):
        resp = client.get("/api/v1/launches/recent", params={"limit": 1})
        assert resp.status_code == 200
        assert len(resp.json()["items"]) <= 1

    def test_chain_param_passthrough(self, client):
        resp = client.get("/api/v1/launches/recent", params={"chain": "ethereum", "limit": 3})
        assert resp.status_code == 200
        assert resp.json()["chain"] == "ethereum"

    def test_pump_only_flag(self, client):
        resp = client.get(
            "/api/v1/launches/recent",
            params={"chain": "solana", "pump_only": True, "limit": 3},
        )
        assert resp.status_code == 200
        assert resp.json()["pump_only"] is True

    def test_invalid_limit_400(self, client):
        resp = client.get("/api/v1/launches/recent", params={"limit": 0})
        assert resp.status_code == 422  # FastAPI Query ge=1 validation


class TestOwnDataCascade:
    def test_own_clickhouse_data_preferred(self, client, monkeypatch):
        """Tier-1 own data sorts first; GMGN only fills remaining capacity."""
        import app.routers.v1.launches as L

        async def _fake_gmgn(chain, limit, pump_only):
            return [
                {
                    "token_address": "GmgnFill1",
                    "chain": "ethereum",
                    "name": "Fill",
                    "symbol": "FILL",
                    "launch_timestamp": None,
                    "liquidity": 100,
                    "holders": 5,
                    "bundle_risk": None,
                    "is_pump_fun": False,
                    "source": "gmgn",
                }
            ]

        def _fake_own(chain, limit, pump_only):
            return [
                {
                    "token_address": "0xOwnToken123",
                    "chain": "ethereum",
                    "name": "",
                    "symbol": "",
                    "launch_timestamp": "2026-08-09T01:00:00+00:00",
                    "liquidity": None,
                    "holders": None,
                    "bundle_risk": None,
                    "is_pump_fun": False,
                    "source": "own_clickhouse",
                }
            ]

        monkeypatch.setattr(L, "_own_data_launches", _fake_own)
        monkeypatch.setattr(L, "_gmgn_launches", _fake_gmgn)

        resp = client.get("/api/v1/launches/recent", params={"chain": "ethereum", "limit": 5})
        assert resp.status_code == 200
        body = resp.json()
        # Own data is first in the list (priority), GMGN fills remaining capacity
        assert body["items"][0]["token_address"] == "0xOwnToken123"
        assert body["items"][0]["source"] == "own_clickhouse"
        assert body["items"][-1]["source"] == "gmgn"

    def test_dedupe_by_address(self, client, monkeypatch):
        """Same token from multiple tiers dedupes to one entry."""
        import app.routers.v1.launches as L

        def _fake_own(chain, limit, pump_only):
            return [
                {
                    "token_address": "DupToken1",
                    "chain": "solana",
                    "name": "Dup",
                    "symbol": "DUP",
                    "launch_timestamp": None,
                    "liquidity": None,
                    "holders": None,
                    "bundle_risk": None,
                    "is_pump_fun": True,
                    "source": "own_clickhouse",
                }
            ]

        async def _fake_gmgn(chain, limit, pump_only):
            return [
                {
                    "token_address": "DupToken1",
                    "chain": "solana",
                    "name": "Dup GMGN",
                    "symbol": "DUP",
                    "launch_timestamp": None,
                    "liquidity": 100,
                    "holders": 10,
                    "bundle_risk": None,
                    "is_pump_fun": True,
                    "source": "gmgn",
                }
            ]

        monkeypatch.setattr(L, "_own_data_launches", _fake_own)
        monkeypatch.setattr(L, "_gmgn_launches", _fake_gmgn)

        resp = client.get("/api/v1/launches/recent", params={"limit": 5})
        assert resp.status_code == 200
        body = resp.json()
        addrs = [i["token_address"] for i in body["items"]]
        assert addrs.count("DupToken1") == 1

    def test_bundle_enrichment_applies(self, client, monkeypatch):
        """Bundle enrichment adds bundle_risk dict when enabled."""
        import app.routers.v1.launches as L

        async def _fake_enrich(item):
            item["bundle_risk"] = {
                "is_bundled": True,
                "confidence": 0.8,
                "risk_label": "high",
                "top10_holder_pct": 0.9,
                "holder_count": 25,
            }
            return item

        monkeypatch.setattr(L, "_enrich_bundle_risk", _fake_enrich)

        resp = client.get(
            "/api/v1/launches/recent",
            params={"chain": "solana", "limit": 3, "enrich_bundle": True},
        )
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["bundle_risk"]["is_bundled"] is True
        assert item["bundle_risk"]["risk_label"] == "high"

    def test_all_tiers_fail_returns_empty(self, client, monkeypatch):
        """Route returns empty list (not 500) when every tier fails."""
        import app.routers.v1.launches as L

        async def _boom(chain, limit, pump_only):
            raise RuntimeError("external down")

        monkeypatch.setattr(L, "_own_data_launches", lambda *a, **k: [])
        monkeypatch.setattr(L, "_dexscreener_new_pairs", _boom)
        monkeypatch.setattr(L, "_gmgn_launches", _boom)

        resp = client.get("/api/v1/launches/recent", params={"limit": 5})
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


class TestTsToIso:
    def test_epoch_seconds(self):
        from app.routers.v1.launches import _ts_to_iso

        assert _ts_to_iso(1723000000).startswith("2024-08-07")

    def test_epoch_millis(self):
        from app.routers.v1.launches import _ts_to_iso

        assert _ts_to_iso(1723000000000).startswith("2024-08-07")

    def test_none(self):
        from app.routers.v1.launches import _ts_to_iso

        assert _ts_to_iso(None) is None

    def test_zero(self):
        from app.routers.v1.launches import _ts_to_iso

        assert _ts_to_iso(0) is None

    def test_garbage(self):
        from app.routers.v1.launches import _ts_to_iso

        assert _ts_to_iso("nonsense") is None
