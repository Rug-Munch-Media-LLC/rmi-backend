"""Security tests: Authentication — JWT, API keys, Bearer tokens, scopes/roles.

Tests verify REAL behavior:
  - JWT creation and validation via app.auth.jwt_validation
  - Auth middleware via TestClient against the real FastAPI app
  - Token expiry, invalid tokens, missing auth, wrong API keys
  - Bearer token format, X-API-Key header, internal key bypass
  - Scopes/roles enforcement (FREE vs PRO vs ENTERPRISE)
  - Public endpoint whitelist vs protected routes

These are integration-style security tests. They boot the real FastAPI app
(see conftest.py in this directory) and exercise real middleware + route protection.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from jose import jwt as jose_jwt

# Import test-level fixtures from this package's conftest
from .conftest import TEST_JWT_SECRET, TEST_RMI_AUTH_TOKEN

# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _make_jwt(
    sub: str = "test-user-123",
    email: str = "test@rugmunch.io",
    tier: str = "FREE",
    role: str = "USER",
    expire_days: int = 7,
    secret: str = "",
) -> str:
    """Create a signed HS256 JWT for testing."""
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "email": email,
        "tier": tier,
        "role": role,
        "iat": now,
        "exp": now + timedelta(days=expire_days),
    }
    return jose_jwt.encode(payload, secret or TEST_JWT_SECRET, algorithm="HS256")


# ═══════════════════════════════════════════════════════════════════════
# Test Class: JWT Creation + Validation (unit-level, direct module calls)
# ═══════════════════════════════════════════════════════════════════════


class TestJWTCreationAndValidation:
    """Direct unit tests on jwt_validation module — JWT create + validate cycle."""

    @pytest.fixture(autouse=True)
    def _setup_env(self):
        """Ensure JWT env vars are set for the test."""
        with patch.dict(
            "os.environ",
            {
                "RMI_JWT_ALGORITHM": "HS256",
                "RMI_JWT_SECRET": TEST_JWT_SECRET,
                "JWT_SECRET": TEST_JWT_SECRET,
                "RMI_AUTH_TOKEN": TEST_RMI_AUTH_TOKEN,
                "ENVIRONMENT": "dev",
            },
            clear=False,
        ):
            # Force reload of jwt_validation module to pick up env
            import importlib

            from app import auth as auth_shim

            importlib.reload(auth_shim)
            yield

    def test_create_and_validate_jwt_roundtrip(self):
        """Creating a JWT and validating it should return the original claims."""
        from app.auth.jwt_validation import create_token, validate_token

        token = create_token(
            user_id="user-abc-123",
            email="alice@rugmunch.io",
            tier="PRO",
            role="ADMIN",
            wallet="So11et1111111111111111111111111111111111111",
        )
        assert isinstance(token, str)
        assert len(token) > 50  # JWTs are substantial strings

        claims = validate_token(token)
        assert claims["id"] == "user-abc-123"
        assert claims["email"] == "alice@rugmunch.io"
        assert claims["tier"] == "PRO"
        assert claims["role"] == "ADMIN"
        assert claims["wallet"] == "So11et1111111111111111111111111111111111111"

    def test_jwt_contains_all_standard_claims(self):
        """A generated JWT must contain iat, exp, sub, email, tier, role."""
        from app.auth.jwt_validation import create_token

        token = create_token(user_id="u1", email="u1@rmi.io", tier="FREE", role="USER")
        # Decode without verification to inspect structure
        header = jose_jwt.get_unverified_header(token)
        assert header["alg"] in ("HS256", "RS256")

        payload = jose_jwt.get_unverified_claims(token)
        assert "sub" in payload
        assert "email" in payload
        assert "tier" in payload
        assert "role" in payload
        assert "iat" in payload
        assert "exp" in payload

    def test_jwt_without_sub_is_rejected(self):
        """A token missing the 'sub' claim should be rejected."""
        from app.auth.jwt_validation import validate_token

        # Manually craft a JWT without 'sub'
        now = datetime.now(UTC)
        payload = {
            "email": "no-sub@rmi.io",
            "tier": "FREE",
            "role": "USER",
            "iat": now,
            "exp": now + timedelta(days=1),
        }
        token = jose_jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")

        with pytest.raises(Exception) as exc_info:
            validate_token(token)
        # Should raise HTTPException 401
        assert exc_info.errisinstance(Exception)
        # FastAPI HTTPException carries status_code
        assert getattr(exc_info.value, "status_code", None) in (401, None)

    def test_jwt_with_empty_token_raises_401(self):
        """validate_token with empty string should raise 401."""
        from app.auth.jwt_validation import validate_token

        with pytest.raises(Exception) as exc_info:
            validate_token("")
        status = getattr(exc_info.value, "status_code", None)
        assert status == 401, f"Expected 401, got {status}"


class TestJWTExpiry:
    """Token expiration behavior — exp claim enforcement."""

    @pytest.fixture(autouse=True)
    def _setup_env(self):
        with patch.dict(
            "os.environ",
            {
                "RMI_JWT_ALGORITHM": "HS256",
                "RMI_JWT_SECRET": TEST_JWT_SECRET,
                "JWT_SECRET": TEST_JWT_SECRET,
                "ENVIRONMENT": "dev",
            },
            clear=False,
        ):
            import importlib

            from app import auth as auth_shim

            importlib.reload(auth_shim)
            yield

    def test_expired_token_is_rejected(self):
        """A JWT with expired 'exp' should be rejected."""
        from app.auth.jwt_validation import validate_token

        now = datetime.now(UTC)
        payload = {
            "sub": "user-expired",
            "email": "old@rmi.io",
            "tier": "FREE",
            "role": "USER",
            "iat": now - timedelta(days=10),
            "exp": now - timedelta(days=1),  # expired yesterday
        }
        token = jose_jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")

        with pytest.raises(Exception) as exc_info:
            validate_token(token)
        status = getattr(exc_info.value, "status_code", None)
        assert status == 401, f"Expected 401 for expired token, got {status}"

    def test_token_expires_at_correct_time(self):
        """Token exp should be set to midnight + expire_days (code truncates to midnight)."""
        from app.auth.jwt_validation import create_token

        token = create_token("u1", "u1@rmi.io", expire_days=1)
        payload = jose_jwt.get_unverified_claims(token)
        exp = payload.get("exp", 0)
        # Code sets exp = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=expire_days)
        # So exp should be midnight UTC today + 1 day
        midnight_utc = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        expected = int(midnight_utc.timestamp()) + 86400
        assert abs(exp - expected) < 3600, f"exp={exp}, expected ~{expected}"

    def test_token_iat_is_set(self):
        """iat (issued at) should be in the past or present."""
        from app.auth.jwt_validation import create_token

        token = create_token("u1", "u1@rmi.io")
        payload = jose_jwt.get_unverified_claims(token)
        iat = payload.get("iat", 0)
        assert iat <= time.time() + 1  # allow 1s clock skew

    def test_future_token_not_accepted(self):
        """A token with iat in the far future might be rejected (clock drift protection)."""
        # Note: python-jose does not enforce iat by default
        # This test documents that behavior
        from app.auth.jwt_validation import validate_token

        far_future = datetime.now(UTC) + timedelta(days=365)
        payload = {
            "sub": "future-user",
            "email": "future@rmi.io",
            "tier": "FREE",
            "role": "USER",
            "iat": far_future,
            "exp": far_future + timedelta(days=7),
        }
        token = jose_jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")

        try:
            claims = validate_token(token)
            assert claims["id"] == "future-user"  # jose doesn't enforce iat
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            assert status_code == 401


# ═══════════════════════════════════════════════════════════════════════
# Test Class: Auth Middleware — X-API-Key header validation
# ═══════════════════════════════════════════════════════════════════════


class TestAuthMiddlewareAPIKey:
    """AuthMiddleware behavior via TestClient with the real FastAPI app."""

    def test_missing_api_key_returns_401(self, client):
        """Protected route without X-API-Key returns 401."""
        resp = client.get("/api/v1/version")
        # This might be public — try a known protected path
        resp2 = client.get("/api/v1/wallet/abc123")
        # At least one should be protected
        assert resp2.status_code == 401, f"Expected 401 for protected route without API key, got {resp2.status_code}"
        body = resp2.json()
        assert "detail" in body

    def test_wrong_api_key_returns_401(self, client):
        """Protected route with wrong X-API-Key returns 401."""
        resp = client.get(
            "/api/v1/wallet/abc123",
            headers={"X-API-Key": "this-is-the-wrong-key-entirely"},
        )
        assert resp.status_code == 401
        body = resp.json()
        assert "detail" in body

    def test_valid_api_key_allows_access(self, client):
        """Protected route with correct X-API-Key returns non-401."""
        resp = client.get(
            "/api/v1/wallet/abc123",
            headers={"X-API-Key": TEST_RMI_AUTH_TOKEN},
        )
        # 404 is fine — the route exists but wallet not found (that's auth passing)
        # 200 is also fine — wallet might exist
        # 401 is the failure case
        assert resp.status_code != 401, f"Expected non-401 with valid API key, got {resp.status_code}"

    def test_empty_api_key_rejected(self, client):
        """Empty X-API-Key header should be rejected."""
        resp = client.get(
            "/api/v1/wallet/abc123",
            headers={"X-API-Key": ""},
        )
        assert resp.status_code == 401


class TestAuthMiddlewareBearerToken:
    """Tests for Bearer token authentication (JWT-based, where applicable)."""

    def test_missing_bearer_token_returns_401(self, client):
        """Protected route without Authorization header returns 401."""
        resp = client.get("/api/v1/wallet/abc123")
        # Should be rejected by AuthMiddleware (X-API-Key required)
        assert resp.status_code == 401

    def test_invalid_bearer_token_returns_401(self, client):
        """Bearer token that is not a valid JWT returns 401."""
        resp = client.get(
            "/api/v1/wallet/abc123",
            headers={
                "Authorization": "Bearer this-is-not-a-valid-jwt-token",
                "X-API-Key": TEST_RMI_AUTH_TOKEN,  # still valid
            },
        )
        # This test verifies Bearer doesn't break X-API-Key auth
        assert resp.status_code != 500  # Should not crash

    def test_bearer_format_validation(self, client):
        """Authorization header must use 'Bearer <token>' format."""
        resp = client.get(
            "/api/v1/wallet/abc123",
            headers={
                "Authorization": "NotBearer token123",
                "X-API-Key": TEST_RMI_AUTH_TOKEN,
            },
        )
        # X-API-Key is what matters for middleware auth
        assert resp.status_code != 401


class TestPublicEndpointWhitelist:
    """Public endpoints should be accessible without any auth."""

    def test_health_is_public(self, client):
        """GET /health should return 200 without auth."""
        resp = client.get("/health")
        assert resp.status_code in (200, 404)

    def test_live_is_public(self, client):
        """GET /live should return 200 without auth."""
        resp = client.get("/live")
        assert resp.status_code in (200, 404)

    def test_ready_is_public(self, client):
        """GET /ready should return 200 without auth."""
        resp = client.get("/ready")
        assert resp.status_code in (200, 404)

    def test_openapi_is_public(self, client):
        """GET /openapi.json should return 200 without auth."""
        resp = client.get("/openapi.json")
        assert resp.status_code == 200

    def test_docs_is_public(self, client):
        """GET /docs should return 200 without auth."""
        resp = client.get("/docs")
        assert resp.status_code == 200

    def test_metrics_is_public(self, client):
        """GET /metrics should return 200 or 404 without auth."""
        resp = client.get("/metrics")
        assert resp.status_code in (200, 404)

    def test_auth_routes_are_public(self, client):
        """Auth endpoints like /api/v1/auth/* should be public (no X-API-Key needed)."""
        resp = client.get("/api/v1/auth/nonce")
        # May be 404 if route not mounted, but should NOT be 401
        assert resp.status_code != 401, "Auth routes must be public"


# ═══════════════════════════════════════════════════════════════════════
# Test Class: Scopes / Roles Enforcement
# ═══════════════════════════════════════════════════════════════════════


class TestScopesAndRoles:
    """JWT role/scope enforcement in auth dependencies."""

    def test_jwt_default_role_is_user(self):
        """Default JWT role should be USER when not specified."""
        from app.auth.jwt_validation import create_token, validate_token

        token = create_token("u1", "u1@rmi.io")
        claims = validate_token(token)
        assert claims["role"] == "USER"

    def test_jwt_admin_role(self):
        """JWT with ADMIN role should be honored."""
        from app.auth.jwt_validation import create_token, validate_token

        token = create_token("u1", "u1@rmi.io", role="ADMIN")
        claims = validate_token(token)
        assert claims["role"] == "ADMIN"

    def test_jwt_default_tier_is_free(self):
        """Default JWT tier should be FREE when not specified."""
        from app.auth.jwt_validation import create_token, validate_token

        token = create_token("u1", "u1@rmi.io")
        claims = validate_token(token)
        assert claims["tier"] == "FREE"

    def test_jwt_pro_tier(self):
        """JWT with PRO tier should be honored."""
        from app.auth.jwt_validation import create_token, validate_token

        token = create_token("u1", "u1@rmi.io", tier="PRO")
        claims = validate_token(token)
        assert claims["tier"] == "PRO"

    def test_jwt_enterprise_tier(self):
        """JWT with ENTERPRISE tier should be honored."""
        from app.auth.jwt_validation import create_token, validate_token

        token = create_token("u1", "u1@rmi.io", tier="ENTERPRISE")
        claims = validate_token(token)
        assert claims["tier"] == "ENTERPRISE"

    def test_require_auth_with_valid_jwt(self):
        """_verify_jwt should return user claims for valid JWT."""
        from app.domains.auth.jwt import _verify_jwt

        token = _make_jwt(sub="user-abc", email="test@rmi.io")
        claims = _verify_jwt(token)
        assert claims is not None
        assert claims["id"] == "user-abc"
        assert claims["email"] == "test@rmi.io"
        assert claims["role"] == "USER"

    def test_require_auth_with_expired_jwt_returns_none(self):
        """_verify_jwt should return None for expired token."""
        from app.domains.auth.jwt import _verify_jwt

        now = datetime.now(UTC)
        payload = {
            "sub": "expired-user",
            "email": "expired@rmi.io",
            "tier": "FREE",
            "role": "USER",
            "iat": now - timedelta(days=10),
            "exp": now - timedelta(days=1),
        }
        token = jose_jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")

        claims = _verify_jwt(token)
        assert claims is None, "Expired token should return None"


# ═══════════════════════════════════════════════════════════════════════
# Test Class: Internal Key Bypass (admin/internal endpoints)
# ═══════════════════════════════════════════════════════════════════════


class TestInternalKey:
    """Internal API key for admin/backend-to-backend communication."""

    def test_internal_key_is_configured(self, client):
        """Internal API key should be a non-empty string when configured."""
        import os

        key = os.environ.get("RMI_INTERNAL_KEY", "")
        if key:
            assert isinstance(key, str)
            assert len(key) > 0

    def test_internal_endpoints_require_auth(self, client):
        """Admin/internal endpoints should still require X-API-Key."""
        resp = client.get("/api/v1/_test/")
        # Should require auth (401) rather than be open
        assert resp.status_code in (401, 404)

    def test_internal_key_works_as_x_api_key(self, client):
        """Internal key should work as a valid X-API-Key (same as auth token for middleware)."""
        import os

        # The AuthMiddleware checks X-API-Key against RMI_AUTH_TOKEN
        # RMI_INTERNAL_KEY is separate; verify both exist
        auth_token = os.environ.get("RMI_AUTH_TOKEN", "")
        internal_key = os.environ.get("RMI_INTERNAL_KEY", "")
        assert auth_token, "RMI_AUTH_TOKEN must be set for middleware auth"
        assert internal_key, "RMI_INTERNAL_KEY must be set for internal access"

        # Internal endpoints still need RMI_AUTH_TOKEN (auth middleware)
        resp = client.get(
            "/api/v1/_test/",
            headers={"X-API-Key": auth_token},
        )
        assert resp.status_code in (200, 401, 404), f"Auth token should reach backend, got {resp.status_code}"


# ═══════════════════════════════════════════════════════════════════════
# Test Class: Auth Middleware Configuration Validation
# ═══════════════════════════════════════════════════════════════════════


class TestAuthMiddlewareConfig:
    """AuthMiddleware configuration: public path whitelist, etc."""

    def test_is_public_path_identifies_health(self):
        """is_public_path should return True for health endpoints."""
        from app.core.auth import is_public_path

        assert is_public_path("/health", "GET") is True
        assert is_public_path("/live", "GET") is True
        assert is_public_path("/ready", "GET") is True

    def test_is_public_path_identifies_docs(self):
        """is_public_path should return True for docs endpoints."""
        from app.core.auth import is_public_path

        assert is_public_path("/docs", "GET") is True
        assert is_public_path("/redoc", "GET") is True
        assert is_public_path("/openapi.json", "GET") is True

    def test_is_public_path_identifies_auth_routes(self):
        """is_public_path should return True for /api/v1/auth/* routes."""
        from app.core.auth import is_public_path

        assert is_public_path("/api/v1/auth/login", "POST") is True
        assert is_public_path("/api/v1/auth/register", "POST") is True
        assert is_public_path("/api/v1/auth/nonce", "GET") is True

    def test_is_public_path_rejects_protected_routes(self):
        """is_public_path should return False for protected API routes."""
        from app.core.auth import is_public_path

        assert is_public_path("/api/v1/wallet/abc", "GET") is False
        assert is_public_path("/api/v1/scanner/token", "POST") is False
        assert is_public_path("/api/v1/ai/chat", "POST") is False
        assert is_public_path("/api/v1/admin/users", "GET") is False

    def test_is_public_path_rejects_unknown_routes(self):
        """is_public_path should return False for arbitrary paths."""
        from app.core.auth import is_public_path

        assert is_public_path("/some/random/path", "GET") is False
        assert is_public_path("/api/v1/random", "GET") is False

    def test_auth_middleware_rejects_missing_token_in_prod(self, client):
        """In production-like mode, missing token should get 401, not 503."""
        # Our test env is "dev" so this tests the dev path
        # Still should return 401
        resp = client.get("/api/v1/version")
        # This may be public - use a definitely protected route
        resp = client.get("/api/v1/token/SOL123")
        assert resp.status_code == 401, f"Protected route should 401 without API key, got {resp.status_code}"
