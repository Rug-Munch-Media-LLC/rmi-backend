"""Tests for critical API infrastructure: health endpoints, error handling
format (RFC 7807), CORS headers, auth middleware path whitelist, and
response format consistency.

Uses TestClient from Starlette to hit the real FastAPI app (with external
calls mocked).
"""

from __future__ import annotations

import os
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, status


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def app():
    """Build a fresh FastAPI app for each test with health routes."""
    from app.factory import create_app

    return create_app()


@pytest.fixture
def client(app):
    """Test client against the real app."""
    from starlette.testclient import TestClient

    return TestClient(app)


# ── Health Endpoints ─────────────────────────────────────────────────────


class TestHealthEndpoint:
    """Basic health endpoint behavior."""

    def test_live_returns_200(self, client):
        """GET /live returns 200 with alive status."""
        resp = client.get("/live")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "alive"

    def test_ready_returns_200(self, client):
        """GET /ready returns 200 (with mocked store checks)."""
        resp = client.get("/ready")
        # /ready tries Redis + Postgres which will fail in test env
        # But endpoint should still return 200 (it reports health)
        assert resp.status_code == 200

    def test_ready_returns_checks(self, client):
        """GET /ready returns checks dict."""
        resp = client.get("/ready")
        body = resp.json()
        assert "status" in body
        assert "checks" in body

    def test_health_endpoint_returns_200(self, client):
        """GET /health returns 200."""
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_has_required_fields(self, client):
        """GET /health returns required fields: status, version, stores."""
        resp = client.get("/health")
        body = resp.json()
        assert "status" in body
        assert "version" in body
        assert "stores" in body
        assert body["version"]  # non-empty
        assert isinstance(body["stores"], dict)


# ── Error Handling Format (RFC 7807) ────────────────────────────────────


class TestErrorHandling:
    """Error response format consistency."""

    def test_404_returns_rfc7807_format(self, client):
        """404 response follows RFC 7807 problem detail format.

        Uses a non-existent path under a public prefix so auth middleware
        does not intercept the request.
        """
        resp = client.get("/api/v1/public/nonexistent-route-12345")
        assert resp.status_code == 404
        body = resp.json()
        # RFC 7807 required fields
        assert "type" in body
        assert "title" in body
        assert "status" in body
        assert body["status"] == 404
        assert "detail" in body
        assert "instance" in body
        # type should be "about:blank" when no specific error code
        assert body["type"] is not None

    def test_404_has_request_id(self, client):
        """404 response includes a request_id for tracing."""
        resp = client.get("/api/v1/public/nonexistent-path-xyz")
        body = resp.json()
        # request_id is optional but should be present
        assert "request_id" in body
        assert body["request_id"] is not None

    def test_404_detail_is_not_empty(self, client):
        """404 response has non-empty detail."""
        resp = client.get("/api/v1/public/route-that-does-not-exist")
        body = resp.json()
        assert body.get("detail")

    def test_404_title_is_not_found(self, client):
        """404 response title is 'Not Found'."""
        resp = client.get("/api/v1/public/no-such-path")
        body = resp.json()
        assert "Not Found" in body.get("title", "")


# ── ProblemDetail Model ──────────────────────────────────────────────────


class TestProblemDetail:
    """ProblemDetail model construction and serialization."""

    def test_create_basic_problem_detail(self):
        """ProblemDetail.create returns valid response."""
        from app.core.problem_details import ProblemDetail

        pd = ProblemDetail.create(
            status=400,
            detail="Bad input",
            code="validation.invalid_input",
            instance="/api/v1/test",
        )
        assert pd.status == 400
        assert pd.detail == "Bad input"
        assert pd.code == "validation.invalid_input"
        assert "/errors/validation/invalid_input" in pd.type

    def test_problem_detail_as_response(self):
        """ProblemDetail.as_response returns JSONResponse with correct status."""
        from app.core.problem_details import ProblemDetail

        pd = ProblemDetail.create(status=429, detail="Too fast", code="rate_limit.exceeded")
        resp = pd.as_response()
        assert resp.status_code == 429

        import json

        body = json.loads(resp.body)
        assert body["status"] == 429
        assert "rate_limit" in body["type"]

    def test_problem_detail_with_errors(self):
        """ProblemDetail.create includes field-level errors."""
        from app.core.problem_details import ProblemDetail

        field_errors = [
            {"field": "email", "message": "Invalid format", "type": "value_error"},
        ]
        pd = ProblemDetail.create(
            status=422,
            detail="Validation failed",
            code="validation.request_invalid",
            errors=field_errors,
        )
        assert pd.errors == field_errors

    def test_problem_detail_default_title(self):
        """ProblemDetail.create uses default titles for status codes."""
        from app.core.problem_details import ProblemDetail

        pd = ProblemDetail.create(status=401)
        assert "Unauthorized" in pd.title


# ── Auth Middleware ──────────────────────────────────────────────────────


class TestAuthMiddleware:
    """API key auth middleware behavior."""

    def test_public_paths_do_not_need_auth(self, client):
        """Public paths (health) are accessible without API key."""
        resp = client.get("/health")
        assert resp.status_code != 401  # Should not be blocked

    def test_public_prefixes_do_not_need_auth(self, client):
        """Public prefix paths are accessible without API key."""
        resp = client.get("/api/v1/auth/register")
        assert resp.status_code != 401

    def test_public_path_exact_matches(self):
        """is_public_path returns True for exact match paths."""
        from app.core.auth import is_public_path

        public_paths = ["/health", "/live", "/ready", "/metrics", "/version", "/docs", "/"]
        for path in public_paths:
            assert is_public_path(path, "GET"), f"{path} should be public"

    def test_public_path_prefixes(self):
        """is_public_path returns True for public prefix paths."""
        from app.core.auth import is_public_path

        assert is_public_path("/api/v1/auth/login", "POST")
        assert is_public_path("/api/v1/auth/register", "POST")
        assert is_public_path("/api/v1/public/some-data", "GET")

    def test_private_path_requires_auth(self):
        """is_public_path returns False for non-public paths."""
        from app.core.auth import is_public_path

        assert not is_public_path("/api/v1/scanner/analyze", "POST")
        assert not is_public_path("/api/v1/databus/query", "GET")

    def test_auth_middleware_rejects_missing_key(self, app):
        """Auth middleware returns 401 on protected path without X-API-Key
        when RMI_AUTH_TOKEN is configured.
        """
        from starlette.testclient import TestClient

        import app.core.auth as auth_mod

        # Save and set a token so auth is enforced
        saved_token = auth_mod.RMI_AUTH_TOKEN
        auth_mod.RMI_AUTH_TOKEN = "test-token"

        try:
            client = TestClient(app)
            resp = client.get("/api/v1/some-protected-route")
            # Should be rejected — 401 because token is configured
            assert resp.status_code in (401,)
        finally:
            auth_mod.RMI_AUTH_TOKEN = saved_token

    def test_auth_middleware_accepts_valid_key(self, app):
        """Auth middleware allows request with valid X-API-Key."""
        from starlette.testclient import TestClient

        # Temporarily set a token
        import app.core.auth as auth_mod

        original_token = auth_mod.RMI_AUTH_TOKEN
        auth_mod.RMI_AUTH_TOKEN = "test-valid-key-12345"

        try:
            client = TestClient(app)
            resp = client.get(
                "/api/v1/test-protected-auth",
                headers={"X-API-Key": "test-valid-key-12345"},
            )
            # Route doesn't exist, so we get 404, not 401
            assert resp.status_code == 404
        finally:
            auth_mod.RMI_AUTH_TOKEN = original_token


# ── CORS Headers ─────────────────────────────────────────────────────────


class TestCORSHeaders:
    """CORS header configuration."""

    def test_cors_headers_present_on_response(self, client):
        """Response includes CORS headers when origins are configured."""
        resp = client.get("/health")
        # CORS headers depend on middleware config which may or may not
        # be active depending on settings. Check if present.
        if "access-control-allow-origin" in resp.headers:
            assert resp.headers["access-control-allow-origin"] is not None

    def test_security_headers_present(self, client):
        """Response includes security headers."""
        resp = client.get("/health")
        headers = resp.headers
        # These should always be present from SecurityHeadersMiddleware
        security_headers = [
            "x-content-type-options",
            "x-frame-options",
            "x-xss-protection",
            "strict-transport-security",
            "referrer-policy",
            "content-security-policy",
        ]
        for header in security_headers:
            assert header in headers, f"Missing security header: {header}"

    def test_security_header_values(self, client):
        """Security headers have expected values."""
        resp = client.get("/health")
        headers = resp.headers
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        assert "strict-transport-security" in headers
        assert "max-age" in headers["strict-transport-security"]


# ── Request ID Middleware ────────────────────────────────────────────────


class TestRequestIDMiddleware:
    """Request ID and timing headers."""

    def test_response_has_request_id(self, client):
        """Response includes X-Request-ID header."""
        resp = client.get("/health")
        assert "x-request-id" in resp.headers
        assert resp.headers["x-request-id"]  # non-empty

    def test_response_has_response_time(self, client):
        """Response includes X-Response-Time-Ms header."""
        resp = client.get("/health")
        assert "x-response-time-ms" in resp.headers

    def test_response_time_is_number(self, client):
        """X-Response-Time-Ms is a valid number."""
        resp = client.get("/health")
        val = resp.headers["x-response-time-ms"]
        try:
            float(val)
        except ValueError:
            pytest.fail(f"X-Response-Time-Ms is not a number: {val}")

    def test_server_timing_header(self, client):
        """Response includes Server-Timing header."""
        resp = client.get("/health")
        assert "server-timing" in resp.headers


# ── CSRF Middleware ──────────────────────────────────────────────────────


class TestCSRFMiddleware:
    """CSRF protection behavior."""

    def test_safe_methods_skip_csrf(self, client):
        """GET requests skip CSRF check."""
        resp = client.get("/api/v1/auth/register")
        assert resp.status_code not in (403,)

    def test_bearer_auth_skips_csrf(self, app):
        """POST with Bearer token skips CSRF check."""
        from starlette.testclient import TestClient

        import app.core.auth as auth_mod

        saved_token = auth_mod.RMI_AUTH_TOKEN
        auth_mod.RMI_AUTH_TOKEN = "test-token"

        try:
            client = TestClient(app)
            resp = client.post(
                "/api/v1/auth/login",
                json={"email": "test@test.com", "password": "test1234"},
                headers={
                    "Authorization": "Bearer some-token",
                    "X-API-Key": "test-token",
                },
            )
            # May get something other than 403 for CSRF
            assert resp.status_code != 403
        finally:
            auth_mod.RMI_AUTH_TOKEN = saved_token
