"""Shared fixtures for integration tests.

Phase 1 stub — Phase 3 (AUDIT-2026-Q3) adds testcontainers fixtures for
postgres, redis, qdrant.

Sets required env vars before any app imports to ensure the factory
boots cleanly in CI/test environments.
"""

import os

# ── Required env vars for app factory boot ────────────────────────────
os.environ.setdefault("RMI_AUTH_TOKEN", "test-auth-token-32-chars-long!!")
os.environ.setdefault("RMI_INTERNAL_KEY", "test-internal-key-32-chars-long!!!")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-key-thats-32-chars-long!")
os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest


@pytest.fixture(scope="session")
def event_loop_policy():
    """Default asyncio policy. Phase 3 will replace with testcontainers."""
    import asyncio

    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture(scope="module")
def app():
    """Build the FastAPI app once per module with test settings."""
    from app.factory import create_app

    return create_app()
