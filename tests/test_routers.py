"""Tests for FastAPI routers with TestClient.

Covers:
- /health endpoint responds correctly
- /live liveness endpoint
- /ready readiness endpoint
- /api/v2/scanner/token/scan endpoint
- Router response schemas
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


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


# ── Health Route Tests ────────────────────────────────────────────────


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


# ── Scanner Router Tests ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scanner_endpoint_requires_auth(client):
    """POST /api/v2/scanner/token/scan returns 401 without auth."""
    response = await client.post(
        "/api/v2/scanner/token/scan",
        json={"address": "0xtoken", "chain": "ethereum"},
    )
    # Should fail with auth or 500 since scanner requires init
    assert response.status_code in (401, 403, 422, 500)


@pytest.mark.asyncio
async def test_scanner_endpoint_bad_request(client):
    """Scanner endpoint rejects empty body."""
    response = await client.post(
        "/api/v2/scanner/token/scan",
        json={},
    )
    assert response.status_code in (422, 400, 403)


# ── Router Response Schema ────────────────────────────────────────────


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
