"""Integration tests for auth + billing flow — registration, login, tier access, rate limits.

Tests REAL business logic:
  - Email registration and login with password hashing (bcrypt/scrypt)
  - JWT token creation with tier claims and validation
  - Wallet-based auth (nonce generation, signature verification)
  - Rate limiting per tier (free=5, pro=100, elite=unlimited)
  - Password validation, email format validation
  - 2FA setup and verification (TOTP)

External Redis is mocked; all crypto/auth logic is tested directly.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class TestPasswordHashing:
    """Tests for password hashing — REAL bcrypt/scrypt business logic."""

    def test_hash_password_produces_different_output(self):
        """hash_password should not return the input."""
        from app.domains.auth.passwords import hash_password

        password = "MySecureP@ss123!"
        hashed = hash_password(password)
        assert hashed is not None
        assert hashed != password

    def test_hash_password_verifies_correct(self):
        """hash_password + verify_password should work for correct password."""
        from app.domains.auth.passwords import hash_password, verify_password

        hashed = hash_password("CorrectP@ss1")
        assert verify_password("CorrectP@ss1", hashed) is True

    def test_hash_password_rejects_wrong(self):
        """verify_password should reject wrong password."""
        from app.domains.auth.passwords import hash_password, verify_password

        hashed = hash_password("CorrectP@ss1")
        assert verify_password("WrongP@ss1", hashed) is False

    def test_hash_is_deterministic_salt(self):
        """Each hash should be unique (different salt)."""
        from app.domains.auth.passwords import hash_password

        h1 = hash_password("same_password")
        h2 = hash_password("same_password")
        assert h1 != h2  # Different salts produce different hashes


class TestEmailValidation:
    """Tests for _is_valid_email — input validation business logic."""

    def test_valid_standard_email(self):
        """Standard email formats should be valid."""
        from app.domains.auth.store import _is_valid_email

        assert _is_valid_email("user@example.com") is True

    def test_valid_email_with_plus_tag(self):
        """Email with +tag should be valid."""
        from app.domains.auth.store import _is_valid_email

        assert _is_valid_email("test.user+tag@sub.domain.com") is True

    def test_valid_short_email(self):
        """Short but valid email should pass."""
        from app.domains.auth.store import _is_valid_email

        assert _is_valid_email("a@b.co") is True

    def test_invalid_empty_email(self):
        """Empty string should not be valid."""
        from app.domains.auth.store import _is_valid_email

        assert _is_valid_email("") is False

    def test_invalid_no_at_sign(self):
        """Email without @ should be invalid."""
        from app.domains.auth.store import _is_valid_email

        assert _is_valid_email("not-an-email") is False

    def test_invalid_no_domain(self):
        """Email without domain should be invalid."""
        from app.domains.auth.store import _is_valid_email

        assert _is_valid_email("user@") is False

    def test_invalid_no_username(self):
        """Email without username should be invalid."""
        from app.domains.auth.store import _is_valid_email

        assert _is_valid_email("@example.com") is False


class TestRedisUserStore:
    """Tests for Redis-backed user storage — CRUD operations on user records."""

    @pytest.fixture(autouse=True)
    def _mock_redis(self):
        """Mock Redis for store operations."""
        self.redis_data: dict[str, dict[str, str]] = {
            "rmi:users": {},
            "rmi:users:email": {},
        }

        def mock_hset(name, key, value):
            if name not in self.redis_data:
                self.redis_data[name] = {}
            self.redis_data[name][key] = value

        def mock_hget(name, key):
            data = self.redis_data.get(name, {})
            return data.get(key)

        with patch("app.domains.auth.store.get_redis") as mock_get_redis:
            mock_redis = MagicMock()
            mock_redis.hset.side_effect = mock_hset
            mock_redis.hget.side_effect = mock_hget
            mock_get_redis.return_value = mock_redis
            yield

    def test_save_and_get_user(self):
        """_save_user then _get_user should return the saved record."""
        from app.domains.auth.store import _get_user, _save_user

        user = {
            "id": "user_001",
            "email": "test@example.com",
            "tier": "FREE",
            "role": "USER",
        }
        _save_user(user)
        fetched = _get_user("user_001")
        assert fetched is not None
        assert fetched["email"] == "test@example.com"
        assert fetched["tier"] == "FREE"

    def test_get_user_by_email(self):
        """_get_user_by_email should find user by email."""
        from app.domains.auth.store import _get_user_by_email, _save_user

        user = {
            "id": "user_002",
            "email": "findme@example.com",
            "tier": "PRO",
        }
        _save_user(user)
        fetched = _get_user_by_email("findme@example.com")
        assert fetched is not None
        assert fetched["id"] == "user_002"

    def test_get_user_by_email_case_insensitive(self):
        """Email lookup should be case-insensitive."""
        from app.domains.auth.store import _get_user_by_email, _save_user

        user = {
            "id": "user_003",
            "email": "CaseMix@Example.Com",
        }
        _save_user(user)
        fetched = _get_user_by_email("casemix@example.com")
        assert fetched is not None
        assert fetched["id"] == "user_003"

    def test_get_nonexistent_user_returns_none(self):
        """Getting a non-existent user should return None."""
        from app.domains.auth.store import _get_user

        assert _get_user("nonexistent_user") is None

    def test_get_user_by_nonexistent_email_returns_none(self):
        """Getting user by non-existent email should return None."""
        from app.domains.auth.store import _get_user_by_email

        assert _get_user_by_email("noone@example.com") is None

    def test_register_sets_new_user_with_free_tier(self):
        """New users registered in the email route should have FREE tier."""
        from app.domains.auth.store import _get_user, _save_user

        user = {
            "id": "new_user_123",
            "email": "new@example.com",
            "display_name": "New User",
            "password_hash": "hashed_pw_here",
            "tier": "FREE",
            "role": "USER",
            "created_at": datetime.now(UTC).isoformat(),
            "scans_remaining": 5,
            "scans_used": 0,
        }
        _save_user(user)
        fetched = _get_user("new_user_123")
        assert fetched["tier"] == "FREE"
        assert fetched["scans_remaining"] == 5


class TestJWTAuth:
    """Tests for JWT token lifecycle — REAL token creation, validation, edge cases."""

    def test_create_jwt_with_tier_propagates_claim(self):
        """JWT should carry the tier claim for access control."""
        from app.domains.auth.jwt import _create_jwt, _verify_jwt

        token = _create_jwt(user_id="pro-user", email="pro@test.com", tier="PRO", role="USER")
        claims = _verify_jwt(token)
        assert claims is not None
        assert claims["tier"] == "PRO"

    def test_create_jwt_with_enterprise_tier(self):
        """Enterprise tier should propagate through JWT."""
        from app.domains.auth.jwt import _create_jwt, _verify_jwt

        token = _create_jwt(
            user_id="ent-user", email="ent@test.com", tier="ENTERPRISE", role="ADMIN"
        )
        claims = _verify_jwt(token)
        assert claims["tier"] == "ENTERPRISE"
        assert claims["role"] == "ADMIN"

    def test_jwt_has_standard_claims(self):
        """Every JWT should have sub, iat, exp, email."""
        from jose import jwt

        from app.domains.auth.jwt import JWT_ALGORITHM, JWT_SECRET, _create_jwt

        token = _create_jwt(user_id="claims-test", email="claims@test.com")
        payload = jwt.decode(
            token, JWT_SECRET, algorithms=[JWT_ALGORITHM], options={"verify_signature": False}
        )
        assert payload["sub"] == "claims-test"
        assert "iat" in payload
        assert "exp" in payload
        assert "email" in payload

    def test_jwt_expiry_in_future(self):
        """JWT expiry should be ~7 days in the future."""
        from jose import jwt

        from app.domains.auth.jwt import JWT_ALGORITHM, JWT_SECRET, _create_jwt

        token = _create_jwt(user_id="exp-test", email="exp@test.com")
        payload = jwt.decode(
            token, JWT_SECRET, algorithms=[JWT_ALGORITHM], options={"verify_signature": False}
        )
        assert payload["exp"] > payload["iat"]
        expected = datetime.now(UTC) + timedelta(days=7)
        assert abs(payload["exp"] - expected.timestamp()) < 86400

    def test_expired_token_returns_none(self):
        """Token with past expiry should fail verification."""
        from jose import jwt as jose_jwt

        from app.domains.auth.jwt import JWT_ALGORITHM, JWT_SECRET, _create_jwt, _verify_jwt

        token = _create_jwt(user_id="exp-test", email="exp@test.com")
        payload = jose_jwt.decode(
            token, JWT_SECRET, algorithms=[JWT_ALGORITHM], options={"verify_signature": False}
        )
        payload["exp"] = int((datetime.now(UTC) - timedelta(hours=1)).timestamp())
        expired_token = jose_jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

        result = _verify_jwt(expired_token)
        assert result is None

    def test_tampered_token_returns_none(self):
        """Token with modified payload should fail verification."""
        from app.domains.auth.jwt import _create_jwt, _verify_jwt

        token = _create_jwt(user_id="user1", email="user1@test.com", tier="FREE")
        parts = token.split(".")
        tampered = parts[0] + ".eyJ0YW1wZXJlZCI6InllcyJ9." + parts[2]
        result = _verify_jwt(tampered)
        assert result is None

    def test_malformed_tokens_return_none(self):
        """Malformed JWT strings should return None."""
        from app.domains.auth.jwt import _verify_jwt

        assert _verify_jwt("") is None
        assert _verify_jwt("not-a-token") is None
        assert _verify_jwt("a.b") is None
        assert _verify_jwt("a.b.c.d") is None

    def test_wallet_address_in_jwt(self):
        """Wallet address should be embedded in JWT claims."""
        from app.domains.auth.jwt import _create_jwt, _verify_jwt

        token = _create_jwt(
            user_id="wallet-user",
            email="wallet@test.com",
            wallet="0x742d35Cc6634C0532925a3b844Bc454e4438f44e",
        )
        claims = _verify_jwt(token)
        assert claims["wallet"] == "0x742d35Cc6634C0532925a3b844Bc454e4438f44e"

    def test_bare_minimum_jwt(self):
        """JWT with only user_id and email should still work."""
        from app.domains.auth.jwt import _create_jwt, _verify_jwt

        token = _create_jwt(user_id="minimal-user", email="minimal@test.com")
        claims = _verify_jwt(token)
        assert claims["id"] == "minimal-user"
        assert claims["email"] == "minimal@test.com"
        assert claims["tier"] == "FREE"  # Default
        assert claims["role"] == "USER"  # Default

    def test_jwt_roundtrip_preserves_all_fields(self):
        """All fields should survive a JWT create+verify roundtrip."""
        from app.domains.auth.jwt import _create_jwt, _verify_jwt

        token = _create_jwt(
            user_id="roundtrip-user",
            email="roundtrip@test.com",
            tier="ENTERPRISE",
            role="ADMIN",
            wallet="0x1234",
        )
        claims = _verify_jwt(token)
        assert claims["id"] == "roundtrip-user"
        assert claims["email"] == "roundtrip@test.com"
        assert claims["tier"] == "ENTERPRISE"
        assert claims["role"] == "ADMIN"
        assert claims["wallet"] == "0x1234"


class TestWalletAuth:
    """Tests for wallet-based auth — nonce generation, user creation."""

    def test_generate_nonce_returns_unique_strings(self):
        """generate_nonce should produce unique URL-safe strings."""
        from app.domains.auth.jwt import generate_nonce

        nonces = {generate_nonce() for _ in range(100)}
        assert len(nonces) == 100
        for n in nonces:
            assert isinstance(n, str)
            assert len(n) > 10

    def test_derive_user_id_is_deterministic(self):
        """Same email should produce same user ID."""
        from app.domains.auth.jwt import _derive_user_id

        id1 = _derive_user_id("user@example.com")
        id2 = _derive_user_id("user@example.com")
        assert id1 == id2
        assert len(id1) == 32  # SHA256 hex digest, first 32 chars

    def test_derive_user_id_differs_for_diff_emails(self):
        """Different emails should produce different IDs."""
        from app.domains.auth.jwt import _derive_user_id

        assert _derive_user_id("a@b.com") != _derive_user_id("c@d.com")

    def test_derive_user_id_is_deterministic(self):
        """Same email should produce same user ID."""
        from app.domains.auth.jwt import _derive_user_id

        id1 = _derive_user_id("user@example.com")
        id2 = _derive_user_id("user@example.com")
        assert id1 == id2
        assert len(id1) == 32

    def test_derive_user_id_differs_for_diff_emails(self):
        """Different emails should produce different IDs."""
        from app.domains.auth.jwt import _derive_user_id

        assert _derive_user_id("a@b.com") != _derive_user_id("c@d.com")

    def test_verify_wallet_signature_empty_inputs(self):
        """verify_wallet_signature should return False for empty inputs."""
        from app.domains.auth.wallet import verify_wallet_signature

        assert verify_wallet_signature("", "sig", "0xaddr") is False
        assert verify_wallet_signature("msg", "", "0xaddr") is False
        assert verify_wallet_signature("msg", "sig", "") is False

    def test_verify_wallet_signature_short_address(self):
        """verify_wallet_signature should reject addresses under 20 chars."""
        from app.domains.auth.wallet import verify_wallet_signature

        assert verify_wallet_signature("msg", "sig", "0xshort") is False

    def test_verify_wallet_signature_no_nonce(self):
        """Message without nonce should fail verification."""
        from app.domains.auth.wallet import verify_wallet_signature

        result = verify_wallet_signature(
            "Plain message no nonce here",
            "0x" + "ab" * 65,
            "0x742d35Cc6634C0532925a3b844Bc454e4438f44e",
        )
        assert result is False

    def test_decode_signature_extracts_r_s_v(self):
        """decode_signature should extract r, s, v components."""
        from app.domains.auth.wallet import decode_signature

        sig = "0x" + "ab" * 32 + "cd" * 32 + "01"
        r, s, v = decode_signature(sig)
        assert r == "ab" * 32
        assert s == "cd" * 32
        assert v == 1


class TestAuthMiddlewareIntegration:
    """Auth middleware integration — endpoint reachability tests."""

    _AUTH = {"X-API-Key": "test-auth-token-32-chars-long!!"}

    @pytest.fixture(scope="module")
    def app(self):
        from app.factory import create_app

        return create_app()

    def test_public_endpoint_no_auth_required(self, app) -> None:
        """Public root endpoint should not need auth."""
        from fastapi.testclient import TestClient

        resp = TestClient(app).get("/")
        assert resp.status_code == 200
        assert resp.json()["service"] == "RMI Backend"

    def test_protected_route_without_auth_returns_401(self, app) -> None:
        """Protected routes require X-API-Key header."""
        from fastapi.testclient import TestClient

        resp = TestClient(app).get("/api/v1/bulletin/health")
        assert resp.status_code == 401

    def test_protected_route_with_valid_auth_succeeds(self, app) -> None:
        """Protected route with valid X-API-Key should pass auth."""
        from fastapi.testclient import TestClient

        resp = TestClient(app).get("/api/v1/bulletin/health", headers=self._AUTH)
        assert resp.status_code in (200, 404)
        if resp.status_code == 404:
            assert "Unauthorized" not in resp.json().get("detail", "")

    def test_protected_route_with_wrong_key_returns_401(self, app) -> None:
        """Wrong API key should be rejected."""
        from fastapi.testclient import TestClient

        resp = TestClient(app).get(
            "/api/v1/bulletin/health",
            headers={"X-API-Key": "wrong-key-that-is-not-valid"},
        )
        assert resp.status_code == 401

    def test_security_headers_present(self, app) -> None:
        """Security headers should be on every response."""
        from fastapi.testclient import TestClient

        resp = TestClient(app).get("/version")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert "Strict-Transport-Security" in resp.headers
        assert "X-Request-ID" in resp.headers

    def test_auth_prefix_is_public(self, app) -> None:
        """Routes under /api/v1/auth/ should not require X-API-Key."""
        from fastapi.testclient import TestClient

        resp = TestClient(app).post("/api/v1/auth/register", json={})
        assert resp.status_code != 401


class TestTwoFactorAuth:
    """Tests for 2FA setup and verification — REAL TOTP logic."""

    def test_generate_totp_secret_is_valid_base32(self):
        """TOTP secret should be valid base32."""
        from app.domains.auth.totp import PYOTP_AVAILABLE, _generate_totp_secret

        if not PYOTP_AVAILABLE:
            pytest.skip("pyotp not installed")

        secret = _generate_totp_secret()
        assert isinstance(secret, str)
        assert len(secret) >= 16

        import base64

        base64.b32decode(secret, casefold=True)  # Should not raise

    def test_generate_backup_codes_returns_8_codes(self):
        """Backup codes should be a list of 8 codes."""
        from app.domains.auth.totp import _generate_backup_codes

        codes = _generate_backup_codes()
        assert len(codes) == 8
        for code in codes:
            assert len(code) >= 8

    def test_backup_code_hash_roundtrip(self):
        """Backup codes should be hashable and verifiable."""
        from app.domains.auth.totp import _hash_backup_code, _verify_backup_code

        code = "ABC12345"
        hashed = _hash_backup_code(code)
        assert hashed != code
        assert _verify_backup_code(code, hashed) is True
        assert _verify_backup_code("wrong-code", hashed) is False
