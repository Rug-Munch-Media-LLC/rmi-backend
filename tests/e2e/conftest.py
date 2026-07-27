"""End-to-End test configuration and fixtures.

E2E tests connect to a RUNNING instance of the rmi-backend API.
They assume the service is already deployed (CI/CD pipeline, Docker
Compose stack, or manual local server).

Fixtures:
  - e2e_base_url:  base URL of the running API (default: http://localhost:8000)
  - e2e_client:    httpx.AsyncClient pointed at the live API
  - e2e_auth:      auth headers for the live API's test user

If the service is not reachable (connection refused), all E2E tests
are skipped with a clear message rather than failing.

Usage:
    # Start the backend locally first:
    docker compose up -d rmi-backend
    # Or run against a deployed instance:
    TEST_BASE_URL=https://api.staging.rugmunch.io pytest tests/e2e/ -v
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session")
def e2e_base_url() -> str:
    """Base URL of the running rmi-backend instance.

    Priority: TEST_BASE_URL env var → http://localhost:8000 fallback
    """
    return os.getenv("TEST_BASE_URL", "http://localhost:8000")


@pytest.fixture(scope="session")
def e2e_auth() -> dict[str, str]:
    """Auth headers for the running API.

    Uses E2E_API_KEY env var or falls back to the test token.
    """
    api_key = os.getenv("E2E_API_KEY", "test-auth-token-32-chars-long!!")
    return {"X-API-Key": api_key}


@pytest.fixture(scope="module")
async def e2e_client(e2e_base_url: str):
    """HTTP async client against the live E2E API.

    If the service is unreachable, all tests in the module are skipped.
    """
    import httpx

    try:
        async with httpx.AsyncClient(
            base_url=e2e_base_url,
            timeout=httpx.Timeout(connect=5.0, read=30.0, pool=5.0),
            headers={"Content-Type": "application/json"},
        ) as client:
            # Quick health check to verify the service is up
            try:
                resp = await client.get("/version", timeout=5.0)
                assert resp.status_code == 200, f"Health check failed: {resp.status_code}"
            except (httpx.ConnectError, httpx.TimeoutException, AssertionError) as exc:
                pytest.skip(
                    f"E2E service not reachable at {e2e_base_url}: {exc}. Start the backend or set TEST_BASE_URL."
                )
            yield client
    except ImportError:
        pytest.skip("httpx not installed — cannot run E2E tests")
