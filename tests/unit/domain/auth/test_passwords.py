"""Tests for app/domains/auth/passwords.py - bcrypt password hashing.

Tests hash_password and verify_password functions that wrap bcrypt.
"""

from __future__ import annotations


class TestHashPassword:
    """Tests for hash_password function."""

    def test_hash_password_returns_string(self):
        """hash_password should return a non-empty string."""
        from app.domains.auth.passwords import hash_password

        hashed = hash_password("my_secure_password")
        assert isinstance(hashed, str)
        assert len(hashed) > 0

    def test_hash_password_is_deterministically_different(self):
        """Each hash should be unique (bcrypt salts)."""
        from app.domains.auth.passwords import hash_password

        h1 = hash_password("same_password")
        h2 = hash_password("same_password")
        assert h1 != h2

    def test_hash_password_starts_with_bcrypt_prefix(self):
        """Bcrypt hashes start with $2b$ or $2a$."""
        from app.domains.auth.passwords import hash_password

        hashed = hash_password("test_password")
        assert hashed.startswith(("$2", "$2b", "$2a"))

    def test_hash_password_handles_empty_string(self):
        """Empty passwords should still hash successfully."""
        from app.domains.auth.passwords import hash_password

        hashed = hash_password("")
        assert isinstance(hashed, str)
        assert len(hashed) > 0

    def test_hash_password_handles_unicode(self):
        """Unicode passwords should hash successfully."""
        from app.domains.auth.passwords import hash_password

        hashed = hash_password("héllo_wörld_密码")
        assert isinstance(hashed, str)
        assert len(hashed) > 0


class TestVerifyPassword:
    """Tests for verify_password function."""

    def test_verify_correct_password(self):
        """verify_password should return True for the correct password."""
        from app.domains.auth.passwords import hash_password, verify_password

        password = "my_correct_password"
        hashed = hash_password(password)

        assert verify_password(password, hashed) is True

    def test_verify_incorrect_password(self):
        """verify_password should return False for an incorrect password."""
        from app.domains.auth.passwords import hash_password, verify_password

        hashed = hash_password("real_password")
        assert verify_password("wrong_password", hashed) is False

    def test_verify_empty_password(self):
        """Empty password should not verify against a real hash."""
        from app.domains.auth.passwords import hash_password, verify_password

        hashed = hash_password("real_password")
        assert verify_password("", hashed) is False

    def test_verify_against_invalid_hash(self):
        """Invalid hash should return False without raising."""
        from app.domains.auth.passwords import verify_password

        assert verify_password("any_password", "not_a_valid_hash") is False

    def test_verify_against_empty_hash(self):
        """Empty hash should return False."""
        from app.domains.auth.passwords import verify_password

        assert verify_password("any_password", "") is False

    def test_verify_unicode_password(self):
        """Unicode passwords should verify correctly."""
        from app.domains.auth.passwords import hash_password, verify_password

        pwd = "héllo_wörld_密码"
        hashed = hash_password(pwd)
        assert verify_password(pwd, hashed) is True
        assert verify_password(pwd + "_wrong", hashed) is False

    def test_verify_same_password_different_hash(self):
        """A password hashed twice should verify against either hash."""
        from app.domains.auth.passwords import hash_password, verify_password

        pwd = "test_password_123"
        h1 = hash_password(pwd)
        h2 = hash_password(pwd)
        assert h1 != h2
        assert verify_password(pwd, h1) is True
        assert verify_password(pwd, h2) is True
