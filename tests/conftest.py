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

collect_ignore: list[str] = []
collect_ignore_glob = ["manual/*"]

# ── rugmunch_scanner availability guard ──────────────────────────────
# rugmunch_scanner is a private package installed inside the Docker image
# (pip install git+...rugmunch-scanner.git) but NOT in local dev/test envs.
# The test modules below import app thin-adapter modules that re-export from
# rugmunch_scanner at import time, so they cannot even be collected without
# it. Exclude them from collection when the package is missing (they still
# run in the container). Mocking the package would make the detectors
# MagicMocks and every assertion meaningless, so exclusion — not faking —
# is the correct dev-env behavior.
try:
    import rugmunch_scanner  # noqa: F401

    _HAS_RUGMUNCH_SCANNER = True
except ImportError:  # pragma: no cover — depends on dev env
    _HAS_RUGMUNCH_SCANNER = False

if not _HAS_RUGMUNCH_SCANNER:
    collect_ignore += [
        "test_launchpad_lp.py",
        "test_pumpfun_metadata.py",
        "test_scan_numbers.py",
        "test_scanner_degodding.py",
        "unit/domain/scanner/test_bundle_detector_behavior.py",
        "unit/domain/scanner/test_contract_authority.py",
        "unit/domain/scanner/test_cross_chain_trace.py",
        "unit/domain/scanner/test_holder_analyzer.py",
        "unit/domain/scanner/test_liquidity_verifier.py",
        "unit/domain/scanner/test_oracle_manipulation.py",
        "unit/domain/scanner/test_proxy_detector.py",
        "unit/domain/scanners/test_cross_launch_correlation.py",
    ]

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
from hypothesis import HealthCheck, settings  # noqa: E402 — module docstring precedes import block

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
import pytest  # noqa: E402 — module docstring precedes import block


@pytest.fixture
def auth_headers():
    """Standard API key auth headers for integration tests."""
    return {"X-API-Key": "test-auth-token-32-chars-long!!"}


@pytest.fixture
def csrf_headers(auth_headers):
    """Auth headers plus CSRF token."""
    return {**auth_headers, "X-CSRF-Token": "test-csrf-token"}


@pytest.fixture(autouse=True)
def _no_chat_market_context(monkeypatch):
    """Keep chat tests hermetic: stub the chat market-context upstreams.

    The chat router injects a live market-context block into every system
    prompt (app/routers/chat/_market_context.py, best-effort). Stubbing its
    two upstream fetchers makes the block empty in tests — no real network
    calls to CoinGecko/alternative.me. This only patches the chat module's
    own fetchers, never ``market_intel.providers`` (used by market-intel
    routes and their tests).
    """
    from unittest.mock import AsyncMock

    monkeypatch.setattr("app.routers.chat._market_context._cg", AsyncMock(return_value=None))
    monkeypatch.setattr("app.routers.chat._market_context._fng", AsyncMock(return_value=None))
