"""Shared test configuration for security tests.

Sets required env vars before any app imports to ensure the factory
boots cleanly. Provides the `client` fixture (fastapi.TestClient).
"""

from __future__ import annotations

import os

# ── Test auth credentials (must be 32+ chars for JWT secret validation) ──
TEST_RMI_AUTH_TOKEN: str = "test-auth-token-32-chars-long!!"
TEST_INTERNAL_KEY: str = "test-internal-key-32-chars-long!!!"
TEST_JWT_SECRET: str = "test-jwt-secret-key-thats-32-chars-long!"

# ── Required env vars for app factory boot ──────────────────────────────
os.environ.setdefault("RMI_AUTH_TOKEN", TEST_RMI_AUTH_TOKEN)
os.environ.setdefault("RMI_INTERNAL_KEY", TEST_INTERNAL_KEY)
os.environ.setdefault("JWT_SECRET", TEST_JWT_SECRET)
os.environ.setdefault("RMI_JWT_SECRET", TEST_JWT_SECRET)
os.environ.setdefault("RMI_JWT_ALGORITHM", "HS256")
os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("REDIS_PASSWORD", "")
os.environ.setdefault("REDIS_HOST", "localhost")
os.environ.setdefault("REDIS_PORT", "6379")
os.environ.setdefault("CORS_ORIGINS", '["*"]')

import pytest


@pytest.fixture(scope="session")
def app():
    """Build the FastAPI app once per test session."""
    from app.factory import create_app

    return create_app()


@pytest.fixture
def client(app):
    """Provide a FastAPI TestClient for each test.

    Each test gets a fresh TestClient (but the same app instance).
    This ensures test isolation.
    """
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c
