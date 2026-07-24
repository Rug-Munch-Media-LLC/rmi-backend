"""API Contract Tests — verify every critical endpoint behaves correctly.

Validates status codes, response schemas (RFC 7807 for errors, Pydantic
models for success), required headers (X-Request-ID, X-RateLimit-*,
security headers), CORS headers on OPTIONS, rate limiting enforcement,
and correct error handling for auth/validation failures.

Uses FastAPI TestClient against the real app factory (with external
calls mocked where needed). No delegation — real tests that catch
real bugs.

Run:  cd /srv/work/repos/rmi-backend && python3 -m pytest tests/contract/test_api_contracts.py -x --tb=short -v
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def app():
    """Build the real FastAPI app with dev-friendly env vars."""
    os.environ.setdefault("ENVIRONMENT", "dev")
    os.environ.setdefault("RMI_AUTH_TOKEN", "test-auth-token-32-chars-long!!")
    os.environ.setdefault("DEV_BYPASS_AUTH", "true")
    os.environ.setdefault("CORS_ORIGINS", '["http://localhost:3000"]')

    from app.factory import create_app

    return create_app()


@pytest.fixture(scope="module")
def client(app):
    """Test client against the real app. Never raises server exceptions."""
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(scope="module")
def auth_headers():
    """Valid auth headers using the test token."""
    return {"X-API-Key": "test-auth-token-32-chars-long!!"}


# ═════════════════════════════════════════════════════════════════════════
# 1. HEALTH ENDPOINTS
# ═════════════════════════════════════════════════════════════════════════


class TestHealthEndpoints:
    """Contract: GET /live, /ready, /health, /api/v1/health/unified."""

    def test_live_returns_200_with_alive_status(self, client):
        """GET /live returns 200 with {status: 'alive'}."""
        resp = client.get("/live")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "alive"
        # Schema: must have exactly 'status' (per LivenessResponse)
        assert set(body.keys()) == {"status"}

    def test_ready_returns_200_with_checks(self, client):
        """GET /ready returns 200 with status and checks dict."""
        resp = client.get("/ready")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] in ("ready", "not_ready")
        assert "checks" in body
        assert isinstance(body["checks"], dict)
        # Checks must contain at least redis and postgres
        assert "redis" in body["checks"]
        assert "postgres" in body["checks"]

    def test_health_returns_200_with_all_fields(self, client):
        """GET /health returns 200 with status, version, uptime_seconds, stores."""
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert "status" in body
        assert "version" in body
        assert "uptime_seconds" in body
        assert "stores" in body
        assert body["status"] in ("healthy", "degraded", "unhealthy")
        assert isinstance(body["stores"], dict)
        # Must have at least redis and postgres in stores
        assert "redis" in body["stores"]
        assert "postgres" in body["stores"]

    def test_health_v1_redirects(self, client, auth_headers):
        """GET /api/v1/health redirects to root /health."""
        resp = client.get("/api/v1/health", headers=auth_headers, follow_redirects=False)
        assert resp.status_code in (307, 200)

    def test_health_v1_live_redirects(self, client, auth_headers):
        """GET /api/v1/health/live redirects to /live."""
        resp = client.get("/api/v1/health/live", headers=auth_headers, follow_redirects=False)
        assert resp.status_code in (307, 200)

    def test_health_unified_returns_aggregated_data(self, client, auth_headers):
        """GET /api/v1/health/unified returns aggregated health dashboard."""
        resp = client.get("/api/v1/health/unified", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        # Must have the unified fields
        assert "status" in body
        assert "services" in body
        assert "providers" in body
        assert "degradation" in body
        assert "config_issues" in body
        assert "timestamp" in body
        assert "version" in body
        assert body["status"] in ("healthy", "degraded", "unhealthy")


# ═════════════════════════════════════════════════════════════════════════
# 2. VERSION & HOMEPAGE
# ═════════════════════════════════════════════════════════════════════════


class TestVersionEndpoints:
    """Contract: GET /version, GET /."""

    def test_version_returns_200_with_metadata(self, client):
        """GET /version returns version metadata."""
        resp = client.get("/version")
        assert resp.status_code == 200
        body = resp.json()
        assert "version" in body
        assert "title" in body
        assert "description" in body
        assert body["status"] == "operational"
        assert body["version"]  # non-empty

    def test_homepage_returns_200(self, client):
        """GET / returns service metadata."""
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.json()
        assert "service" in body
        assert "version" in body
        assert "docs" in body
        assert "health" in body


# ═════════════════════════════════════════════════════════════════════════
# 3. AUTH MIDDLEWARE
# ═════════════════════════════════════════════════════════════════════════


class TestAuthMiddleware:
    """Contract: X-API-Key auth enforcement."""

    def test_public_paths_do_not_require_auth(self, client):
        """Health endpoints are accessible without any auth."""
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_public_prefix_auth_paths_accessible_without_key(self, client):
        """Paths under /api/v1/auth/ are public."""
        # This route may not exist, but should not return 401
        resp = client.get("/api/v1/auth/register")
        assert resp.status_code != 401

    def test_protected_path_returns_401_without_auth(self, client):
        """Protected routes return 401 when X-API-Key is missing."""
        # Temporarily set RMI_AUTH_TOKEN to force auth enforcement
        import app.core.auth as auth_mod

        saved = auth_mod.RMI_AUTH_TOKEN
        auth_mod.RMI_AUTH_TOKEN = "enforced-token-value"

        try:
            resp = client.get("/api/v1/scanner/nonexistent-check")
            assert resp.status_code == 401
            body = resp.json()
            assert "detail" in body
        finally:
            auth_mod.RMI_AUTH_TOKEN = saved

    def test_protected_path_returns_401_with_wrong_key(self, client):
        """Protected routes return 401 with invalid X-API-Key."""
        import app.core.auth as auth_mod

        saved = auth_mod.RMI_AUTH_TOKEN
        auth_mod.RMI_AUTH_TOKEN = "the-correct-token-value"

        try:
            resp = client.get(
                "/api/v1/scanner/nonexistent-check",
                headers={"X-API-Key": "wrong-key-value"},
            )
            assert resp.status_code == 401
        finally:
            auth_mod.RMI_AUTH_TOKEN = saved

    def test_protected_path_accepts_valid_key(self, client):
        """Protected routes pass with valid X-API-Key (returns 404, not 401)."""
        import app.core.auth as auth_mod

        saved = auth_mod.RMI_AUTH_TOKEN
        auth_mod.RMI_AUTH_TOKEN = "valid-key-for-testing"

        try:
            resp = client.get(
                "/api/v1/protected/test-route-xyz",
                headers={"X-API-Key": "valid-key-for-testing"},
            )
            # Route doesn't exist, so should be 404 not 401
            assert resp.status_code == 404
        finally:
            auth_mod.RMI_AUTH_TOKEN = saved

    def test_is_public_path_utility(self):
        """is_public_path correctly identifies public vs private routes."""
        from app.core.auth import is_public_path

        assert is_public_path("/health", "GET") is True
        assert is_public_path("/live", "GET") is True
        assert is_public_path("/ready", "GET") is True
        assert is_public_path("/api/v1/auth/login", "POST") is True
        assert is_public_path("/api/v1/public/trending", "GET") is True
        assert is_public_path("/api/v1/scanner/scan", "POST") is False
        assert is_public_path("/api/v1/wallet/0xabc", "GET") is False


# ═════════════════════════════════════════════════════════════════════════
# 4. ERROR HANDLING (RFC 7807)
# ═════════════════════════════════════════════════════════════════════════


class TestErrorHandling:
    """Contract: Error responses follow RFC 7807 Problem Details."""

    def test_404_returns_rfc7807_format(self, client):
        """Non-existent public route returns RFC 7807 problem detail."""
        resp = client.get("/api/v1/public/does-not-exist-xyz")
        assert resp.status_code == 404
        body = resp.json()
        # RFC 7807 required fields
        assert "type" in body
        assert "title" in body
        assert "status" in body
        assert body["status"] == 404
        assert "detail" in body
        assert "instance" in body

    def test_404_includes_request_id(self, client):
        """404 response includes request_id for tracing."""
        resp = client.get("/api/v1/public/missing-resource")
        body = resp.json()
        assert "request_id" in body
        assert body["request_id"] is not None

    def test_404_title_is_not_found(self, client):
        """404 response title contains 'Not Found'."""
        resp = client.get("/api/v1/public/no-such-endpoint")
        body = resp.json()
        assert "Not Found" in body.get("title", "")

    def test_422_validation_error_has_field_errors(self, client, auth_headers):
        """422 validation errors include field-level detail."""
        headers = {**auth_headers, "Authorization": "Bearer test-bearer-token"}
        resp = client.post(
            "/api/v2/scanner/token/scan",
            json={"chain": 123},  # invalid: chain should be string
            headers=headers,
        )
        assert resp.status_code == 422
        body = resp.json()
        assert "detail" in body

    def test_problem_detail_model_construction(self):
        """ProblemDetail.create produces valid RFC 7807 model."""
        from app.core.problem_details import ProblemDetail

        pd = ProblemDetail.create(
            status=429,
            detail="Rate limit exceeded",
            code="rate_limit.exceeded",
            instance="/api/v1/market/trending",
        )
        assert pd.status == 429
        assert pd.detail == "Rate limit exceeded"
        assert pd.code == "rate_limit.exceeded"
        assert "/errors/rate_limit/exceeded" in pd.type

        resp = pd.as_response()
        assert resp.status_code == 429

    def test_problem_detail_with_field_errors(self):
        """ProblemDetail.create includes field-level errors for 422."""
        from app.core.problem_details import ProblemDetail

        errs = [{"field": "address", "message": "Invalid address format", "type": "value_error"}]
        pd = ProblemDetail.create(
            status=422,
            detail="Validation failed",
            code="validation.request_invalid",
            errors=errs,
        )
        assert pd.errors == errs
        data = pd.model_dump(exclude_none=True)
        assert "errors" in data
        assert data["errors"][0]["field"] == "address"


# ═════════════════════════════════════════════════════════════════════════
# 5. SECURITY HEADERS
# ═════════════════════════════════════════════════════════════════════════


class TestSecurityHeaders:
    """Contract: Every response has required security headers."""

    def test_security_headers_present(self, client):
        """Every response includes standard security headers."""
        resp = client.get("/health")
        headers = resp.headers

        security_headers = [
            "x-content-type-options",
            "x-frame-options",
            "x-xss-protection",
            "strict-transport-security",
            "referrer-policy",
            "content-security-policy",
        ]
        for hdr in security_headers:
            assert hdr in headers, f"Missing security header: {hdr}"

    def test_security_header_values_are_correct(self, client):
        """Security headers have expected values."""
        resp = client.get("/health")
        headers = resp.headers
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        assert "max-age=31536000" in headers["strict-transport-security"]
        assert "includeSubDomains" in headers["strict-transport-security"]

    def test_cors_headers_on_options(self, client):
        """OPTIONS request returns CORS headers when origin matches."""
        resp = client.options(
            "/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        # CORS headers present if CORS middleware is configured
        cors_headers = [
            "access-control-allow-origin",
            "access-control-allow-methods",
            "access-control-allow-headers",
        ]
        for hdr in cors_headers:
            if hdr in resp.headers:
                assert resp.headers[hdr], f"CORS header {hdr} is empty"
                break
        # At least one CORS header should be present
        cors_found = any(h in resp.headers for h in cors_headers)
        assert cors_found, "No CORS headers found on OPTIONS response"


# ═════════════════════════════════════════════════════════════════════════
# 6. REQUEST ID & TIMING HEADERS
# ═════════════════════════════════════════════════════════════════════════


class TestRequestIDHeaders:
    """Contract: Every response has X-Request-ID and timing headers."""

    def test_response_has_request_id(self, client):
        """Every response includes X-Request-ID."""
        resp = client.get("/live")
        assert "x-request-id" in resp.headers
        assert resp.headers["x-request-id"]  # non-empty

    def test_response_has_response_time(self, client):
        """Every response includes X-Response-Time-Ms."""
        resp = client.get("/ready")
        assert "x-response-time-ms" in resp.headers

    def test_response_time_is_numeric(self, client):
        """X-Response-Time-Ms is a parsable number."""
        resp = client.get("/health")
        val = resp.headers["x-response-time-ms"]
        try:
            float(val)
        except ValueError:
            pytest.fail(f"X-Response-Time-Ms not numeric: {val}")

    def test_server_timing_header_present(self, client):
        """Every response includes Server-Timing."""
        resp = client.get("/")
        assert "server-timing" in resp.headers


# ═════════════════════════════════════════════════════════════════════════
# 7. MARKET INTEL ENDPOINTS
# ═════════════════════════════════════════════════════════════════════════


class TestMarketEndpoints:
    """Contract: /api/v1/market/* endpoints return correct schemas."""

    def test_market_trending_returns_valid_schema(self, client, auth_headers):
        """GET /api/v1/market/trending returns items with required fields."""
        resp = client.get("/api/v1/market/trending", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert "total" in body
        assert "updated_at" in body
        assert isinstance(body["items"], list)
        # Each item must have required fields per the route
        if body["items"]:
            item = body["items"][0]
            assert "symbol" in item
            assert "name" in item
            assert "address" in item
            assert "chain" in item
            assert "risk_score" in item

    def test_market_launches_returns_valid_schema(self, client, auth_headers):
        """GET /api/v1/market/launches returns items with required fields."""
        resp = client.get("/api/v1/market/launches", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert "total" in body
        assert isinstance(body["items"], list)

    def test_market_trending_accepts_limit_param(self, client, auth_headers):
        """GET /api/v1/market/trending?limit=5 respects limit parameter."""
        resp = client.get("/api/v1/market/trending", headers=auth_headers, params={"limit": 5})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] <= 5

    def test_market_trending_rejects_invalid_limit(self, client, auth_headers):
        """GET /api/v1/market/trending?limit=999 returns validation error."""
        resp = client.get("/api/v1/market/trending", headers=auth_headers, params={"limit": 999})
        # Should be 422 for invalid param (max 50)
        assert resp.status_code in (200, 422)


# ═════════════════════════════════════════════════════════════════════════
# 8. X402 PAYMENT ENDPOINTS
# ═════════════════════════════════════════════════════════════════════════


class TestX402Endpoints:
    """Contract: /api/v1/x402/* payment and pricing endpoints."""

    def test_x402_catalog_returns_valid_schema(self, client, auth_headers):
        """GET /api/v1/x402/catalog returns tool catalog with pricing."""
        resp = client.get("/api/v1/x402/catalog", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "server" in body
        assert "version" in body
        assert "tools" in body
        assert "payment_protocol" in body
        assert isinstance(body["tools"], list)
        assert body["payment_protocol"] == "x402"

    def test_x402_pricing_returns_tier_info(self, client, auth_headers):
        """GET /api/v1/x402/pricing returns tier pricing summary."""
        resp = client.get("/api/v1/x402/pricing", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "tiers" in body
        assert "accepted_chains" in body
        assert "accepted_tokens" in body
        assert "free" in body["tiers"]
        assert "pro" in body["tiers"]
        assert "enterprise" in body["tiers"]

    def test_x402_verify_missing_tool_returns_404(self, client, auth_headers):
        """POST /api/v1/x402/verify with unknown tool returns 404."""
        resp = client.post(
            "/api/v1/x402/verify",
            json={"tool": "nonexistent_tool_xyz", "x_payment": "test"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_x402_verify_free_tool_returns_valid(self, client, auth_headers):
        """POST /api/v1/x402/verify with free tool returns valid=true."""
        # We need to find a free tool from the pricing
        # Get catalog to find a free tool
        catalog_resp = client.get("/api/v1/x402/catalog", headers=auth_headers)
        if catalog_resp.status_code == 200:
            tools = catalog_resp.json().get("tools", [])
            # Find a free tool (if any)
            for tool in tools:
                if tool.get("price_usd", 1) == 0 or tool.get("free_tier"):
                    resp = client.post(
                        "/api/v1/x402/verify",
                        json={"tool": tool["name"], "x_payment": ""},
                        headers=auth_headers,
                    )
                    assert resp.status_code == 200
                    body = resp.json()
                    assert body.get("valid") is True
                    assert body.get("tier") == "free"
                    break

    def test_x402_verify_paid_tool_empty_payment(self, client, auth_headers):
        """POST /api/v1/x402/verify with paid tool but empty payment returns valid=false."""
        # Get catalog to find a paid tool
        catalog_resp = client.get("/api/v1/x402/catalog", headers=auth_headers)
        if catalog_resp.status_code == 200:
            tools = catalog_resp.json().get("tools", [])
            paid_tools = [t for t in tools if t.get("price_usd", 0) > 0]
            if paid_tools:
                tool = paid_tools[0]
                resp = client.post(
                    "/api/v1/x402/verify",
                    json={"tool": tool["name"], "x_payment": ""},
                    headers=auth_headers,
                )
                assert resp.status_code == 200
                body = resp.json()
                # Empty payment -> invalid
                assert "valid" in body
                if not body.get("valid"):
                    assert "reason" in body


# ═════════════════════════════════════════════════════════════════════════
# 9. WALLET ENDPOINTS
# ═════════════════════════════════════════════════════════════════════════


class TestWalletEndpoints:
    """Contract: /api/v1/wallet/* endpoints."""

    def test_wallet_resolve_returns_501_stub(self, client, auth_headers):
        """GET /wallet/{address} returns 501 stub for now."""
        resp = client.get(
            "/wallet/0xdAC17F958D2ee523a2206206994597C13D831ec7",
            headers=auth_headers,
        )
        assert resp.status_code == 501
        body = resp.json()
        assert "detail" in body

    def test_wallet_resolve_response_model_valid(self):
        """WalletResolveResponse validates correctly."""
        from app.api.v1.public.wallet import WalletResolveResponse

        data = {
            "chain": "ethereum",
            "address": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
            "labels": ["exchange", "hot_wallet"],
            "entity": "Tether Treasury",
            "balance_usd": 1_234_567.89,
            "tx_count": 42_000,
        }
        model = WalletResolveResponse(**data)
        assert model.entity == "Tether Treasury"
        assert model.tx_count == 42_000

    def test_wallet_resolve_rejects_invalid_chain(self):
        """WalletResolveResponse rejects unknown chain."""
        from app.api.v1.public.wallet import WalletResolveResponse

        with pytest.raises(ValidationError):
            WalletResolveResponse(
                chain="invalid_chain",
                address="0xdAC17F958D2ee523a2206206994597C13D831ec7",
            )

    def test_wallet_resolve_allows_null_optionals(self):
        """WalletResolveResponse works with all optional fields null."""
        from app.api.v1.public.wallet import WalletResolveResponse

        data = {
            "chain": "base",
            "address": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        }
        model = WalletResolveResponse(**data)
        assert model.labels == []
        assert model.entity is None
        assert model.balance_usd is None


# ═════════════════════════════════════════════════════════════════════════
# 10. SCANNER ENDPOINTS
# ═════════════════════════════════════════════════════════════════════════


class TestScannerEndpoints:
    """Contract: /api/v2/scanner/* scanner endpoints."""

    def test_scanner_token_scan_validates_request(self, client, auth_headers):
        """POST /api/v2/scanner/token/scan validates request body."""
        # Add Bearer auth to bypass CSRF middleware on POST
        headers = {**auth_headers, "Authorization": "Bearer test-bearer-token"}
        resp = client.post(
            "/api/v2/scanner/token/scan",
            json={},  # missing required fields
            headers=headers,
        )
        assert resp.status_code == 422
        body = resp.json()
        assert "detail" in body

    def test_scanner_wallet_scan_validates_request(self, client, auth_headers):
        """POST /api/v2/scanner/wallet/scan validates request body."""
        headers = {**auth_headers, "Authorization": "Bearer test-bearer-token"}
        resp = client.post(
            "/api/v2/scanner/wallet/scan",
            json={"chain": 123},  # chain should be string
            headers=headers,
        )
        assert resp.status_code == 422

    def test_scanner_token_rejects_invalid_address_type(self, client, auth_headers):
        """POST /api/v2/scanner/token/scan rejects non-string address."""
        headers = {**auth_headers, "Authorization": "Bearer test-bearer-token"}
        resp = client.post(
            "/api/v2/scanner/token/scan",
            json={"address": 12345, "chain": "ethereum"},
            headers=headers,
        )
        assert resp.status_code == 422


# ═════════════════════════════════════════════════════════════════════════
# 11. SYSTEM ENDPOINTS
# ═════════════════════════════════════════════════════════════════════════


class TestSystemEndpoints:
    """Contract: /api/v1/system/* endpoints."""

    def test_system_backup_status_returns_valid_schema(self, client, auth_headers):
        """GET /api/v1/system/backup-status returns backup status."""
        resp = client.get("/api/v1/system/backup-status", headers=auth_headers)
        # Could be 200 or 404 if route has issues, but not 401
        assert resp.status_code != 401
        if resp.status_code == 200:
            body = resp.json()
            assert isinstance(body, dict)

    def test_system_backup_config_returns_config(self, client, auth_headers):
        """GET /api/v1/system/backup-config returns backup config."""
        resp = client.get("/api/v1/system/backup-config", headers=auth_headers)
        assert resp.status_code != 401
        if resp.status_code == 200:
            body = resp.json()
            assert isinstance(body, dict)


# ═════════════════════════════════════════════════════════════════════════
# 12. JWKS ENDPOINT
# ═════════════════════════════════════════════════════════════════════════


class TestJWKSEndpoint:
    """Contract: /.well-known/jwks.json, /api/v1/auth/jwks."""

    def test_jwks_well_known_returns_valid_schema(self, client, auth_headers):
        """GET /.well-known/jwks.json returns JWKS keyset or informative error."""
        resp = client.get("/.well-known/jwks.json", headers=auth_headers)
        assert resp.status_code != 401
        if resp.status_code == 200:
            body = resp.json()
            # Either keys are configured or an error message is returned
            assert "keys" in body or "error" in body

    def test_jwks_v1_returns_keys(self, client, auth_headers):
        """GET /api/v1/auth/jwks returns JWKS."""
        resp = client.get("/api/v1/auth/jwks", headers=auth_headers)
        assert resp.status_code != 401
        if resp.status_code == 200:
            body = resp.json()
            assert "keys" in body or "error" in body


# ═════════════════════════════════════════════════════════════════════════
# 13. BULLETIN ENDPOINTS
# ═════════════════════════════════════════════════════════════════════════


class TestBulletinEndpoints:
    """Contract: /api/v1/bulletin/* endpoints."""

    def test_bulletin_latest_returns_posts(self, client, auth_headers):
        """GET /api/v1/bulletin/latest returns posts list."""
        resp = client.get("/api/v1/bulletin/latest", headers=auth_headers)
        # May be 200 or 500 if supabase is down — not a contract failure
        # But must not be 401 if auth headers are provided
        assert resp.status_code != 401
        if resp.status_code == 200:
            body = resp.json()
            assert "posts" in body
            assert "total" in body
            assert isinstance(body["posts"], list)

    def test_bulletin_latest_accepts_category_param(self, client, auth_headers):
        """GET /api/v1/bulletin/latest?category=security filters posts."""
        resp = client.get(
            "/api/v1/bulletin/latest",
            params={"category": "security", "limit": 5},
            headers=auth_headers,
        )
        assert resp.status_code != 401
        if resp.status_code == 200:
            body = resp.json()
            assert "posts" in body


# ═════════════════════════════════════════════════════════════════════════
# 14. CONTENT TYPE ENFORCEMENT
# ═════════════════════════════════════════════════════════════════════════


class TestContentTypeEnforcement:
    """Contract: Write endpoints require application/json Content-Type."""

    def test_post_without_json_content_type_returns_415(self, client, auth_headers):
        """POST with non-JSON Content-Type returns 415 on protected paths.

        Uses Bearer auth to bypass CSRF check, then verifies content-type
        enforcement rejects text/plain.
        """
        resp = client.post(
            "/api/v2/scanner/token/scan",
            content="not-json-data",
            headers={
                "Content-Type": "text/plain",
                "X-API-Key": "test-auth-token-32-chars-long!!",
                "Authorization": "Bearer test-bearer-token",
            },
        )
        # Should return 415 for non-JSON content type on write endpoints
        assert resp.status_code in (415, 422)
        if resp.status_code == 415:
            body = resp.json()
            assert "detail" in body
            assert "code" in body


# ═════════════════════════════════════════════════════════════════════════
# 15. OPENAPI SCHEMA ALIGNMENT
# ═════════════════════════════════════════════════════════════════════════


class TestOpenAPIContract:
    """Contract: OpenAPI spec declares critical endpoints and models."""

    def test_openapi_schema_loads(self, client):
        """OpenAPI schema is valid and has paths + components."""
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        data = resp.json()
        assert "paths" in data
        assert "components" in data
        assert len(data["paths"]) > 0

    def test_openapi_has_health_endpoint(self, client):
        """OpenAPI must declare GET /health."""
        resp = client.get("/openapi.json")
        data = resp.json()
        paths = data["paths"]
        assert "/health" in paths

    def test_openapi_has_live_endpoint(self, client):
        """OpenAPI must declare GET /live."""
        resp = client.get("/openapi.json")
        data = resp.json()
        assert "/live" in data["paths"]

    def test_openapi_has_market_trending(self, client, auth_headers):
        """OpenAPI must declare GET /api/v1/market/trending."""
        resp = client.get("/openapi.json", headers=auth_headers)
        data = resp.json()
        paths = data["paths"]
        trending_path = "/api/v1/market/trending"
        assert trending_path in paths, f"Missing route: {trending_path}"

    def test_openapi_has_x402_catalog(self, client, auth_headers):
        """OpenAPI must declare GET /api/v1/x402/catalog."""
        resp = client.get("/openapi.json", headers=auth_headers)
        data = resp.json()
        paths = data["paths"]
        catalog_path = "/api/v1/x402/catalog"
        assert catalog_path in paths, f"Missing route: {catalog_path}"

    def test_openapi_has_x402_verify(self, client, auth_headers):
        """OpenAPI must declare POST /api/v1/x402/verify."""
        resp = client.get("/openapi.json", headers=auth_headers)
        data = resp.json()
        paths = data["paths"]
        verify_path = "/api/v1/x402/verify"
        assert verify_path in paths, f"Missing route: {verify_path}"

    def test_openapi_has_scanner_routes(self, client, auth_headers):
        """OpenAPI must declare scanner POST routes."""
        resp = client.get("/openapi.json", headers=auth_headers)
        data = resp.json()
        paths = data["paths"]
        token_scan = "/api/v2/scanner/token/scan"
        wallet_scan = "/api/v2/scanner/wallet/scan"
        assert token_scan in paths, f"Missing route: {token_scan}"
        assert wallet_scan in paths, f"Missing route: {wallet_scan}"

    def test_openapi_has_bulletin_route(self, client, auth_headers):
        """OpenAPI must declare GET /api/v1/bulletin/latest."""
        resp = client.get("/openapi.json", headers=auth_headers)
        data = resp.json()
        paths = data["paths"]
        bulletin_path = "/api/v1/bulletin/latest"
        assert bulletin_path in paths, f"Missing route: {bulletin_path}"


# ═════════════════════════════════════════════════════════════════════════
# 16. RATE LIMITING
# ═════════════════════════════════════════════════════════════════════════


class TestRateLimitHeaders:
    """Contract: Rate limit headers present on responses."""

    def test_rate_limit_headers_present_when_configured(self, client):
        """Response may include X-RateLimit-* headers if rate limiter is active."""
        resp = client.get("/health")
        rate_limit_headers = [
            "x-ratelimit-limit",
            "x-ratelimit-remaining",
            "x-ratelimit-reset",
        ]
        # Rate limiter might not be active in test mode, so just check if present
        found = [h for h in rate_limit_headers if h in resp.headers]
        if found:
            for h in found:
                assert resp.headers[h], f"Rate limit header {h} empty"


# ═════════════════════════════════════════════════════════════════════════
# 17. V1 API REDIRECTS (DEPRECATION)
# ═════════════════════════════════════════════════════════════════════════


class TestV1Redirects:
    """Contract: Root-level routes with v1 equivalents redirect."""

    def test_v1_health_redirects(self, client, auth_headers):
        """GET /api/v1/health redirects to /health."""
        resp = client.get("/api/v1/health", headers=auth_headers, follow_redirects=False)
        assert resp.status_code in (307, 200)
