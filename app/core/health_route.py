"""Health routes - /health, /live, /ready.

Per RMIV5 v4.0 §T33. Provides basic Kubernetes-style health endpoints:
  - /health  - full health (deep checks)
  - /live    - liveness (process alive, no deps)
  - /ready   - readiness (critical deps reachable)

Emits Prometheus HEALTH_CHECK_DURATION + HEALTH_CHECK_STATUS gauges per store.

Environment variables for service URLs — defaults are Docker Compose
service names so containers discover each other without explicit env.
Dev/local/test mode switches defaults to localhost.

  URL                    Env var           Docker default            Dev default
  ─────────────────────────────────────────────────────────────────────────────
  Qdrant                 QDRANT_URL        http://rmi-qdrant:6333    http://127.0.0.1:6333
  ClickHouse             CLICKHOUSE_URL    http://rmi-clickhouse:8123/  http://127.0.0.1:8123/
  MinIO                  MINIO_URL         http://rmi-minio:9000     http://127.0.0.1:9000
  Ethereum RPC           RETH_URL          http://127.0.0.1:8545     http://127.0.0.1:8545
  PostgreSQL             DATABASE_URL      postgresql://rmi:***@rmi-postgres:5432/rmi  postgresql://rmi:***@127.0.0.1:5432/rmi
  Neo4j                  NEO4J_URI/NEO4J_URL bolt://rmi-neo4j:7687  bolt://localhost:7687
  Redis                  REDIS_URL         redis://rmi-redis:6379/0  redis://localhost:6379/0

Structure (de-godded 2026-08-21, behavior-preserving):
  - ``health_env.py``    — env/URL resolution + ``check_config``
  - ``health_stores.py`` — store probes (redis/postgres/neo4j/qdrant/
    clickhouse/minio/eth_rpc/degenfeed/phoenix) + ``_check_store`` +
    degradation sync + the shared HTTP client
  - ``health_rag.py``    — RAG ingestion pipeline probe
  - this module          — response models + the three endpoints (thin
    assembler) + re-exports so every existing importer and every
    ``patch("app.core.health_route._check_*")`` keeps working. The
    handlers resolve the check functions via THIS module's globals at
    call time — do not inline-import them inside the handlers.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core import health_ports, health_rag, health_status
from app.core.health_env import (
    _DATABASE_URL_DEV,
    _DATABASE_URL_DOCKER,
    _ENV,
    _IS_DEV,
    _NEO4J_URI_DEV,
    _NEO4J_URI_DOCKER,
    _OPTIONAL_CONFIG,
    _REDIS_URL_DEV,
    _REDIS_URL_DOCKER,
    _REQUIRED_CONFIG,
    CLICKHOUSE_URL,
    MINIO_URL,
    QDRANT_URL,
    RETH_URL,
    _get_url,
    check_config,
)
from app.core.health_stores import (
    _check_clickhouse,
    _check_degenfeed,
    _check_minio,
    _check_neo4j,
    _check_phoenix,
    _check_postgres,
    _check_qdrant,
    _check_redis,
    _check_reth,
    _check_store,
    _get_degradation_section,
    _get_http_client,
    _sync_degradation_from_health,
)

# Public surface — re-exported so existing importers (app/routers/v1/health.py,
# app/mount.py, tests) keep working unchanged.
__all__ = [
    "CLICKHOUSE_URL",
    "MINIO_URL",
    "QDRANT_URL",
    "RETH_URL",
    "_DATABASE_URL_DEV",
    "_DATABASE_URL_DOCKER",
    "_ENV",
    "_IS_DEV",
    "_NEO4J_URI_DEV",
    "_NEO4J_URI_DOCKER",
    "_OPTIONAL_CONFIG",
    "_REDIS_URL_DEV",
    "_REDIS_URL_DOCKER",
    "_REQUIRED_CONFIG",
    "HealthResponse",
    "LivenessResponse",
    "ReadinessResponse",
    "_check_clickhouse",
    "_check_degenfeed",
    "_check_minio",
    "_check_neo4j",
    "_check_phoenix",
    "_check_postgres",
    "_check_qdrant",
    "_check_rag_pipeline",
    "_check_redis",
    "_check_reth",
    "_check_store",
    "_get_degradation_section",
    "_get_http_client",
    "_get_url",
    "_sync_degradation_from_health",
    "check_config",
    "router",
]

router = APIRouter(tags=["health"])


# ── Response models ─────────────────────────────────────────────


class HealthResponse(BaseModel):
    status: str
    version: str
    uptime_seconds: float
    stores: dict[str, dict[str, Any]]
    rag_pipeline: dict[str, Any] = {}
    # Which scoring calibration answered: {"name", "source", "illustrative"}.
    # A deployment scoring on the example policy is visible here, not inferred
    # from a log line nobody reads.
    risk_policy: dict[str, Any] = {}

    model_config = {"extra": "allow"}
    config_issues: list[dict[str, str]] = []


class LivenessResponse(BaseModel):
    status: str = "alive"


class ReadinessResponse(BaseModel):
    status: str
    checks: dict[str, bool]


_START_TIME = time.monotonic()


def _risk_policy_report() -> dict[str, Any]:
    """Resolved scoring policy, degrading to a visible error (never silent).

    Delegates to the core-owned port (``app.core.health_ports``). Core may not
    import ``app.domains``; the scanner domain binds the adapter in
    ``app.factory``, the composition root.
    """
    return health_ports.risk_policy_report()


async def _check_rag_pipeline() -> dict[str, Any]:
    """RAG pipeline probe wrapper.

    Delegates to :func:`app.core.health_rag.check_rag_pipeline`, injecting
    THIS module's ``_get_http_client`` so monkeypatching
    ``app.core.health_route._get_http_client`` keeps controlling the
    rugbrain-api HTTP client (pre-split behavior).
    """
    return await health_rag.check_rag_pipeline(client_factory=_get_http_client)


# ── Endpoints ───────────────────────────────────────────────────


@router.get(
    "/live",
    response_model=LivenessResponse,
    summary="Liveness",
    description="Kubernetes-style liveness probe. Returns 200 if the process is alive. No dependency checks.",
)
async def liveness() -> LivenessResponse:
    return LivenessResponse(status="alive")


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness",
    description="Kubernetes-style readiness probe. Checks critical dependencies (Redis, PostgreSQL) and reports per-store health in the body. Always returns 200 — orchestrators read ``status`` (``ready``/``not_ready``) and ``checks`` from the payload.",
)
async def readiness() -> JSONResponse:
    r1 = await _check_store("redis", _check_redis())
    r2 = await _check_store("postgres", _check_postgres())
    redis_ok = r1 if not isinstance(r1, Exception) else {"healthy": False}
    pg_ok = r2 if not isinstance(r2, Exception) else {"healthy": False}
    checks = {"redis": redis_ok.get("healthy", False), "postgres": pg_ok.get("healthy", False)}
    all_ok = all(checks.values())
    # Always 200: the readiness STATE is carried in the body so dashboards
    # and orchestrators can distinguish "probe failed" from "deps down".
    return JSONResponse(
        status_code=200,
        content={"status": "ready" if all_ok else "not_ready", "checks": checks},
    )


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Full Health Check",
    description="Comprehensive system health check. Probes Redis, PostgreSQL, Neo4j, Qdrant, ClickHouse, MinIO, and Ethereum RPC. Returns aggregated status with per-store health, latency, and config validation.",
)
async def health() -> HealthResponse:
    started = time.monotonic()
    # Every probe is timed inside _check_store, so the timeout budget and the
    # reported latency belong to the store that spent them. Do not wrap these
    # in a second wait_for: an outer timeout that expires first mislabels the
    # whole request's wall clock as the store's latency (it read 5078 ms on
    # eight stores at once, then reported them healthy).
    store_checks = {
        "redis": _check_store("redis", _check_redis()),
        "postgres": _check_store("postgres", _check_postgres()),
        "neo4j": _check_store("neo4j", _check_neo4j()),
        "qdrant": _check_store("qdrant", _check_qdrant()),
        "clickhouse": _check_store("clickhouse", _check_clickhouse()),
        "minio": _check_store("minio", _check_minio()),
        "eth_rpc": _check_store("eth_rpc", _check_reth()),
        "degenfeed": _check_store("degenfeed", _check_degenfeed()),
        "phoenix": _check_store("phoenix", _check_phoenix()),
        "rag_pipeline": asyncio.wait_for(_check_rag_pipeline(), timeout=5.0),
    }
    results = await asyncio.gather(*store_checks.values(), return_exceptions=True)
    stores = {}
    for (name, _), ref in zip(store_checks.items(), results, strict=False):
        if isinstance(ref, BaseException):
            # Escaped _check_store entirely: it really did consume this much
            # of the request, and it is not a healthy dependency.
            ref = {
                "healthy": False,
                "error": str(ref)[:200] or type(ref).__name__,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "latency_state": "unhealthy",
            }
        stores[name] = ref

    # RAG stats are observability, not a liveness gate; the verdict rules live
    # in health_status so this handler stays a thin assembler.
    rag_pipeline = health_status.finalize_rag_pipeline(stores)
    _sync_degradation_from_health(stores)  # Win #14
    status = health_status.compute_status(stores)

    # Include degradation info in response
    degradation = _get_degradation_section()

    config_issues = check_config()
    return HealthResponse(
        status=status,
        version="2026.06.21",
        uptime_seconds=time.monotonic() - _START_TIME,
        stores=stores | degradation,
        rag_pipeline=rag_pipeline,
        risk_policy=_risk_policy_report(),
        config_issues=config_issues,
    )
