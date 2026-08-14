"""Property-based API fuzzing via Schemathesis against the app's own OpenAPI schema.

Fuzzes a curated, **offline-safe** subset of the FastAPI app. The schema is
loaded in-process from the ASGI app (``schemathesis.openapi.from_asgi``) so
no server is started and no external RPC/HTTP calls are made — every request
is served by the in-memory ASGI transport.

Contract asserted (per the Schemathesis core guarantee): no generated case
may produce an unhandled 5xx server error. 4xx validation rejections are
expected and allowed — they are the *validation* half of the test.

Why only this subset: every fuzzed endpoint must run fully offline. Routes
that hit RPC providers, CoinGecko/DexScreener, SENTINEL, the RAG engine,
Redis/Postgres/ClickHouse, or subprocesses (rclone) are deliberately
excluded — fuzzing them offline would either fail with false 5xx or make
real network calls. The health/readiness store probes are stubbed (see
``_stub_health_store_checks``) so ``/health`` and ``/ready`` never touch
real dependencies. ``/metrics`` and ``/.well-known/jwks.json`` are excluded
because they are not part of the OpenAPI schema (``include_in_schema=False``).

Runtime budget: 8 endpoints, each with a single generated case per
Hypothesis iteration bounded by ``max_examples=5`` — the whole module runs
in a few seconds after the ~10s app import. Marked ``slow`` so CI can run
``pytest -m "not slow"`` to skip.
"""

from __future__ import annotations

import os

# Mirror tests/conftest.py BEFORE any app module is imported: app.config and
# several routers read these env vars at import time.
os.environ["ENVIRONMENT"] = "dev"
os.environ["RMI_AUTH_TOKEN"] = "test-auth-token-32-chars-long!!"
os.environ["RMI_INTERNAL_KEY"] = "test-internal-key-32-chars-long!!!"
os.environ["JWT_SECRET"] = "a" * 32
os.environ["CERTSTREAM_ENABLED"] = "false"
os.environ["WEBHOOK_ENGINE_ENABLED"] = "false"

from unittest.mock import patch

import pytest
from hypothesis import HealthCheck, settings

schemathesis = pytest.importorskip("schemathesis", reason="schemathesis not installed")

# Imported after importorskip so a missing schemathesis skips this module
# without paying the ~10s cost of building the app.
from app.factory import create_app  # noqa: E402

# ── Offline-safe endpoints to fuzz (all in the OpenAPI schema) ──────────
# Each is verified network-free: pure handlers, in-memory metrics, or store
# probes stubbed by the autouse fixture below.
FUZZ_ENDPOINTS: tuple[str, ...] = (
    "/health",  # full health (store probes stubbed)
    "/live",  # liveness (pure)
    "/ready",  # readiness (store probes stubbed)
    "/version",  # version info (pure)
    "/",  # homepage (pure)
    "/api",  # API version index (pure)
    "/api/v1/auth/jwks",  # JWKS public key (in-memory, HS256 fallback)
    "/api/v1/rag/metrics",  # RAG observability (in-memory counters)
)

# Build the app + schema once at module level: ``parametrize()`` needs the
# operation set at collection time. This is the ~10s slow part.
APP = create_app()

FUZZ_SCHEMA = schemathesis.openapi.from_asgi("/openapi.json", app=APP).include(
    path=list(FUZZ_ENDPOINTS),
    method="GET",
)

# Bounded fuzz: a handful of generated cases per endpoint. Parameterless
# endpoints yield one case each by construction; this caps the worst case.
FUZZ_SCHEMA.hypothesis_settings = settings(
    max_examples=5,
    deadline=None,
    database=None,
    suppress_health_check=list(HealthCheck),
)


@pytest.fixture(autouse=True)
def _allow_rate_limits():
    """Open the unified rate limiter so the middleware never 429s or 500s.

    Mirrors tests/test_rag_endpoints.py::_allow_global_rate_limits. Without
    Redis the limiter falls back to in-memory state; patching ``acquire``
    keeps every fuzz case deterministic and dependency-free.
    """
    from app.core.rate_limiter import UnifiedRateLimiter

    async def _allow(self, key, rate=1.0, burst=5.0, tokens=1.0):
        return (
            True,
            {
                "X-RateLimit-Limit": int(burst),
                "X-RateLimit-Remaining": 1,
                "X-RateLimit-Reset": 0,
            },
        )

    with patch.object(UnifiedRateLimiter, "acquire", _allow):
        yield


@pytest.fixture(autouse=True)
def _stub_health_store_checks():
    """Stub every health/readiness store probe so no real I/O occurs.

    ``/health`` and ``/ready`` otherwise ping Redis/Postgres/Qdrant/etc.
    Each check is an async function returning a fail-open ``healthy`` dict,
    so the handlers exercise their full aggregation/status logic offline.
    """
    import app.core.health_route as health_route

    async def _ok() -> dict[str, object]:
        return {"healthy": True}

    patches = [
        patch.object(health_route, name, _ok)
        for name in (
            "_check_redis",
            "_check_postgres",
            "_check_neo4j",
            "_check_qdrant",
            "_check_clickhouse",
            "_check_minio",
            "_check_reth",
            "_check_degenfeed",
            "_check_phoenix",
            "_check_rag_pipeline",
        )
    ]
    for patcher in patches:
        patcher.start()
    try:
        yield
    finally:
        for patcher in patches:
            patcher.stop()


@pytest.mark.slow
@FUZZ_SCHEMA.parametrize()
def test_no_unhandled_server_error(case) -> None:
    """No generated request may crash the app into a 5xx response.

    4xx (validation/auth) responses are fine — they prove the schema-driven
    validation layer is rejecting malformed input instead of blowing up.
    """
    response = case.call()
    assert response.status_code < 500, (
        f"unhandled {response.status_code} for {case.method} {case.path} "
        f"with {case.as_transport_kwargs()}: {response.text[:200]}"
    )
