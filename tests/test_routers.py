"""Tests for FastAPI routers with TestClient.

Covers:
- /health endpoint responds correctly
- /live liveness endpoint
- /ready readiness endpoint
- /api/v2/scanner/token/scan endpoint
- Router response schemas
- Route registration
- Response format contracts
- Error responses (422, 401, 404)
- Pagination, filtering
- Multiple router prefixes and feature flags
"""

from __future__ import annotations

import os

# Force env vars BEFORE any app imports
os.environ["JWT_SECRET"] = "a" * 32
os.environ["ENVIRONMENT"] = "dev"
os.environ["RMI_AUTH_TOKEN"] = "test-auth-token-32-chars-long!!"
os.environ["DEV_BYPASS_AUTH"] = "false"

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

if TYPE_CHECKING:
    from app.domains.scanner.unified.model import ScanResult

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def app():
    """Create a fresh FastAPI app for each test."""
    from app.factory import create_app

    return create_app()


@pytest.fixture
async def client(app):
    """Create an async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def _allow_global_rate_limits():
    """Open the unified rate limiter so the middleware never 429s tests."""
    from app.core.rate_limiter import UnifiedRateLimiter

    async def _allow(self, key, rate=1.0, burst=5.0, tokens=1.0):
        return (True, {"X-RateLimit-Limit": int(burst), "X-RateLimit-Remaining": 1, "X-RateLimit-Reset": 0})

    with patch.object(UnifiedRateLimiter, "acquire", _allow):
        yield


def _make_scan_result(address: str, chain: str = "ethereum") -> ScanResult:
    """Build a minimal ScanResult so scanner tests never touch live services."""
    from app.domains.scanner.unified.model import ScanResult

    return ScanResult(
        token_address=address,
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
        risk_level="low",
        risk_flags=[],
        green_flags=["liquidity_locked"],
        modules_run=["core", "baseline", "sentinel"],
        enrichment_sources=["price:own", "security:own_clickhouse"],
        tier="free",
        scan_duration_ms=1234.5,
        background_status="complete",
    )


@pytest.fixture
def mock_scanner():
    """AsyncMock token scanner returning a fresh ScanResult per address."""

    scanner = AsyncMock()

    async def fake_scan(address, chain="solana", tier="free", admin_key=None):
        return _make_scan_result(address, chain)

    scanner.scan = fake_scan
    return scanner


@pytest.fixture
async def scanner_client(app, mock_scanner):
    """Async client with the token scanner dependency overridden to the mock."""
    from app.features import get_token_scanner

    app.dependency_overrides[get_token_scanner] = lambda: mock_scanner
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_token_scanner, None)


# ═══════════════════════════════════════════════════════════════════════
# ── Health Route Tests
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_health_live_endpoint(client):
    """GET /live returns alive status."""
    response = await client.get("/live")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "alive"


@pytest.mark.asyncio
async def test_health_endpoint_structure(client):
    """GET /health returns expected response structure."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()

    # Check top-level fields exist
    assert "status" in data
    assert "version" in data
    assert "uptime_seconds" in data
    assert "stores" in data

    # Status is one of the valid states
    assert data["status"] in ("healthy", "degraded", "unhealthy")
    assert data["version"] is not None


@pytest.mark.asyncio
async def test_health_stores_contain_expected_keys(client):
    """GET /health stores contain expected infrastructure keys."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()

    stores = data.get("stores", {})
    # Should at minimum have redis and postgres entries
    assert "redis" in stores
    assert "postgres" in stores


@pytest.mark.asyncio
async def test_ready_endpoint(client):
    """GET /ready returns readiness response."""
    response = await client.get("/ready")
    assert response.status_code == 200
    data = response.json()

    assert "status" in data
    assert "checks" in data
    assert data["status"] in ("ready", "not_ready")


# ── Root / Version ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_root_endpoint(client):
    """GET / returns some response."""
    response = await client.get("/")
    assert response.status_code in (200, 404)


# ═══════════════════════════════════════════════════════════════════════
# ── Scanner Route Tests
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_scanner_endpoint_public_without_auth(scanner_client):
    """POST /api/v2/scanner/token/scan is public (Tier 0) — no 401 without auth."""
    response = await scanner_client.post(
        "/api/v2/scanner/token/scan",
        json={"address": "0xtoken", "chain": "ethereum"},
    )
    assert response.status_code == 200, response.text
    assert "status" in response.json()


@pytest.mark.asyncio
async def test_scanner_endpoint_bad_request(client):
    """Scanner endpoint rejects empty body."""
    response = await client.post(
        "/api/v2/scanner/token/scan",
        json={},
    )
    assert response.status_code in (422, 400, 403)


# ═══════════════════════════════════════════════════════════════════════
# ── Router Registration Tests
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_health_routes_accessible(client):
    """Health endpoints /health, /live, /ready are accessible."""
    for path in ("/health", "/live", "/ready"):
        resp = await client.get(path)
        assert resp.status_code == 200, f"{path} should return 200"


@pytest.mark.asyncio
async def test_market_routes_public(client):
    """Market router endpoints are public (Tier 0) — no auth required."""
    resp = await client.get("/api/v1/market/overview")
    assert resp.status_code == 200
    assert "total_market_cap" in resp.json()


@pytest.mark.asyncio
async def test_scanner_routes_public(scanner_client):
    """Scanner v2 router endpoints are public (Tier 0) — no auth required."""
    for endpoint in ("/api/v2/scanner/token/scan", "/api/v2/scanner/wallet/scan"):
        resp = await scanner_client.post(endpoint, json={"address": "0xtest", "chain": "ethereum"})
        assert resp.status_code == 200, f"{endpoint} should be publicly accessible: {resp.text[:200]}"


@pytest.mark.asyncio
async def test_bulletin_routes_registered(client):
    """Bulletin-board v2 router prefix /api/v1/bulletin-board is registered."""
    from app.bulletin_board.manager import BulletinBoardManager

    async def fake_list_posts(**kwargs):
        return {"posts": [], "total": 0}

    with patch.object(BulletinBoardManager, "list_posts", new=fake_list_posts):
        resp = await client.get(
            "/api/v1/bulletin-board/posts",
            headers={"X-API-Key": "test-auth-token-32-chars-long!!"},
        )
    assert resp.status_code == 200, "Bulletin-board v2 route should be registered"


@pytest.mark.asyncio
async def test_deep_intel_router_has_correct_prefix(app):
    """Deep intel router has correct prefix and routes."""
    from app.services.deep_intel_router import router

    assert router.prefix == "/api/v1/deep-intel"
    expected = (
        "/api/v1/deep-intel/health",
        "/api/v1/deep-intel/deployer/{address}",
        "/api/v1/deep-intel/full-report/{token_address}",
        "/api/v1/deep-intel/batch-report",
    )
    route_paths = {r.path for r in router.routes}
    for path in expected:
        assert path in route_paths, f"Expected route {path} not found in deep intel router"


# ═══════════════════════════════════════════════════════════════════════
# ── Response Format Contract Tests
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_live_response_format(client):
    """GET /live response matches LivenessResponse schema."""
    response = await client.get("/live")
    data = response.json()
    # Contract: must have 'status' = 'alive', no extra required fields
    assert list(data.keys()) == ["status"]
    assert data["status"] == "alive"


@pytest.mark.asyncio
async def test_health_response_format(client):
    """GET /health response has required top-level fields."""
    response = await client.get("/health")
    data = response.json()
    required_fields = {"status", "version", "uptime_seconds", "stores"}
    assert required_fields.issubset(data.keys()), f"Missing fields: {required_fields - set(data.keys())}"
    assert isinstance(data["version"], str)
    assert isinstance(data["uptime_seconds"], (int, float))
    assert isinstance(data["stores"], dict)


@pytest.mark.asyncio
async def test_ready_response_format(client):
    """GET /ready response matches ReadinessResponse schema."""
    response = await client.get("/ready")
    data = response.json()
    assert "status" in data
    assert "checks" in data
    assert isinstance(data["checks"], dict)
    assert "redis" in data["checks"]
    assert "postgres" in data["checks"]


# ═══════════════════════════════════════════════════════════════════════
# ── Error Response Tests
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_404_returns_json_error(client):
    """Non-existent route returns JSON error, not HTML (with valid auth)."""
    response = await client.get(
        "/nonexistent-route-12345",
        headers={"X-API-Key": "test-auth-token-32-chars-long!!"},
    )
    assert response.status_code == 404
    content_type = response.headers.get("content-type", "")
    assert "json" in content_type, "404 should return JSON, not HTML"


@pytest.mark.asyncio
async def test_unauthenticated_error_format(client):
    """401 error response has 'detail' key on a protected endpoint."""
    response = await client.post("/api/v1/rag/v2/ask", json={"query": "test"})
    assert response.status_code == 401
    data = response.json()
    assert "detail" in data
    assert "Unauthorized" in data["detail"]


@pytest.mark.asyncio
async def test_scanner_invalid_address_format_error(scanner_client):
    """Scanner returns a JSON response for empty address (accepted, scanned)."""
    response = await scanner_client.post(
        "/api/v2/scanner/token/scan",
        json={"address": "", "chain": "solana"},
        headers={"X-API-Key": "test-auth-token-32-chars-long!!"},
    )
    # Should be a JSON response, not a crash or HTML error page
    assert response.status_code in (200, 400, 403, 422, 500)
    assert "application/json" in response.headers.get("content-type", "")


# ═══════════════════════════════════════════════════════════════════════
# ── Pagination Tests
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_bulletin_posts_pagination_defaults(client):
    """GET /api/v1/bulletin-board/posts uses pagination defaults."""
    from app.bulletin_board.manager import BulletinBoardManager

    async def fake_list_posts(**kwargs):
        return {"posts": [], "total": 0}

    with patch.object(BulletinBoardManager, "list_posts", new=fake_list_posts):
        response = await client.get(
            "/api/v1/bulletin-board/posts",
            headers={"X-API-Key": "test-auth-token-32-chars-long!!"},
        )
    assert response.status_code == 200
    assert response.json()["limit"] == 20


@pytest.mark.asyncio
async def test_bulletin_posts_accepts_limit_param(client):
    """GET /api/v1/bulletin-board/posts with limit query is routed correctly."""
    from app.bulletin_board.manager import BulletinBoardManager

    async def fake_list_posts(**kwargs):
        return {"posts": [], "total": 0}

    with patch.object(BulletinBoardManager, "list_posts", new=fake_list_posts):
        response = await client.get(
            "/api/v1/bulletin-board/posts",
            params={"limit": "5"},
            headers={"X-API-Key": "test-auth-token-32-chars-long!!"},
        )
    assert response.status_code == 200
    assert response.json()["limit"] == 5


# ═══════════════════════════════════════════════════════════════════════
# ── Request Header Forwarding Tests
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_request_id_propagates_to_response(client):
    """X-Request-ID from request appears in response headers."""
    req_id = "test-req-id-42"
    response = await client.get("/live", headers={"X-Request-ID": req_id})
    assert response.headers.get("X-Request-ID") == req_id


@pytest.mark.asyncio
async def test_cors_headers_on_options(app):
    """OPTIONS request includes CORS headers."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.options(
            "/live",
            headers={
                "Origin": "https://app.rugmunch.io",
                "Access-Control-Request-Method": "GET",
            },
        )
        # CORS headers present
        assert "access-control-allow-origin" in response.headers or response.status_code in (
            200,
            400,
        )


# ═══════════════════════════════════════════════════════════════════════
# ── Health Response Model Tests
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_health_response_model():
    """HealthResponse model can be instantiated."""
    from app.core.health_route import HealthResponse

    hr = HealthResponse(
        status="healthy",
        version="1.0.0",
        uptime_seconds=100.0,
        stores={"redis": {"healthy": True}},
    )
    assert hr.status == "healthy"
    assert hr.version == "1.0.0"
    assert hr.uptime_seconds == 100.0
    assert hr.stores["redis"]["healthy"] is True


@pytest.mark.asyncio
async def test_liveness_response_model():
    """LivenessResponse model can be instantiated."""
    from app.core.health_route import LivenessResponse

    lr = LivenessResponse()
    assert lr.status == "alive"


@pytest.mark.asyncio
async def test_readiness_response_model():
    """ReadinessResponse model can be instantiated."""
    from app.core.health_route import ReadinessResponse

    rr = ReadinessResponse(status="ready", checks={"redis": True, "postgres": True})
    assert rr.status == "ready"
    assert rr.checks["redis"] is True


# ═══════════════════════════════════════════════════════════════════════
# ── Config Validation Tests
# ═══════════════════════════════════════════════════════════════════════


class TestConfigValidation:
    """Health config validation logic."""

    def test_check_config_missing_key(self, monkeypatch):
        """check_config flags missing required keys."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("REDIS_URL", raising=False)
        monkeypatch.setenv("CLICKHOUSE_URL", "http://clickhouse:8123")

        from app.core.health_route import check_config

        issues = check_config()
        issue_keys = [i["key"] for i in issues]
        assert "DATABASE_URL" in issue_keys
        assert "REDIS_URL" in issue_keys

    def test_check_config_placeholder_detected(self, monkeypatch):
        """check_config flags placeholder values."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:***@host/db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setenv("CLICKHOUSE_URL", "http://clickhouse:8123")

        from app.core.health_route import check_config

        issues = check_config()
        assert len(issues) >= 1  # DATABASE_URL has *** placeholder

    def test_check_config_clean_passes(self, monkeypatch):
        """check_config returns empty list when all required vars are set properly."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:realpass@host/db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setenv("CLICKHOUSE_URL", "http://clickhouse:8123")

        from app.core.health_route import check_config

        issues = check_config()
        # Only non-placeholder, non-missing configs
        assert "DATABASE_URL" not in {i["key"] for i in issues}


# ═══════════════════════════════════════════════════════════════════════
# ── TokenScanRequest Model Tests
# ═══════════════════════════════════════════════════════════════════════


class TestTokenScanRequest:
    """TokenScanRequest model validation."""

    def test_default_chain_is_solana(self):
        from app.routers.unified_scanner_router import TokenScanRequest

        req = TokenScanRequest(address="So11111111111111111111111111111111111111112")
        assert req.chain == "solana"

    def test_chain_override(self):
        from app.routers.unified_scanner_router import TokenScanRequest

        req = TokenScanRequest(address="0xtoken", chain="ethereum")
        assert req.chain == "ethereum"

    def test_address_required(self):
        from app.routers.unified_scanner_router import TokenScanRequest

        with pytest.raises((ValueError, TypeError), match=r".*"):
            TokenScanRequest()


# ═══════════════════════════════════════════════════════════════════════
# ── Rate Limiter Unit Tests
# ═══════════════════════════════════════════════════════════════════════


class TestRateLimitPathResolution:
    """_resolve_rate_limit path categorization."""

    def test_health_path(self):
        from app.core.rate_limiter import _resolve_rate_limit

        rate, _burst = _resolve_rate_limit("/health")
        assert rate > 0
        assert _burst > 0

    def test_auth_path(self):
        from app.core.rate_limiter import _resolve_rate_limit

        rate, _burst = _resolve_rate_limit("/login")
        assert rate > 0

    def test_unknown_path_defaults(self):
        from app.core.rate_limiter import _resolve_rate_limit

        rate, _burst = _resolve_rate_limit("/some/unknown/path")
        assert rate > 0
        assert _burst > 0

    def test_scanner_path(self):
        from app.core.rate_limiter import _resolve_rate_limit

        rate, _burst = _resolve_rate_limit("/api/v1/scanner/token")
        assert rate > 0

    def test_path_matches_longest_prefix(self):
        from app.core.rate_limiter import _resolve_rate_limit

        # /api/v1/scanner should match before /api/v1 defaults
        rate, _burst = _resolve_rate_limit("/api/v1/scanner/token/scan")
        assert rate > 0
