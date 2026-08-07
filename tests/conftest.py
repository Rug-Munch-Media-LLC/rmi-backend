"""Pytest root conftest for the rmi-backend tests/ tree.

Excludes tests/manual/ from pytest auto-collection. Files in tests/manual/
are bespoke runners (custom @test() decorators, banner warnings about
pytest incompatibility, ad-hoc debugging scripts) that the audit's
manual-test suite owns. Run them directly with python3, not pytest:

    python3 tests/manual/run_tests.py
    python3 tests/manual/test_rag.py

To temporarily include them in a pytest run (e.g. for migration):

    pytest tests/ --collect-ignore-glob='"'"'!tests/manual/*'"'"'

Registers Hypothesis profiles:
  - `ci`:       derandomized, 200 examples, 5s deadline — for CI pipelines.
  - `dev`:      50 examples, no deadline, tolerant health checks — for local dev.
  - `ci-quick`: derandomized, 50 examples, 2s deadline — fast CI smoke tests.
"""

import os

collect_ignore_glob = ["manual/*"]

# Set safe defaults for test environment (avoids RuntimeError on jwt.py import
# when JWT_ALGORITHM=RS256 but keys aren't configured)
os.environ.setdefault("ENVIRONMENT", "dev")

# Force the test auth credentials (not setdefault): a real RMI_AUTH_TOKEN in
# the developer's shell would otherwise leak into the app, and app.config
# caches whichever token is active at first import — making auth-dependent
# tests (contract, security, middleware) order/environment-dependent.
os.environ["RMI_AUTH_TOKEN"] = "test-auth-token-32-chars-long!!"
os.environ["RMI_INTERNAL_KEY"] = "test-internal-key-32-chars-long!!!"
os.environ["JWT_SECRET"] = "test-jwt-secret-key-thats-32-chars-long!"

# ── Hypothesis profiles ───────────────────────────────────────────────
from hypothesis import HealthCheck, settings

settings.register_profile(
    "ci",
    max_examples=200,
    derandomize=True,
    deadline=5000,
    suppress_health_check=[],
    report_multiple_bugs=False,
)

settings.register_profile(
    "dev",
    max_examples=50,
    derandomize=False,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.filter_too_much],
    report_multiple_bugs=True,
)

settings.register_profile(
    "ci-quick",
    max_examples=50,
    derandomize=True,
    deadline=2000,
    suppress_health_check=[],
    report_multiple_bugs=False,
)


# ── Shared auth fixtures ──────────────────────────────────────────────
import pytest


@pytest.fixture
def auth_headers():
    """Standard API key auth headers for integration tests."""
    return {"X-API-Key": "test-auth-token-32-chars-long!!"}


@pytest.fixture
def csrf_headers(auth_headers):
    """Auth headers plus CSRF token."""
    return {**auth_headers, "X-CSRF-Token": "test-csrf-token"}
