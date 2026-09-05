"""Tests for the v1 scanner recent endpoint — /api/v1/scanner/recent.

Verifies the endpoint is public, returns HTTP 200, and always yields a
valid JSON array (possibly empty) — never a 500. The endpoint reads from
Postgres ``scan_results`` first (canonical store written by
``app.domains.scanner.unified.persistence.persist_result``) and falls back
to Redis caches when Postgres is unavailable.
"""

from __future__ import annotations

import os

# Force env vars BEFORE any app imports
os.environ["JWT_SECRET"] = "a" * 32
os.environ["ENVIRONMENT"] = "dev"
os.environ["RMI_AUTH_TOKEN"] = "test-auth-token-32-chars-long!!"
os.environ["DEV_BYPASS_AUTH"] = "false"

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def app():
    """Create a fresh FastAPI app for each test."""
    from app.factory import create_app

    return create_app()


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """The scan category allows 10 req/min (burst 2) — this file makes many
    scan POSTs, so the in-memory token bucket 429s later tests. Reset the
    limiter's in-memory buckets before each test."""
    from app.core.rate_limiter import get_rate_limiter

    limiter = get_rate_limiter()
    limiter._mem_buckets.clear()
    yield
    limiter._mem_buckets.clear()


@pytest.fixture
async def client(app):
    """Create an async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_scanner_recent_returns_200(client):
    """GET /api/v1/scanner/recent returns HTTP 200."""
    response = await client.get("/api/v1/scanner/recent")
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_scanner_recent_is_public(client):
    """The endpoint is on the public allowlist — no X-API-Key required."""
    response = await client.get("/api/v1/scanner/recent?limit=5")
    # Public path means we get 200 even without auth headers.
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_scanner_recent_valid_json_structure(client):
    """Response is a JSON array of scan records with the news-fetcher contract."""
    response = await client.get("/api/v1/scanner/recent?limit=5")
    assert response.status_code == 200, response.text
    data = response.json()
    assert isinstance(data, list), "expected a JSON array"

    for scan in data:
        assert isinstance(scan, dict)
        # News fetcher contract (fetchers_api.py) keys.
        assert "token_name" in scan
        assert "chain" in scan
        assert "risk_score" in scan
        assert "contract_address" in scan or "token_address" in scan


@pytest.mark.asyncio
async def test_scanner_recent_limit_respected(client):
    """limit query param is honored and clamped to the 1..100 range."""
    response = await client.get("/api/v1/scanner/recent?limit=100")
    assert response.status_code == 200, response.text
    data = response.json()
    assert isinstance(data, list)
    assert len(data) <= 100

    # Out-of-range limit → 422 validation error, not 500.
    response = await client.get("/api/v1/scanner/recent?limit=0")
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_scan_history_endpoint_returns_200(client):
    """Sibling /api/v1/data/scan-history endpoint stays healthy."""
    response = await client.get("/api/v1/data/scan-history?limit=5")
    assert response.status_code == 200, response.text
    data = response.json()
    assert isinstance(data, dict)
    assert "scans" in data
    assert "count" in data
    assert isinstance(data["scans"], list)


# ══════════════════════════════════════════════════════════════════════
# v2 unified scanner — /api/v2/scanner/*
# ══════════════════════════════════════════════════════════════════════
# The v2 token/wallet scan endpoints are public (PUBLIC_PREFIXES) and
# dependency-inject the scanner via ``Depends(get_token_scanner)`` so we
# override it with an AsyncMock returning a real ScanResult. No live
# Redis/ClickHouse/Qdrant is touched.

from unittest.mock import AsyncMock  # noqa: E402 — module docstring precedes import block

from fastapi.testclient import TestClient  # noqa: E402 — module docstring precedes import block

if TYPE_CHECKING:
    from app.domains.scanner.unified.model import ScanResult


def _make_result(
    token_address: str,
    chain: str = "ethereum",
    *,
    risk_level: str = "low",
    risk_flags: list[str] | None = None,
    tier: str = "free",
) -> ScanResult:
    from app.domains.scanner.unified.model import ScanResult

    return ScanResult(
        token_address=token_address,
        chain=chain,
        scanned_at="2026-08-07T00:00:00Z",
        name="TestToken",
        symbol="TST",
        price_usd=1.23,
        market_cap_usd=1_000_000.0,
        liquidity_usd=500_000.0,
        volume_24h_usd=10_000.0,
        age_hours=72.0,
        safety_score=80.0,
        confidence=95.0,
        risk_level=risk_level,
        risk_flags=risk_flags or [],
        green_flags=["liquidity_locked", "mint_revoked"],
        modules_run=["core", "baseline", "sentinel"],
        enrichment_sources=["price:own", "security:own_clickhouse"],
        tier=tier,
        scan_duration_ms=1234.5,
        background_status="complete",
    )


@pytest.fixture(scope="module")
def scanner_app():
    """Module-scoped app for the v2 scanner tests (TestClient without
    context manager skips lifespan — no Redis/ClickHouse startup)."""
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
def mock_scanner():
    """AsyncMock token scanner returning a fresh ScanResult per address."""

    scanner = AsyncMock()

    async def fake_scan(address, chain="solana", tier="free", admin_key=None):
        return _make_result(address, chain)

    scanner.scan = fake_scan
    return scanner


@pytest.fixture
def sync_client(scanner_app, mock_scanner):
    """TestClient with the token scanner dependency overridden to the mock."""
    from app.features import get_token_scanner

    scanner_app.dependency_overrides[get_token_scanner] = lambda: mock_scanner
    client = TestClient(scanner_app)
    yield client
    scanner_app.dependency_overrides.pop(get_token_scanner, None)
    client.close()


class TestTokenScanV2:
    def test_scan_submission_returns_result(self, sync_client, mock_scanner) -> None:
        """POST /api/v2/scanner/token/scan → 200 with the scan result fields."""
        resp = sync_client.post(
            "/api/v2/scanner/token/scan",
            json={"address": "0x1234567890abcdef1234567890abcdef12345678", "chain": "ethereum"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        assert body["token"] == "0x1234567890abcdef1234567890abcdef12345678"
        assert body["chain"] == "ethereum"
        assert body["safety_score"] == 80.0
        assert body["risk_level"] == "low"
        assert body["modules_run"] == ["core", "baseline", "sentinel"]

    def test_scan_risk_flags_propagate(self, sync_client, mock_scanner) -> None:
        """risk_flags from the ScanResult are returned verbatim."""

        async def flagged_scan(address, chain="solana", tier="free", admin_key=None):
            return _make_result(
                address,
                chain,
                risk_level="high",
                risk_flags=["honeypot_detected", "lp_not_locked"],
            )

        mock_scanner.scan = flagged_scan
        resp = sync_client.post(
            "/api/v2/scanner/token/scan",
            json={"address": "0x1234567890abcdef1234567890abcdef12345678", "chain": "ethereum"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["risk_flags"] == ["honeypot_detected", "lp_not_locked"]
        assert resp.json()["risk_level"] == "high"

    def test_payment_header_is_not_trusted(self, sync_client, mock_scanner) -> None:
        """X-Payment-Verified is client-controlled and MUST NOT unlock pro tier
        (TOP-100 #8, 2026-08-23). The header is ignored; tier stays 'free'."""
        seen = {}

        async def tiered_scan(address, chain="solana", tier="free", admin_key=None):
            seen["tier"] = tier
            return _make_result(address, chain, tier=tier)

        mock_scanner.scan = tiered_scan
        resp = sync_client.post(
            "/api/v2/scanner/token/scan",
            json={"address": "0x1234567890abcdef1234567890abcdef12345678", "chain": "ethereum"},
            headers={"X-Payment-Verified": "true"},
        )
        assert resp.status_code == 200, resp.text
        assert seen["tier"] == "free"
        assert resp.json()["tier"] == "free"

    def test_scan_missing_address_422(self, sync_client) -> None:
        """Missing required `address` field → 422, not 500."""
        resp = sync_client.post("/api/v2/scanner/token/scan", json={"chain": "ethereum"})
        assert resp.status_code == 422, resp.text

    def test_scan_non_string_address_422(self, sync_client) -> None:
        """address must be a string — a number is a Pydantic type error → 422."""
        resp = sync_client.post(
            "/api/v2/scanner/token/scan",
            json={"address": 12345, "chain": "ethereum"},
        )
        assert resp.status_code == 422, resp.text

    def test_concurrent_scans_are_isolated(self, sync_client, mock_scanner) -> None:
        """Two scan requests don't share scanner state — each returns its own token."""
        seen_addresses = []

        async def recording_scan(address, chain="solana", tier="free", admin_key=None):
            seen_addresses.append(address)
            return _make_result(address, chain)

        mock_scanner.scan = recording_scan
        r1 = sync_client.post(
            "/api/v2/scanner/token/scan",
            json={"address": "0x1234567890abcdef1234567890abcdef12345678", "chain": "ethereum"},
        )
        r2 = sync_client.post(
            "/api/v2/scanner/token/scan",
            json={"address": "0xabcdef1234567890abcdef1234567890abcdef12", "chain": "ethereum"},
        )
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["token"] == "0x1234567890abcdef1234567890abcdef12345678"
        assert r2.json()["token"] == "0xabcdef1234567890abcdef1234567890abcdef12"
        assert set(seen_addresses) == {
            "0x1234567890abcdef1234567890abcdef12345678",
            "0xabcdef1234567890abcdef1234567890abcdef12",
        }


class TestTokenScanStatusV2:
    @pytest.fixture
    def mock_scan_status(self, monkeypatch):
        from app.domains.scanner.unified import scanner as scanner_mod

        async def fake_status(chain: str, address: str) -> dict | None:
            return {
                "chain": chain,
                "address": address,
                "state": "running",
                "modules_run": ["core", "baseline", "sentinel"],
                "risk_flags": [],
            }

        monkeypatch.setattr(scanner_mod, "scan_status", fake_status)
        return fake_status

    @pytest.mark.usefixtures("_allow_global_rate_limits")
    def test_status_returns_state_and_modules(self, scanner_app, mock_scan_status) -> None:
        """GET /api/v2/scanner/token/scan/{chain}/{address}/status → state + modules_run."""
        client = TestClient(scanner_app)
        resp = client.get("/api/v2/scanner/token/scan/ethereum/0xAbC1234567890AbC1234567890AbC1234567890/status")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["state"] == "running"
        assert body["modules_run"] == ["core", "baseline", "sentinel"]
        client.close()

    @pytest.mark.usefixtures("_allow_global_rate_limits")
    def test_status_includes_risk_flags(self, scanner_app, monkeypatch) -> None:
        """Completed cached status surfaces risk_flags for the UI."""
        from app.domains.scanner.unified import scanner as scanner_mod

        async def fake_status(chain: str, address: str) -> dict | None:
            return {
                "chain": chain,
                "address": address,
                "state": "complete",
                "source": "result_cache",
                "modules_run": ["core", "baseline", "sentinel"],
                "risk_flags": ["honeypot_detected", "lp_not_locked"],
            }

        monkeypatch.setattr(scanner_mod, "scan_status", fake_status)
        client = TestClient(scanner_app)
        resp = client.get("/api/v2/scanner/token/scan/ethereum/0xBAD/status")
        assert resp.status_code == 200, resp.text
        assert resp.json()["risk_flags"] == ["honeypot_detected", "lp_not_locked"]
        client.close()

    @pytest.mark.usefixtures("_allow_global_rate_limits")
    def test_status_unknown_scan_404(self, scanner_app, monkeypatch) -> None:
        """No status exists → 404 with a ProblemDetail body, never 500."""
        from app.domains.scanner.unified import scanner as scanner_mod

        async def fake_status(chain: str, address: str) -> dict | None:
            return None

        monkeypatch.setattr(scanner_mod, "scan_status", fake_status)
        client = TestClient(scanner_app)
        resp = client.get("/api/v2/scanner/token/scan/ethereum/0xUNKNOWN/status")
        assert resp.status_code == 404, resp.text
        assert "detail" in resp.json()
        client.close()


class TestWalletScanV2:
    @pytest.mark.usefixtures("_allow_global_rate_limits")
    def test_wallet_scan_free_tier_gated(self, scanner_app) -> None:
        """Free-tier wallet scan is data-gated (P0-5): asks caller to sign up.

        The gate short-circuits BEFORE the scanner is invoked — so the
        wallet scanner dependency is overridden with a mock that must
        never be called.
        """
        from app.features import get_wallet_scanner

        never_called = AsyncMock()
        scanner_app.dependency_overrides[get_wallet_scanner] = lambda: never_called
        client = TestClient(scanner_app)
        try:
            resp = client.post(
                "/api/v2/scanner/wallet/scan",
                json={"address": "0xAbC1234567890AbC1234567890AbC1234567890"},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["data_gated"] == "requires_auth"
            assert "message" in body
            never_called.scan.assert_not_awaited()
        finally:
            scanner_app.dependency_overrides.pop(get_wallet_scanner, None)
            client.close()
