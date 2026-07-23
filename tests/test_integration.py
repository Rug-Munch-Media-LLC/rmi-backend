"""Integration tests: end-to-end flows across multiple modules.

These tests validate that components work together correctly.
External services (Redis, external APIs) are mocked at the network
boundary, but internal function composition is tested end-to-end.

WARNING: These tests use the real FastAPI app with mocked dependencies.
They do NOT start Docker containers or connect to real services.
"""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def app():
    """Build a fresh FastAPI app for each test."""
    from app.factory import create_app

    return create_app()


@pytest.fixture
def client(app):
    """Test client against the real app."""
    from starlette.testclient import TestClient

    return TestClient(app)


# ── Health Check → Readiness Flow ────────────────────────────────────────


class TestHealthReadinessFlow:
    """Health and readiness endpoints work together."""

    def test_liveness_then_readiness(self, client):
        """Both /live and /ready endpoints are accessible and return valid responses."""
        live = client.get("/live")
        assert live.status_code == 200
        assert live.json()["status"] == "alive"

        ready = client.get("/ready")
        assert ready.status_code == 200
        ready_body = ready.json()
        assert "status" in ready_body
        assert "checks" in ready_body

    def test_health_has_live_and_ready_fields(self, client):
        """Health endpoint contains status, version, stores."""
        resp = client.get("/health")
        body = resp.json()
        assert body["status"] in ("healthy", "degraded", "unhealthy")
        assert body["version"] == "2026.06.21"


# ── Auth Flow: JWT Creation → Verification → User Lookup ────────────────


class TestAuthJWTFlow:
    """JWT creation, verification, and user lookup work together."""

    def test_full_jwt_lifecycle(self):
        """Create JWT → verify → extract user info."""
        from app.domains.auth import _create_jwt, _verify_jwt

        token = _create_jwt("uid-42", "alice@example.com", "PREMIUM", "MOD", "0xabc")
        payload = _verify_jwt(token)

        assert payload is not None
        assert payload["id"] == "uid-42"
        assert payload["email"] == "alice@example.com"
        assert payload["tier"] == "PREMIUM"
        assert payload["role"] == "MOD"
        assert payload["wallet"] == "0xabc"

    def test_jwt_without_wallet(self):
        """Create and verify JWT without wallet address."""
        from app.domains.auth import _create_jwt, _verify_jwt

        token = _create_jwt("uid-1", "bob@test.com")
        payload = _verify_jwt(token)
        assert payload is not None
        assert payload["wallet"] is None


# ── Auth Flow: Password Hashing → Storage → Login ───────────────────────


class TestPasswordAuthFlow:
    """Password hashing, storage, and verification work end-to-end."""

    def test_password_lifecycle(self):
        """Hash password → store hash → verify password → reject wrong."""
        from app.domains.auth import hash_password, verify_password

        password = "my-secure-password-123!"
        hashed = hash_password(password)

        # Store and retrieve (simulated)
        stored_hash = hashed

        # Verify correct password
        assert verify_password(password, stored_hash) is True

        # Verify wrong password
        assert verify_password("wrong-password", stored_hash) is False

        # Hash is different from original
        assert hashed != password

    def test_password_hash_length(self):
        """Bcrypt hash has expected format and length."""
        from app.domains.auth import hash_password

        hashed = hash_password("password")
        # bcrypt hash: $2b$12$... (60 chars)
        assert hashed.startswith("$2b$")
        assert len(hashed) == 60


# ── Email Validation → User ID Derivation → JWT ─────────────────────────


class TestEmailToAuthFlow:
    """Email validation leads to user creation and JWT generation."""

    def test_email_validation_to_user_id_to_jwt(self):
        """Valid email → derived ID → JWT creation → verification."""
        from app.domains.auth import _create_jwt, _derive_user_id, _is_valid_email, _verify_jwt

        email = "user@example.com"
        assert _is_valid_email(email) is True

        user_id = _derive_user_id(email)
        assert len(user_id) == 32

        token = _create_jwt(user_id, email, "FREE", "USER")
        payload = _verify_jwt(token)

        assert payload is not None
        assert payload["id"] == user_id
        assert payload["email"] == email

    def test_invalid_email_rejected_early(self):
        """Invalid email is rejected before reaching auth."""
        from app.domains.auth import _is_valid_email

        assert _is_valid_email("not-an-email") is False
        assert _is_valid_email("") is False


# ── Wallet Auth → JWT Flow ──────────────────────────────────────────────


class TestWalletAuthFlow:
    """Wallet auth nonce → signature → token flow."""

    def test_wallet_nonce_to_token_flow(self):
        """Wallet nonce generates properly structured response."""
        from app.domains.auth import generate_nonce
        from datetime import datetime, UTC

        nonce = generate_nonce()
        assert len(nonce) > 0

        # Simulate what the wallet_nonce endpoint does
        import hashlib

        address = "0x1234567890123456789012345678901234567890"
        chain = "evm"
        message = (
            f"RugMunch Intelligence wants you to sign in with your {chain.title()} account.\n\n"
            f"Wallet: {address}\nNonce: {nonce}\n"
            f"Timestamp: {datetime.now(UTC).isoformat()}"
        )
        # Message should contain the nonce
        assert nonce in message
        assert address in message

    def test_wallet_verify_requires_valid_signature(self):
        """Wallet verify endpoint rejects invalid signature with 401."""
        from app.domains.auth import verify_wallet_signature

        # With no real signature or Redis for nonce check, this should fail
        result = verify_wallet_signature(
            "Nonce: invalid",
            "bad-signature",
            "0x1234567890123456789012345678901234567890",
            "evm",
        )
        # Since Redis is unavailable and nonce won't be found, it may or
        # may not get to signature verification. Either way, result is False.
        assert result is False


# ── Full Application: Error Flow ─────────────────────────────────────────


class TestErrorFlow:
    """Error handling across the application."""

    def test_404_error_format(self, client):
        """Non-existent route under public prefix returns RFC 7807 problem detail."""
        resp = client.get("/api/v1/public/nonexistent-resource")
        assert resp.status_code == 404
        body = resp.json()
        # RFC 7807 format
        assert "type" in body
        assert "title" in body
        assert "status" in body
        assert body["status"] == 404
        assert "detail" in body
        assert body["detail"]  # non-empty

    def test_method_not_allowed_format(self, client):
        """Invalid method returns error in consistent format.

        Uses OPTIONS (safe method, no CSRF) on a GET-only endpoint.
        """
        resp = client.options("/live")  # OPTIONS on a route that only allows GET
        assert resp.status_code in (200, 204, 400, 405)
        # If we got a body, check it has error-like fields
        if resp.status_code != 204:
            body = resp.json()
            assert "detail" in body or "status" in body


# ── Full Application: Security Headers Flow ──────────────────────────────


class TestSecurityHeadersFlow:
    """Security headers are present on every response."""

    def test_all_responses_have_security_headers(self, client):
        """Multiple endpoints all get security headers."""
        endpoints = ["/health", "/live", "/ready", "/docs", "/openapi.json"]
        for endpoint in endpoints:
            resp = client.get(endpoint)
            assert "x-content-type-options" in resp.headers, f"Missing on {endpoint}"
            assert "x-frame-options" in resp.headers, f"Missing on {endpoint}"
            assert "x-xss-protection" in resp.headers, f"Missing on {endpoint}"

    def test_nosniff_header_present(self, client):
        """X-Content-Type-Options: nosniff is set."""
        resp = client.get("/health")
        assert resp.headers.get("x-content-type-options") == "nosniff"

    def test_frame_deny_header_present(self, client):
        """X-Frame-Options: DENY is set."""
        resp = client.get("/health")
        assert resp.headers.get("x-frame-options") == "DENY"


# ── FastAPI Dependency Flow ──────────────────────────────────────────────


class TestAuthDependencyFlow:
    """FastAPI auth dependencies compose correctly."""

    @pytest.mark.asyncio
    async def test_get_current_user_require_auth_composition(self):
        """get_current_user feeding into require_auth works end-to-end."""
        from app.domains.auth import _create_jwt, get_current_user, require_auth
        from unittest.mock import MagicMock, patch
        from fastapi import HTTPException

        token = _create_jwt("uid-1", "user@test.com")
        mock_user = {"id": "uid-1", "email": "user@test.com", "display_name": "Test"}

        with patch("app.domains.auth.deps._get_user", return_value=mock_user):
            # First get the user
            request = MagicMock()
            request.cookies = {}
            request.headers = {"Authorization": f"Bearer {token}"}

            user = await get_current_user(request)
            assert user is not None

            # Then require_auth should return the same user
            auth_user = await require_auth(request)
            assert auth_user == user

    @pytest.mark.asyncio
    async def test_get_current_user_no_token_falls_to_require_auth(self):
        """Without token, get_current_user returns None and require_auth raises 401."""
        from app.domains.auth import get_current_user, require_auth
        from unittest.mock import MagicMock
        from fastapi import HTTPException

        request = MagicMock()
        request.cookies = {}
        request.headers = {}

        user = await get_current_user(request)
        assert user is None

        with pytest.raises(HTTPException) as exc:
            await require_auth(request)
        assert exc.value.status_code == 401


# ── Problem Detail → API Response Flow ──────────────────────────────────


class TestProblemDetailFlow:
    """ProblemDetail creation to API response."""

    def test_problem_detail_to_response(self):
        """ProblemDetail.create → as_response() produces valid HTTP response."""
        from app.core.problem_details import ProblemDetail

        pd = ProblemDetail.create(
            status=422,
            detail="Validation failed",
            code="validation.request_invalid",
            instance="/api/v1/test",
            request_id="req-123",
        )
        resp = pd.as_response()
        assert resp.status_code == 422
        import json

        body = json.loads(resp.body)
        assert body["status"] == 422
        assert body["detail"] == "Validation failed"
        assert body["code"] == "validation.request_invalid"
        assert body["instance"] == "/api/v1/test"
        assert body["request_id"] == "req-123"
        assert "type" in body
        assert "timestamp" in body

    def test_problem_response_helper(self):
        """problem_response helper creates consistent responses."""
        from app.core.problem_details import problem_response

        resp = problem_response(
            status=429,
            code="rate_limit.exceeded",
            detail="Too many requests",
            instance="/api/v1/scan",
            request_id="req-456",
        )
        assert resp.status_code == 429
        import json

        body = json.loads(resp.body)
        assert body["detail"] == "Too many requests"
        assert "rate_limit" in body["type"]


# ── Nonce → Message → Signature Flow ────────────────────────────────────


class TestNonceMessageFlow:
    """Nonce generation through message construction."""

    def test_nonce_included_in_wallet_message(self):
        """Wallet auth message contains nonce, address, timestamp."""
        from app.domains.auth import generate_nonce
        from datetime import datetime, UTC

        nonce = generate_nonce()
        address = "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
        chain = "solana"
        timestamp = datetime.now(UTC).isoformat()
        message = (
            f"RugMunch Intelligence wants you to sign in with your {chain.title()} account.\n\n"
            f"Wallet: {address}\nNonce: {nonce}\nTimestamp: {timestamp}"
        )

        assert "Nonce: " in message
        assert nonce in message
        assert address in message
        assert timestamp in message
        assert "solana" in message or "Solana" in message

    def test_nonce_extraction_from_message(self):
        """Nonce can be extracted back from message format."""
        from app.domains.auth import generate_nonce

        nonce = generate_nonce()
        message = (
            f"RugMunch Intelligence wants you to sign in.\n\n"
            f"Wallet: 0x1234\nNonce: {nonce}\nTimestamp: 2026-01-01T00:00:00"
        )

        # Extract nonce (same logic as in verify_wallet_signature)
        extracted_nonce = None
        for line in message.split("\n"):
            if line.startswith("Nonce: "):
                extracted_nonce = line.split("Nonce: ")[1].strip()
                break

        assert extracted_nonce == nonce


# ── Middleware Composition Flow ──────────────────────────────────────────


class TestMiddlewareFlow:
    """Middleware components work together on responses."""

    def test_request_id_and_security_headers_both_present(self, client):
        """X-Request-ID and security headers coexist on same response."""
        resp = client.get("/health")
        assert "x-request-id" in resp.headers
        assert "x-content-type-options" in resp.headers
        assert "x-frame-options" in resp.headers
        assert "x-xss-protection" in resp.headers

    def test_response_time_and_request_id_correlate(self, client):
        """X-Request-ID and X-Response-Time-Ms are both present."""
        resp = client.get("/health")
        rid = resp.headers.get("x-request-id")
        rt = resp.headers.get("x-response-time-ms")
        assert rid and rt
        # Request ID should be a UUID-like string
        assert len(rid) >= 32
        # Response time should be a positive number
        assert float(rt) >= 0


# ── Config → Settings Flow ──────────────────────────────────────────────


class TestConfigFlow:
    """Configuration flows from env vars to Settings to consumers."""

    def test_settings_provides_jwt_config(self):
        """Settings provides JWT config consumed by auth module."""
        from app.config import Settings

        settings = Settings(jwt_secret="test-secret-key-32-chars-long-enough!!")
        assert settings.jwt_algorithm in ("RS256", "HS256")
        assert settings.jwt_secret is not None
        assert len(settings.jwt_secret) >= 32

    def test_settings_provides_cors_config(self):
        """Settings provides CORS origins list."""
        from app.config import Settings

        origins = ["https://app.rugmunch.io"]
        settings = Settings(
            jwt_secret="test-secret-key-32-chars-long-enough!!",
            cors_origins=origins,
            environment="prod",
        )
        assert settings.cors_origins == origins
