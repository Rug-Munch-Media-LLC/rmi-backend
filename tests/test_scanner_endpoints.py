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

import pytest
from httpx import ASGITransport, AsyncClient


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
