"""Integration tests for health and version endpoints.

Tests that core health routes respond correctly when dependencies are available.
The /live and /health routes depend on prometheus_client which may not be
installed in test environments — tests handle 404 gracefully for those.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure repo root on path so app.* imports work
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture(scope="module")
def app():
    """Build the FastAPI app once per module."""
    from app.factory import create_app

    return create_app()


@pytest.fixture
def auth_headers():
    """Standard auth header for protected endpoints."""
    return {"X-API-Key": "test-auth-token-32-chars-long!!"}


class TestLiveness:
    """Tests for /live endpoint — depends on prometheus_client being installed."""

    @pytest.mark.integration
    def test_live_returns_200_or_404(self, app) -> None:
        """GET /live returns 200 if prometheus_client installed, else 404.

        The health route imports from app.core.metrics which requires
        prometheus_client. When it's missing, the route doesn't mount.
        """
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/live")
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            assert resp.json() == {"status": "alive"}


class TestVersion:
    """Tests for /version endpoint — no external dependencies."""

    @pytest.mark.integration
    def test_version_returns_200(self, app) -> None:
        """GET /version must return 200 with version info."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/version")
        assert resp.status_code == 200
        body = resp.json()
        assert "version" in body
        assert "title" in body
        assert "status" in body
        assert body["title"] == "RMI Backend"

    @pytest.mark.integration
    def test_version_has_valid_version_string(self, app) -> None:
        """The version string should be present and non-empty."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/version")
        body = resp.json()
        assert len(body["version"]) > 0
        assert body["status"] == "operational"
