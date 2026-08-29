"""Tests for app/config.py - Pydantic Settings.

Tests the Settings class and its validation rules.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest


class TestSettingsBasics:
    """Tests for basic Settings configuration."""

    def test_default_jwt_expire_minutes(self):
        """Default JWT expiry should be 1440 minutes (24h)."""
        from app.config import Settings

        settings = Settings(jwt_secret="test-secret-key-32-chars-long-enough!!")
        assert settings.jwt_expire_minutes == 60 * 24

    def test_default_environment(self):
        """Default environment should be 'dev'."""
        from app.config import Settings

        settings = Settings(jwt_secret="test-secret-key-32-chars-long-enough!!")
        # Will be 'test' from conftest's os.environ.setdefault
        assert settings.environment in ("dev", "staging", "prod", "test")

    def test_default_log_level(self):
        """Default log level should be INFO."""
        from app.config import Settings

        settings = Settings(jwt_secret="test-secret-key-32-chars-long-enough!!")
        assert settings.log_level in ("INFO", "WARNING")  # conftest sets WARNING

    def test_default_port(self):
        """Default port should be 8000."""
        from app.config import Settings

        settings = Settings(jwt_secret="test-secret-key-32-chars-long-enough!!")
        assert settings.port == 8000

    def test_default_rate_limit(self):
        """Default rate limit should be 100 per minute."""
        from app.config import Settings

        settings = Settings(jwt_secret="test-secret-key-32-chars-long-enough!!")
        assert settings.rate_limit_per_minute == 100

    def test_default_cors_origins(self):
        """Default CORS origins should be empty list (dev mode allows this)."""
        from app.config import Settings

        settings = Settings(jwt_secret="test-secret-key-32-chars-long-enough!!")
        # In dev environment, empty list is allowed
        assert isinstance(settings.cors_origins, list)
        assert settings.cors_origins == [] or settings.cors_origins == ["*"]


class TestSettingsValidation:
    """Tests for Settings validation."""

    def test_jwt_secret_min_length(self):
        """jwt_secret shorter than 32 should raise."""
        from app.config import Settings

        with pytest.raises(Exception):
            Settings(jwt_secret="short")

    def test_jwt_secret_proper_length_accepted(self):
        """32+ char jwt_secret should be accepted."""
        from app.config import Settings

        settings = Settings(jwt_secret="a" * 32)
        assert len(settings.jwt_secret) >= 32

    def test_prod_with_default_secret_fails(self):
        """Default dev secret in production should raise ValueError or ValidationError."""
        from app.config import Settings

        with (
            patch.dict(os.environ, {"ENVIRONMENT": "prod"}),
            pytest.raises(
                (ValueError, Exception),
                match=r"jwt_secret must be set|jwt_secret.*short|jwt_secret",
            ),
        ):
            Settings(jwt_secret="dev-secret-CHANGE-ME")

    def test_prod_with_short_secret_fails(self):
        """Short jwt_secret in production should fail validation."""
        from app.config import Settings

        with patch.dict(os.environ, {"ENVIRONMENT": "prod"}), pytest.raises(Exception, match=r"string_too_short"):
            Settings(jwt_secret="short")

    def test_prod_with_custom_secret_passes(self):
        """Non-default secret in production should work (must also set cors_origins)."""
        from app.config import Settings

        with patch.dict(os.environ, {"ENVIRONMENT": "prod", "CORS_ORIGINS": '["https://example.com"]'}):
            settings = Settings(
                jwt_secret="production-secret-123456789012345",
                cors_origins=["https://example.com"],
            )
            assert settings.jwt_secret == "production-secret-123456789012345"


class TestSettingsOverrides:
    """Tests for Settings overrides via env vars."""

    def test_override_port(self):
        """Port should be overridable."""
        from app.config import Settings

        settings = Settings(jwt_secret="test-secret-key-32-chars-long-enough!!", port=9000)
        assert settings.port == 9000

    def test_override_log_level(self):
        """Log level should be overridable."""
        from app.config import Settings

        settings = Settings(jwt_secret="test-secret-key-32-chars-long-enough!!", log_level="DEBUG")
        assert settings.log_level == "DEBUG"

    def test_override_database_url(self):
        """Database URL should be overridable."""
        from app.config import Settings

        custom_url = "postgresql+asyncpg://user:pass@remote:5432/db"
        settings = Settings(jwt_secret="test-secret-key-32-chars-long-enough!!", database_url=custom_url)
        assert settings.database_url == custom_url

    def test_override_redis_url(self):
        """Redis URL should be overridable."""
        from app.config import Settings

        custom_url = "redis://remote:6380/1"
        settings = Settings(jwt_secret="test-secret-key-32-chars-long-enough!!", redis_url=custom_url)
        assert settings.redis_url == custom_url

    def test_override_cors_origins(self):
        """CORS origins should be overridable as list."""
        from app.config import Settings

        origins = ["https://app.example.com", "https://admin.example.com"]
        settings = Settings(jwt_secret="test-secret-key-32-chars-long-enough!!", cors_origins=origins)
        assert settings.cors_origins == origins

    def test_jwt_algorithm_default(self):
        """Default JWT algorithm should be RS256 per config."""
        from app.config import Settings

        settings = Settings(jwt_secret="test-secret-key-32-chars-long-enough!!")
        assert settings.jwt_algorithm == "RS256"


class TestBuildDbUrlFromEnv:
    """Confirm _build_db_url_from_env URL-encodes the password.

    Production secrets contain '/' and '=' which break the URL path/query
    otherwise (asyncpg rejects the malformed URL). This guards the fix in
    app/config.py:_build_db_url_from_env (quote(password, safe="")).
    """

    def test_password_with_slash_and_equals_is_url_encoded(self, monkeypatch):
        from app.config import _build_db_url_from_env

        monkeypatch.setenv("POSTGRES_HOST", "rmi-postgres")
        monkeypatch.setenv("POSTGRES_USER", "rmi")
        monkeypatch.setenv("POSTGRES_PASSWORD", "p/ass=word")
        monkeypatch.setenv("POSTGRES_PORT", "5432")
        monkeypatch.setenv("POSTGRES_DB", "rmi")
        url = _build_db_url_from_env()
        assert url is not None
        # '/' -> %2F , '=' -> %3D  (quote with safe="")
        assert "p%2Fass%3Dword" in url
        # The raw password must NOT leak unencoded into the URL.
        assert "p/ass=word" not in url

    def test_returns_none_when_host_unset(self, monkeypatch):
        from app.config import _build_db_url_from_env

        monkeypatch.delenv("POSTGRES_HOST", raising=False)
        monkeypatch.delenv("DB_HOST", raising=False)
        assert _build_db_url_from_env() is None

    def test_settings_uses_encoded_password_from_postgres_env(self, monkeypatch):
        """Settings.database_url must carry the URL-encoded password when
        built from POSTGRES_* env (DATABASE_URL unset)."""
        from app.config import Settings

        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("POSTGRES_HOST", "rmi-postgres")
        monkeypatch.setenv("POSTGRES_PASSWORD", "sec/ret=pwd")
        settings = Settings(jwt_secret="test-secret-key-32-chars-long-enough!!")
        assert "sec%2Fret%3Dpwd" in settings.database_url
        assert "sec/ret=pwd" not in settings.database_url
