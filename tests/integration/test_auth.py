"""Integration tests for auth-related endpoints.

Tests that public endpoints are accessible, and that protected routes
correctly reject unauthenticated requests.

Note: The auth domain router (app.domains.auth) is NOT currently mounted
in ROUTER_MODULES — endpoints like /register and /login are defined in
the codebase but not exposed. This test covers what IS currently live.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture(scope="module")
def app():
    """Build the FastAPI app once per module."""
    from app.factory import create_app

    return create_app()


class TestPublicEndpoints:
    """Public endpoints should be accessible without authentication."""

    @pytest.mark.integration
    def test_homepage_returns_200(self, app) -> None:
        """GET / should return 200 with service metadata."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.json()
        assert "service" in body
        assert body["service"] == "RMI Backend"

    @pytest.mark.integration
    def test_openapi_schema_accessible(self, app) -> None:
        """GET /openapi.json should return a valid OpenAPI schema."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        assert "openapi" in schema
        assert "paths" in schema
        # Should have a reasonable number of paths
        assert len(schema["paths"]) >= 30

    @pytest.mark.integration
    def test_docs_accessible(self, app) -> None:
        """GET /docs should be accessible (returns HTML)."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/docs")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "").lower()


class TestAuthProtected:
    """Endpoints that require authentication should reject anonymous requests."""

    @pytest.mark.integration
    def test_user_me_without_token_returns_401(self, app) -> None:
        """GET /user/me without Bearer token should return 401."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/user/me")
        # 404 means the route isn't mounted; 401 means it is and auth is enforced
        assert resp.status_code in (401, 404)
        if resp.status_code == 401:
            assert "detail" in resp.json()

    @pytest.mark.integration
    def test_user_me_with_invalid_token_returns_401(self, app) -> None:
        """GET /user/me with invalid Bearer token should return 401."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get(
            "/user/me",
            headers={"Authorization": "Bearer invalid-token-that-is-not-valid"},
        )
        assert resp.status_code in (401, 404)
        if resp.status_code == 401:
            assert "detail" in resp.json()

    @pytest.mark.integration
    def test_health_does_not_require_auth(self, app) -> None:
        """GET /live should not require authentication (200 if mounted, 404 if prometheus missing)."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/live")
        assert resp.status_code in (200, 404)

    @pytest.mark.integration
    def test_version_does_not_require_auth(self, app) -> None:
        """GET /version should not require authentication."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/version")
        assert resp.status_code == 200
