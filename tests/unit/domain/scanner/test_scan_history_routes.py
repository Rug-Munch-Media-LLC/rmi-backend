"""Tests for GET /api/v1/data/scan-history and GET /api/v1/scanner/recent.

Covers:
  1. Route registration (mounted in the v1 aggregator)
  2. Frontend response contract: ``{"scans": [...], "count": N}``
  3. News service contract: plain JSON array with news-required fields
  4. Public auth: both routes reachable without X-API-Key (cookie-auth
     frontend + headerless news service)
  5. Row → ScanRecord mapping (DB path) and Redis cache fallback mapping

Run: pytest tests/unit/domain/scanner/test_scan_history_routes.py -v
"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.v1.data import _cache_to_scan, _row_to_scan
from app.routers.v1.scanner import router as scanner_recent_router

# ── Fixtures ────────────────────────────────────────────────────────────


def _make_app() -> FastAPI:
    """Minimal app with just the new routers — no heavy middleware/DB."""
    from app.routers.v1.data import router as data_router

    app = FastAPI()
    app.include_router(data_router, prefix="/api/v1")
    app.include_router(scanner_recent_router, prefix="/api/v1")
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_make_app(), raise_server_exceptions=False)


class FakeRow:
    """Minimal stand-in for a scan_results ORM row."""

    id = "abc-123"
    token_address = "0xDEADBEEF"
    chain = "ethereum"
    name = "ScamCoin"
    symbol = "SCAM"
    risk_score = 85
    risk_level = "critical"
    risk_flags = ["honeypot", "unlocked_lp", "mint_authority"]
    scanned_at = datetime(2026, 8, 1, 12, 0, 0)


SAMPLE_SCAN = {
    "id": "abc-123",
    "contract_address": "0xDEADBEEF",
    "token_address": "0xDEADBEEF",
    "chain": "ethereum",
    "token_name": "ScamCoin",
    "token_symbol": "SCAM",
    "risk_score": 85,
    "risk_level": "critical",
    "is_honeypot": True,
    "is_rug_pull": False,
    "liquidity_locked": False,
    "has_mint_authority": True,
    "scanned_at": "2026-08-01T12:00:00",
}


# ═══════════════════════════════════════════════════════════════════════
# Route registration + response contracts
# ═══════════════════════════════════════════════════════════════════════


class TestScanHistoryRoute:
    """GET /api/v1/data/scan-history — frontend contract."""

    def _collect_paths(self) -> set[str]:
        """Recursively collect route paths from the v1 aggregator."""
        from app.routers.v1 import v1_router

        paths: set[str] = set()

        def walk(routes) -> None:
            for route in routes:
                if hasattr(route, "original_router"):  # FastAPI 0.141+ _IncludedRouter
                    inc = getattr(route, "include_context", None)
                    prefix = getattr(inc, "prefix", "") if inc is not None else ""
                    for r in route.original_router.routes:
                        sub_path = getattr(r, "path", "")
                        if sub_path:
                            paths.add(prefix + sub_path)
                        sub = getattr(r, "routes", None)
                        if sub:
                            walk(sub)
                    continue
                p = getattr(route, "path", "")
                if p:
                    paths.add(p)
                sub = getattr(route, "routes", None)
                if sub:
                    walk(sub)

        walk(v1_router.routes)
        return paths

    def test_route_registered_in_v1_aggregator(self):
        """Route must be reachable through the v1 aggregator router."""
        paths = self._collect_paths()
        assert any("scan-history" in p for p in paths)

    def test_empty_response_when_no_data(self, client):
        """Graceful degradation: empty scans + count 0 (no DB/Redis)."""
        with patch("app.routers.v1.data.fetch_recent_scans", new_callable=AsyncMock, return_value=[]):
            resp = client.get("/api/v1/data/scan-history?limit=5")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"scans": [], "count": 0}

    def test_response_shape_with_data(self, client):
        """Frontend ScanRecord contract: scans array + count."""
        with patch(
            "app.routers.v1.data.fetch_recent_scans",
            new_callable=AsyncMock,
            return_value=[SAMPLE_SCAN],
        ):
            resp = client.get("/api/v1/data/scan-history?limit=20")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        scan = body["scans"][0]
        assert scan["contract_address"] == "0xDEADBEEF"
        assert scan["chain"] == "ethereum"
        assert scan["risk_score"] == 85
        assert scan["scanned_at"] == "2026-08-01T12:00:00"
        assert "token_name" in scan and "token_symbol" in scan

    def test_address_filter_passed_to_fetcher(self, client):
        """The address query param must be forwarded."""
        with patch(
            "app.routers.v1.data.fetch_recent_scans",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_fetch:
            client.get("/api/v1/data/scan-history?address=0xCAFE&limit=10")
        mock_fetch.assert_awaited_once_with(limit=10, address="0xCAFE")


class TestScannerRecentRoute:
    """GET /api/v1/scanner/recent — news service contract."""

    def _collect_paths(self) -> set[str]:
        from app.routers.v1 import v1_router

        paths: set[str] = set()

        def walk(routes) -> None:
            for route in routes:
                if hasattr(route, "original_router"):  # FastAPI 0.141+ _IncludedRouter
                    inc = getattr(route, "include_context", None)
                    prefix = getattr(inc, "prefix", "") if inc is not None else ""
                    for r in route.original_router.routes:
                        sub_path = getattr(r, "path", "")
                        if sub_path:
                            paths.add(prefix + sub_path)
                        sub = getattr(r, "routes", None)
                        if sub:
                            walk(sub)
                    continue
                p = getattr(route, "path", "")
                if p:
                    paths.add(p)
                sub = getattr(route, "routes", None)
                if sub:
                    walk(sub)

        walk(v1_router.routes)
        return paths

    def test_route_registered_in_v1_aggregator(self):
        paths = self._collect_paths()
        assert any("scanner/recent" in p for p in paths)

    def test_empty_response_when_no_data(self, client):
        """News service expects a plain JSON array — not an object."""
        with patch("app.routers.v1.data.fetch_recent_scans", new_callable=AsyncMock, return_value=[]):
            resp = client.get("/api/v1/scanner/recent?limit=10")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_news_service_fields_present(self, client):
        """Fields the news fetcher reads: token_name, contract_address,
        chain, risk_score, is_honeypot, is_rug_pull, liquidity_locked,
        has_mint_authority, scanned_at."""
        with patch(
            "app.routers.v1.scanner.fetch_recent_scans",
            new_callable=AsyncMock,
            return_value=[SAMPLE_SCAN],
        ):
            resp = client.get("/api/v1/scanner/recent?limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        scan = data[0]
        for field in (
            "token_name",
            "contract_address",
            "chain",
            "risk_score",
            "is_honeypot",
            "is_rug_pull",
            "liquidity_locked",
            "has_mint_authority",
            "scanned_at",
        ):
            assert field in scan, f"missing news field: {field}"


# ═══════════════════════════════════════════════════════════════════════
# Mapping logic
# ═══════════════════════════════════════════════════════════════════════


class TestRowMapping:
    """scan_results ORM row → frontend ScanRecord mapping."""

    def test_row_to_scan(self):
        rec = _row_to_scan(FakeRow())
        assert rec["contract_address"] == "0xDEADBEEF"
        assert rec["risk_score"] == 85
        assert rec["is_honeypot"] is True
        assert rec["liquidity_locked"] is False  # unlocked_lp flag
        assert rec["has_mint_authority"] is True  # mint_authority flag
        assert rec["scanned_at"] == "2026-08-01T12:00:00"

    def test_cache_to_scan(self):
        payload = json.dumps(
            {
                "token_address": "0xCAFE",
                "chain": "base",
                "name": "Token2",
                "symbol": "T2",
                "safety_score": 20,  # risk = 80
                "risk_level": "high",
                "risk_flags": ["rug_pattern"],
                "scanned_at": "2026-08-01T10:00:00Z",
            }
        )
        rec = _cache_to_scan(payload, key="scan_result:test")
        assert rec is not None
        assert rec["contract_address"] == "0xCAFE"
        assert rec["risk_score"] == 80
        assert rec["is_rug_pull"] is True
        assert rec["scanned_at"].startswith("2026-08-01")

    def test_cache_to_scan_rejects_bad_payload(self):
        assert _cache_to_scan("not-json") is None
        assert _cache_to_scan(json.dumps({"chain": "eth"})) is None  # no token_address


# ═══════════════════════════════════════════════════════════════════════
# Auth: routes must be public
# ═══════════════════════════════════════════════════════════════════════


class TestPublicAccess:
    """Cookie-auth frontend + headerless news service need no X-API-Key."""

    def test_scan_history_public(self):
        from app.core.auth import is_public_path

        assert is_public_path("/api/v1/data/scan-history", "GET")

    def test_scanner_recent_public(self):
        from app.core.auth import is_public_path

        assert is_public_path("/api/v1/scanner/recent", "GET")
