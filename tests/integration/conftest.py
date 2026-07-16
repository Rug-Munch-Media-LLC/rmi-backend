"""Shared fixtures for integration tests.

Phase 1 stub — Phase 3 (AUDIT-2026-Q3) adds testcontainers fixtures for
postgres, redis, qdrant.
"""

import pytest


@pytest.fixture(scope="session")
def event_loop_policy():
    """Default asyncio policy. Phase 3 will replace with testcontainers."""
    import asyncio

    return asyncio.DefaultEventLoopPolicy()
