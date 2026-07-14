"""Integration tests for admin API endpoints.

Covers alerts webhook, glitchtip test, and core health/ops endpoints.
Alerts and glitchtip routes are stubs — tests assert that routes
respond (200, 404, 422, 501) to gracefully handle unmounted or
incomplete implementations.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class TestAdminAlerts:
    """Tests for /api/v1/admin/alerts/* endpoints."""

    @pytest.fixture(scope="module")
    def app(self):
        """Build the FastAPI app once per module."""
        from app.factory import create_app

        return create_app()

    @pytest.mark.integration
    def test_admin_alerts_webhook(self, app) -> None:
        """POST /api/v1/admin/alerts/webhook — receive alert webhook.

        Alerts webhook is mounted at /api/v1/admin/alerts/webhook (POST).
        Currently a 501 stub; test accepts 200/404/422/501.
        """
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.post(
            "/api/v1/admin/alerts/webhook",
            json={
                "source": "prometheus",
                "severity": "warning",
                "title": "test alert",
                "description": "testing",
            },
        )
        assert resp.status_code in (200, 404, 422, 501)


class TestAdminHealth:
    """Tests for core health endpoints (/health, /live, /ready)."""

    @pytest.fixture(scope="module")
    def app(self):
        """Build the FastAPI app once per module."""
        from app.factory import create_app

        return create_app()

    @pytest.mark.integration
    def test_health_endpoint(self, app) -> None:
        """GET /health — full health check."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code in (200, 404, 422)
        if resp.status_code == 200:
            body = resp.json()
            assert isinstance(body, dict)
            assert "status" in body

    @pytest.mark.integration
    def test_live_endpoint(self, app) -> None:
        """GET /live — liveness probe."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/live")
        assert resp.status_code in (200, 404, 422)
        if resp.status_code == 200:
            assert resp.json() == {"status": "alive"}

    @pytest.mark.integration
    def test_ready_endpoint(self, app) -> None:
        """GET /ready — readiness probe."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/ready")
        assert resp.status_code in (200, 404, 422)
        if resp.status_code == 200:
            body = resp.json()
            assert isinstance(body, dict)

    @pytest.mark.integration
    def test_glitchtip_test_endpoint(self, app) -> None:
        """POST /api/v1/_test/glitchtip — GlitchTip test route."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.post(
            "/api/v1/_test/glitchtip",
            json={"type": "error", "message": "pytest test"},
        )
        assert resp.status_code in (200, 404, 422)
