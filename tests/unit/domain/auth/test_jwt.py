"""Tests for JWT token creation and verification.

Tests _create_jwt and _verify_jwt from the auth module.
Mocks JWT_SECRET and uses datetime patching instead of freezegun.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import jwt as pyjwt


class TestCreateJWT:
    """Tests for _create_jwt function."""

    def test_creates_valid_jwt_token(self):
        """_create_jwt should return a JWT string with 3 parts."""
        from app.domains.auth.__init__ import _create_jwt

        with patch("app.domains.auth.jwt.JWT_SECRET", "test-secret-key-32-chars-long-enough!!"):
            token = _create_jwt(user_id="user_123", email="test@example.com", tier="FREE", role="USER")
            assert isinstance(token, str)
            parts = token.split(".")
            assert len(parts) == 3

    def test_jwt_contains_claims(self):
        """JWT should contain sub, email, tier, role in payload."""
        from app.domains.auth.__init__ import _create_jwt

        secret = "test-secret-key-32-chars-long-enough!!"
        with patch("app.domains.auth.jwt.JWT_SECRET", secret):
            token = _create_jwt(user_id="user_456", email="alice@example.com", tier="PRO", role="ADMIN")
            payload = pyjwt.decode(token, secret, algorithms=["HS256"])
            assert payload["sub"] == "user_456"
            assert payload["email"] == "alice@example.com"
            assert payload["tier"] == "PRO"
            assert payload["role"] == "ADMIN"

    def test_jwt_includes_wallet_when_provided(self):
        """When wallet is provided, it should be in the JWT payload."""
        from app.domains.auth.__init__ import _create_jwt

        secret = "test-secret-key-32-chars-long-enough!!"
        with patch("app.domains.auth.jwt.JWT_SECRET", secret):
            token = _create_jwt(
                user_id="user_789",
                email="bob@example.com",
                tier="ELITE",
                role="USER",
                wallet="0xabc123",
            )
            payload = pyjwt.decode(token, secret, algorithms=["HS256"])
            assert payload["wallet"] == "0xabc123"

    def test_jwt_has_iat_and_exp(self):
        """JWT should have iat (issued at) and exp (expiry) claims."""
        from app.domains.auth.__init__ import _create_jwt

        secret = "test-secret-key-32-chars-long-enough!!"
        with (
            patch("app.domains.auth.jwt.JWT_SECRET", secret),
            patch("app.domains.auth.jwt.datetime") as mock_dt,
        ):
            fixed_now = datetime(2026, 7, 14, 12, 0, 0)
            mock_dt.now.return_value = fixed_now
            # We also need to mock timedelta
            token = _create_jwt(user_id="u1", email="u@example.com")
            payload = pyjwt.decode(token, secret, algorithms=["HS256"])
            assert "iat" in payload
            assert "exp" in payload


class TestVerifyJWT:
    """Tests for _verify_jwt function."""

    def test_verify_valid_token(self):
        """_verify_jwt should return payload for a valid token."""
        from app.domains.auth.__init__ import _create_jwt, _verify_jwt

        secret = "test-secret-key-32-chars-long-enough!!"
        with patch("app.domains.auth.jwt.JWT_SECRET", secret):
            token = _create_jwt(user_id="user_111", email="verify@example.com")
            payload = _verify_jwt(token)
            assert payload is not None
            assert payload["id"] == "user_111"
            assert payload["email"] == "verify@example.com"

    def test_verify_invalid_token_returns_none(self):
        """_verify_jwt should return None for an invalid token."""
        from app.domains.auth.__init__ import _verify_jwt

        with patch("app.domains.auth.jwt.JWT_SECRET", "test-secret-key-32-chars-long-enough!!"):
            result = _verify_jwt("invalid.token.here")
            assert result is None

    def test_verify_expired_token_returns_none(self):
        """_verify_jwt should return None for an expired token."""
        from app.domains.auth.__init__ import _verify_jwt

        secret = "test-secret-key-32-chars-long-enough!!"
        with patch("app.domains.auth.jwt.JWT_SECRET", secret):
            expired_token = pyjwt.encode(
                {
                    "sub": "user_expired",
                    "email": "expired@example.com",
                    "exp": datetime(2020, 1, 1),
                    "iat": datetime(2020, 1, 1),
                },
                secret,
                algorithm="HS256",
            )
            result = _verify_jwt(expired_token)
            assert result is None

    def test_verify_token_with_wrong_secret_fails(self):
        """Token signed with different secret should not verify."""
        from app.domains.auth.__init__ import _create_jwt, _verify_jwt

        with patch("app.domains.auth.jwt.JWT_SECRET", "correct-secret-32-chars-long!!!!"):
            token = _create_jwt(user_id="u1", email="u@example.com")

        with patch("app.domains.auth.jwt.JWT_SECRET", "different-secret-32-chars-long!!"):
            result = _verify_jwt(token)
            assert result is None

    def test_verify_empty_token(self):
        """Empty token should return None."""
        from app.domains.auth.__init__ import _verify_jwt

        with patch("app.domains.auth.jwt.JWT_SECRET", "test-secret-key-32-chars-long-enough!!"):
            assert _verify_jwt("") is None


class TestJWTDeriveUserID:
    """Tests for _derive_user_id helper."""

    def test_derive_user_id_returns_string(self):
        """_derive_user_id should return a non-empty string."""
        from app.domains.auth.__init__ import _derive_user_id

        result = _derive_user_id("test@example.com")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_derive_user_id_is_deterministic(self):
        """Same email should produce the same user ID."""
        from app.domains.auth.__init__ import _derive_user_id

        id1 = _derive_user_id("same@example.com")
        id2 = _derive_user_id("same@example.com")
        assert id1 == id2

    def test_derive_user_id_differs_for_diff_inputs(self):
        """Different emails should produce different user IDs."""
        from app.domains.auth.__init__ import _derive_user_id

        id1 = _derive_user_id("alice@example.com")
        id2 = _derive_user_id("bob@example.com")
        assert id1 != id2
