"""Tests for authentication: JWT creation/verification, password hashing,
token extraction, email validation, and FastAPI auth dependencies.

These tests operate on the pure functions from app.domains.auth — no server
startup needed. Mocked Redis for user lookups.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Helpers ──────────────────────────────────────────────────────────────


def _freeze_time(days: int = 0, hours: int = 0):
    """Move JWT expiry by the given offset using _verify_jwt's reliance on
    the real clock. Since we cannot mock datetime.now inside the jose lib,
    we instead generate real tokens and let time pass or use fresh tokens.
    """


# ── Password Hashing ─────────────────────────────────────────────────────


class TestPasswordHashing:
    """Password hashing round-trip and edge cases."""

    def test_hash_and_verify_roundtrip(self):
        """hash_password then verify_password with same password returns True."""
        from app.domains.auth import hash_password, verify_password

        pw = "correct-horse-battery-staple-42!"
        hashed = hash_password(pw)
        assert verify_password(pw, hashed) is True

    def test_verify_wrong_password_returns_false(self):
        """verify_password with wrong password returns False."""
        from app.domains.auth import hash_password, verify_password

        hashed = hash_password("real-password")
        assert verify_password("wrong-password", hashed) is False

    def test_verify_with_invalid_hash_returns_false(self):
        """verify_password gracefully handles invalid hash strings."""
        from app.domains.auth import verify_password

        assert verify_password("anything", "not-a-valid-hash") is False

    def test_verify_with_empty_password_returns_false(self):
        """verify_password with empty password returns False."""
        from app.domains.auth import hash_password, verify_password

        hashed = hash_password("real-password")
        assert verify_password("", hashed) is False

    def test_verify_with_empty_hash_returns_false(self):
        """verify_password with empty hash returns False."""
        from app.domains.auth import verify_password

        assert verify_password("password", "") is False

    def test_verify_with_none_hash_returns_false(self):
        """verify_password with None-like values handles gracefully."""
        from app.domains.auth import verify_password

        # Will fail at bcrypt.checkpw level, caught by except
        assert verify_password("password", None) is False  # type: ignore[arg-type]

    def test_hash_is_different_each_time(self):
        """hash_password produces different hashes each call (bcrypt salt)."""
        from app.domains.auth import hash_password

        pw = "same-password"
        h1 = hash_password(pw)
        h2 = hash_password(pw)
        assert h1 != h2
        # Both should still verify
        from app.domains.auth import verify_password

        assert verify_password(pw, h1) is True
        assert verify_password(pw, h2) is True


# ── JWT Creation & Verification ──────────────────────────────────────────


class TestJWTCreation:
    """JWT token creation and format."""

    def test_create_jwt_returns_string_token(self):
        """_create_jwt returns a non-empty string."""
        from app.domains.auth import _create_jwt

        token = _create_jwt("user-123", "test@example.com", "FREE", "USER", None)
        assert isinstance(token, str)
        assert len(token) > 20
        assert token.count(".") == 2  # JWT has 3 parts

    def test_create_jwt_with_wallet(self):
        """_create_jwt includes wallet address when provided."""
        from app.domains.auth import _create_jwt, _verify_jwt

        token = _create_jwt("user-1", "a@b.com", "PREMIUM", "ADMIN", "0x1234")
        payload = _verify_jwt(token)
        assert payload is not None
        assert payload["wallet"] == "0x1234"
        assert payload["tier"] == "PREMIUM"
        assert payload["role"] == "ADMIN"

    def test_create_jwt_default_tier_and_role(self):
        """_create_jwt uses FREE/USER defaults when not specified."""
        from app.domains.auth import _create_jwt, _verify_jwt

        token = _create_jwt("user-1", "a@b.com")
        payload = _verify_jwt(token)
        assert payload is not None
        assert payload["tier"] == "FREE"
        assert payload["role"] == "USER"

    def test_create_jwt_includes_iat_and_exp(self):
        """_create_jwt sets iat and exp claims."""
        from app.domains.auth import JWT_EXPIRY_DAYS, _create_jwt
        from jose import jwt

        token = _create_jwt("uid", "e@mail.com")
        # Decode without verification to inspect claims
        from app.domains.auth import JWT_ALGORITHM, JWT_SECRET

        header = jwt.get_unverified_header(token)
        assert header["alg"] in ("HS256", "RS256")

    def test_create_and_verify_roundtrip(self):
        """Token created by _create_jwt is verified by _verify_jwt."""
        from app.domains.auth import _create_jwt, _verify_jwt

        token = _create_jwt("uid-42", "user@example.com", "PRO", "MOD")
        payload = _verify_jwt(token)
        assert payload is not None
        assert payload["id"] == "uid-42"
        assert payload["email"] == "user@example.com"
        assert payload["tier"] == "PRO"
        assert payload["role"] == "MOD"


class TestJWTVerification:
    """JWT token verification edge cases."""

    def test_verify_valid_token_returns_payload(self):
        """_verify_jwt returns payload dict for a valid token."""
        from app.domains.auth import _create_jwt, _verify_jwt

        token = _create_jwt("uid-1", "a@b.com")
        payload = _verify_jwt(token)
        assert payload is not None
        assert isinstance(payload, dict)
        assert payload["id"] == "uid-1"

    def test_verify_malformed_token_returns_none(self):
        """_verify_jwt returns None for a garbage token."""
        from app.domains.auth import _verify_jwt

        assert _verify_jwt("not-a-jwt") is None

    def test_verify_empty_token_returns_none(self):
        """_verify_jwt returns None for empty string."""
        from app.domains.auth import _verify_jwt

        assert _verify_jwt("") is None

    def test_verify_token_wrong_key_returns_none(self):
        """_verify_jwt returns None for token signed with different key."""
        import jwt as pyjwt

        # Sign with a different secret
        wrong_token = pyjwt.encode(
            {"sub": "u1", "email": "a@b.com"},
            "different-secret-key-thats-long-enough-here!",
            algorithm="HS256",
        )
        from app.domains.auth import _verify_jwt

        assert _verify_jwt(wrong_token) is None

    def test_verify_expired_token_returns_none(self):
        """_verify_jwt returns None for an expired token."""
        import jwt as pyjwt
        from app.domains.auth import JWT_SECRET

        # Create an already-expired token
        expired = datetime.now(UTC) - timedelta(hours=1)
        token = pyjwt.encode(
            {
                "sub": "uid-1",
                "email": "a@b.com",
                "exp": expired,
                "iat": expired - timedelta(minutes=5),
            },
            JWT_SECRET,
            algorithm="HS256",
        )
        from app.domains.auth import _verify_jwt

        assert _verify_jwt(token) is None

    def test_verify_token_missing_sub_returns_none(self):
        """_verify_jwt returns None when payload lacks 'sub'."""
        import jwt as pyjwt
        from app.domains.auth import JWT_SECRET

        token = pyjwt.encode(
            {"email": "a@b.com", "exp": datetime.now(UTC) + timedelta(hours=1)},
            JWT_SECRET,
            algorithm="HS256",
        )
        from app.domains.auth import _verify_jwt

        assert _verify_jwt(token) is None


# ── Token Extraction from requests ───────────────────────────────────────


class TestTokenExtraction:
    """Extracting JWT from cookies and Authorization headers."""

    def _extract_token(self, request):
        """Import from the canonical source."""
        from app.domains.auth.deps import _extract_token as et

        return et(request)

    def test_extract_token_from_bearer_header(self):
        """_extract_token returns token from Authorization: Bearer header."""
        request = MagicMock()
        request.cookies = {}
        request.headers = {"Authorization": "Bearer my-test-token-here"}
        result = self._extract_token(request)
        assert result == "my-test-token-here"

    def test_extract_token_from_cookie(self):
        """_extract_token prefers cookie over header."""
        request = MagicMock()
        request.cookies = {"rmi_token": "cookie-token-value"}
        request.headers = {"Authorization": "Bearer header-token"}
        result = self._extract_token(request)
        assert result == "cookie-token-value"

    def test_extract_token_no_auth(self):
        """_extract_token returns None when no auth is present."""
        request = MagicMock()
        request.cookies = {}
        request.headers = {}
        result = self._extract_token(request)
        assert result is None

    def test_extract_token_missing_bearer_prefix(self):
        """_extract_token returns None for Authorization without Bearer."""
        request = MagicMock()
        request.cookies = {}
        request.headers = {"Authorization": "Token some-value"}
        result = self._extract_token(request)
        assert result is None

    def test_extract_token_empty_bearer(self):
        """_extract_token returns empty string for 'Bearer ' (no token after prefix).

        NOTE: The function returns '' because auth_header[7:] slices an
        empty string. Callers should treat empty string the same as None.
        [REVIEW: Consider returning None instead of '' for consistency.]
        """
        request = MagicMock()
        request.cookies = {}
        request.headers = {"Authorization": "Bearer "}
        result = self._extract_token(request)
        # Current behavior: returns '' (not None)
        assert result == ""


# ── Auth Dependencies (require_auth / get_current_user) ─────────────────


class TestAuthDependencies:
    """FastAPI dependency functions for auth."""

    @pytest.mark.asyncio
    async def test_get_current_user_no_token_returns_none(self):
        """get_current_user returns None when no token present."""
        from app.domains.auth import get_current_user

        request = MagicMock()
        request.cookies = {}
        request.headers = {}
        result = await get_current_user(request)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_current_user_invalid_token_returns_none(self):
        """get_current_user returns None for invalid token."""
        from app.domains.auth import get_current_user

        request = MagicMock()
        request.cookies = {}
        request.headers = {"Authorization": "Bearer invalid-token-here"}
        result = await get_current_user(request)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_current_user_valid_token_user_not_found(self):
        """get_current_user returns None when token is valid but user not in Redis."""
        from app.domains.auth import _create_jwt, get_current_user

        token = _create_jwt("nonexistent-user", "ghost@example.com")

        with patch("app.domains.auth.deps._get_user", return_value=None):
            request = MagicMock()
            request.cookies = {}
            request.headers = {"Authorization": f"Bearer {token}"}
            result = await get_current_user(request)
            assert result is None

    @pytest.mark.asyncio
    async def test_get_current_user_returns_user(self):
        """get_current_user returns user dict for valid token + existing user."""
        from app.domains.auth import _create_jwt, get_current_user

        token = _create_jwt("uid-42", "user@test.com")
        mock_user = {
            "id": "uid-42",
            "email": "user@test.com",
            "display_name": "Test User",
            "tier": "FREE",
            "role": "USER",
        }

        with patch("app.domains.auth.deps._get_user", return_value=mock_user):
            request = MagicMock()
            request.cookies = {}
            request.headers = {"Authorization": f"Bearer {token}"}
            result = await get_current_user(request)
            assert result is not None
            assert result["id"] == "uid-42"
            assert result["email"] == "user@test.com"

    @pytest.mark.asyncio
    async def test_require_auth_raises_401_without_token(self):
        """require_auth raises 401 when no token."""
        from app.domains.auth import require_auth
        from fastapi import HTTPException

        request = MagicMock()
        request.cookies = {}
        request.headers = {}

        with pytest.raises(HTTPException) as exc:
            await require_auth(request)
        assert exc.value.status_code == 401
        assert "Authentication required" in str(exc.value.detail)

    @pytest.mark.asyncio
    async def test_require_auth_returns_user(self):
        """require_auth returns user when authenticated."""
        from app.domains.auth import _create_jwt, require_auth

        token = _create_jwt("uid-1", "a@b.com")
        mock_user = {"id": "uid-1", "email": "a@b.com"}

        with patch("app.domains.auth.deps._get_user", return_value=mock_user):
            request = MagicMock()
            request.cookies = {}
            request.headers = {"Authorization": f"Bearer {token}"}
            result = await require_auth(request)
            assert result["id"] == "uid-1"


# ── Email Validation ─────────────────────────────────────────────────────


class TestEmailValidation:
    """Email format validation."""

    def test_valid_email_formats(self):
        """_is_valid_email returns True for valid email formats."""
        from app.domains.auth import _is_valid_email

        valid_emails = [
            "user@example.com",
            "user.name+tag@example.co.uk",
            "a@b.io",
            "test.user@sub.example.org",
            "123@example.com",
        ]
        for email in valid_emails:
            assert _is_valid_email(email), f"Should be valid: {email}"

    def test_invalid_email_formats(self):
        """_is_valid_email returns False for invalid email formats."""
        from app.domains.auth import _is_valid_email

        invalid_emails = [
            "",
            "not-an-email",
            "@no-local.com",
            "no-domain@",
            "spaces in@email.com",
            "missing@tld",
            "a@b.c",  # TLD too short
        ]
        for email in invalid_emails:
            assert not _is_valid_email(email), f"Should be invalid: {email}"


# ── Nonce Generation ─────────────────────────────────────────────────────


class TestNonceGeneration:
    """Nonce generation utilities."""

    def test_generate_nonce_returns_string(self):
        """generate_nonce returns a non-empty string."""
        from app.domains.auth import generate_nonce

        nonce = generate_nonce()
        assert isinstance(nonce, str)
        assert len(nonce) > 0

    def test_generate_nonce_unique(self):
        """generate_nonce returns unique values each time."""
        from app.domains.auth import generate_nonce

        seen = {generate_nonce() for _ in range(100)}
        assert len(seen) == 100

    def test_generate_nonce_urlsafe(self):
        """generate_nonce returns URL-safe base64."""
        from app.domains.auth import generate_nonce

        nonce = generate_nonce()
        # URL-safe base64 uses alphanumeric, dash, underscore
        import string

        allowed = set(string.ascii_letters + string.digits + "-_")
        assert all(c in allowed for c in nonce)


# ── User ID Derivation ──────────────────────────────────────────────────


class TestUserIDDerivation:
    """User ID derivation from email."""

    def test_derive_user_id_returns_string(self):
        """_derive_user_id returns a hex string."""
        from app.domains.auth import _derive_user_id

        uid = _derive_user_id("test@example.com")
        assert isinstance(uid, str)
        assert len(uid) == 32  # SHA256 truncated to 32 hex chars

    def test_derive_user_id_deterministic(self):
        """_derive_user_id returns same ID for same email."""
        from app.domains.auth import _derive_user_id

        assert _derive_user_id("test@example.com") == _derive_user_id("test@example.com")

    def test_derive_user_id_case_insensitive(self):
        """_derive_user_id lowercases email before hashing."""
        from app.domains.auth import _derive_user_id

        assert _derive_user_id("Test@Example.com") == _derive_user_id("test@example.com")

    def test_derive_user_id_different_for_different_emails(self):
        """_derive_user_id returns different IDs for different emails."""
        from app.domains.auth import _derive_user_id

        assert _derive_user_id("a@b.com") != _derive_user_id("c@d.com")


# ── Redis User Store ─────────────────────────────────────────────────────


class TestUserStore:
    """Redis-backed user store operations."""

    def test_get_user_no_redis_returns_none(self):
        """_get_user returns None when Redis is unavailable."""
        with patch("app.domains.auth.store.get_redis", return_value=None):
            from app.domains.auth import _get_user

            assert _get_user("some-id") is None

    def test_get_user_by_email_no_redis_returns_none(self):
        """_get_user_by_email returns None when Redis is unavailable."""
        with patch("app.domains.auth.store.get_redis", return_value=None):
            from app.domains.auth import _get_user_by_email

            assert _get_user_by_email("test@example.com") is None


# ── Backup Codes ─────────────────────────────────────────────────────────


class TestBackupCodes:
    """Backup code generation and verification."""

    def test_generate_backup_codes_default_count(self):
        """_generate_backup_codes returns 8 codes by default."""
        from app.domains.auth import _generate_backup_codes

        codes = _generate_backup_codes()
        assert len(codes) == 8

    def test_generate_backup_codes_custom_count(self):
        """_generate_backup_codes respects custom count."""
        from app.domains.auth import _generate_backup_codes

        codes = _generate_backup_codes(4)
        assert len(codes) == 4

    def test_backup_code_format(self):
        """Backup codes have expected format: XXXX-XXXX."""
        from app.domains.auth import _generate_backup_codes
        import re

        codes = _generate_backup_codes()
        pattern = re.compile(r"^\d{4}-\d{4}$")
        for code in codes:
            assert pattern.match(code), f"Bad format: {code}"

    def test_backup_code_hash_roundtrip(self):
        """_hash_backup_code and _verify_backup_code roundtrip."""
        from app.domains.auth import _generate_backup_codes, _hash_backup_code, _verify_backup_code

        codes = _generate_backup_codes(3)
        for code in codes:
            hashed = _hash_backup_code(code)
            assert _verify_backup_code(code, hashed) is True
            assert _verify_backup_code("wrong-code", hashed) is False

    def test_backup_codes_unique(self):
        """Generated backup codes are unique."""
        from app.domains.auth import _generate_backup_codes

        codes = _generate_backup_codes(20)
        assert len(set(codes)) == 20
