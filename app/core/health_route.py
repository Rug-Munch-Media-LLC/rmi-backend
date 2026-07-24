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
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter
from pydantic import BaseModel

try:
    from app.core.metrics import HEALTH_CHECK_DURATION, HEALTH_CHECK_STATUS
except (ImportError, ModuleNotFoundError):
    # prometheus_client may not be installed — health endpoints must still work
    _HAS_METRICS = False
    HEALTH_CHECK_DURATION = None  # type: ignore[assignment]
    HEALTH_CHECK_STATUS = None  # type: ignore[assignment]
else:
    _HAS_METRICS = True

if TYPE_CHECKING:
    import httpx

router = APIRouter(tags=["health"])

# ── Environment ──────────────────────────────────────────────────
_ENV = os.getenv("ENVIRONMENT", "production").lower()
_IS_DEV = _ENV in ("dev", "development", "local", "test")


def _get_url(key: str, docker: str, local: str) -> str:
    """Resolve a service URL: explicit env var wins, otherwise pick the
    Docker-Compose default (production) or the localhost default (dev).
    """
    val = os.getenv(key)
    return val if val is not None else (local if _IS_DEV else docker)


# ── Service URLs (module-level for reuse) ────────────────────────
# Defaults use Docker Compose service names so containers resolve
# each other without explicit env vars.  Dev / test mode overrides
# to localhost so ``pytest`` and local ``uvicorn`` work out of the box.
QDRANT_URL = _get_url(
    "QDRANT_URL",
    docker="http://rmi-qdrant:6333",
    local="http://127.0.0.1:6333",
)
CLICKHOUSE_URL = _get_url(
    "CLICKHOUSE_URL",
    docker="http://rmi-clickhouse:8123/",
    local="http://127.0.0.1:8123/",
)
MINIO_URL = _get_url(
    "MINIO_URL",
    docker="http://rmi-minio:9000",
    local="http://127.0.0.1:9000",
)
# reth typically runs on the host or as a sidecar — same default for both
RETH_URL = _get_url(
    "RETH_URL",
    docker="http://127.0.0.1:8545",
    local="http://127.0.0.1:8545",
)

# Fallback constants used by inline os.getenv() in the check functions.
# These mirror the _get_url pattern so module-level validation can
# inspect them without duplicating the URL logic.
_DATABASE_URL_DOCKER = "postgresql://rmi:***@rmi-postgres:5432/rmi"
_DATABASE_URL_DEV = "postgresql://rmi:***@127.0.0.1:5432/rmi"
_NEO4J_URI_DOCKER = "bolt://rmi-neo4j:7687"
_NEO4J_URI_DEV = "bolt://localhost:7687"
_REDIS_URL_DOCKER = "redis://rmi-redis:6379/0"
_REDIS_URL_DEV = "redis://localhost:6379/0"

# ── Config validation schema ────────────────────────────────────
# Required — health check considers these critical
_REQUIRED_CONFIG: dict[str, str] = {
    "DATABASE_URL": "PostgreSQL connection string",
    "REDIS_URL": "Redis connection string (used by app.core.redis)",
    "CLICKHOUSE_URL": "ClickHouse HTTP endpoint",
}
# Optional — health check tolerates these being unset or placeholder
_OPTIONAL_CONFIG: dict[str, str] = {
    "QDRANT_URL": "Qdrant HTTP endpoint",
    "MINIO_URL": "MinIO S3-compatible storage endpoint",
    "NEO4J_URI": "Neo4j bolt URI (NEO4J_URL is also accepted)",
    "RETH_URL": "Ethereum RPC endpoint",
    "CLICKHOUSE_USER": "ClickHouse username (default: rmi)",
    "NEO4J_USER": "Neo4j username (default: neo4j)",
}

_SHARED_HTTP_CLIENT: httpx.AsyncClient | None = None


async def _get_http_client() -> httpx.AsyncClient:
    global _SHARED_HTTP_CLIENT
    if _SHARED_HTTP_CLIENT is None:
        import httpx

        _SHARED_HTTP_CLIENT = httpx.AsyncClient(timeout=5.0)
    return _SHARED_HTTP_CLIENT


# ── Config validation ───────────────────────────────────────────


def check_config() -> list[dict[str, str]]:
    """Validate required / optional env vars.

    Returns a list of issues (empty = all good).  Each issue has
    ``key``, ``description``, and ``severity`` (``"missing"`` or
    ``"placeholder"``).
    """
    issues: list[dict[str, str]] = []

    _placeholder_patterns = ("***", "changeme", "placeholder", "your_")

    for key, desc in _REQUIRED_CONFIG.items():
        val = os.getenv(key)
        if not val:
            issues.append({"key": key, "description": desc, "severity": "missing"})
        elif any(p in val.lower() for p in _placeholder_patterns):
            issues.append({"key": key, "description": desc, "severity": "placeholder"})

    for key, desc in _OPTIONAL_CONFIG.items():
        val = os.getenv(key)
        if val and any(p in val.lower() for p in _placeholder_patterns):
            issues.append({"key": key, "description": desc, "severity": "placeholder"})

    return issues


# ── Response models ─────────────────────────────────────────────


class HealthResponse(BaseModel):
    status: str
    version: str
    uptime_seconds: float
    stores: dict[str, dict[str, Any]]

    model_config = {"extra": "allow"}
    config_issues: list[dict[str, str]] = []


class LivenessResponse(BaseModel):
    status: str = "alive"


class ReadinessResponse(BaseModel):
    status: str
    checks: dict[str, bool]


_START_TIME = time.monotonic()


# ── Store check helpers ─────────────────────────────────────────


async def _check_store(name: str, coro) -> dict[str, Any]:
    """Run a health check and emit Prometheus metrics (if available)."""
    start = time.perf_counter()
    try:
        result = await asyncio.wait_for(coro, timeout=5.0)
        elapsed = time.perf_counter() - start
        healthy = result.get("healthy", False)
        if _HAS_METRICS:
            HEALTH_CHECK_DURATION.labels(store=name).set(elapsed)
            HEALTH_CHECK_STATUS.labels(store=name).set(1 if healthy else 0)
        return result
    except Exception as e:
        elapsed = time.perf_counter() - start
        if _HAS_METRICS:
            HEALTH_CHECK_DURATION.labels(store=name).set(elapsed)
            HEALTH_CHECK_STATUS.labels(store=name).set(0)
        return {"healthy": False, "error": str(e)[:200]}


async def _check_redis() -> dict[str, Any]:
    try:
        from app.core.redis import get_redis_async

        client = get_redis_async()
        if client is None:
            return {"healthy": False, "error": "Redis pool not initialized"}
        await asyncio.wait_for(client.ping(), timeout=5.0)
        return {"healthy": True}
    except Exception as e:
        return {"healthy": False, "error": str(e)[:200]}


async def _check_postgres() -> dict[str, Any]:
    try:
        import asyncpg

        db_url = os.getenv(
            "DATABASE_URL",
            _DATABASE_URL_DEV if _IS_DEV else _DATABASE_URL_DOCKER,
        )
        conn = await asyncpg.connect(db_url)
        await asyncio.wait_for(conn.fetchval("SELECT 1"), timeout=5.0)
        await conn.close()
        return {"healthy": True}
    except Exception as e:
        return {"healthy": False, "error": str(e)[:200]}


async def _check_neo4j() -> dict[str, Any]:
    try:
        try:
            from app.core.neo4j_connection import _driver
        except ImportError:
            from neo4j import GraphDatabase

            uri = (
                os.getenv("NEO4J_URI")
                or os.getenv("NEO4J_URL")
                or (_NEO4J_URI_DEV if _IS_DEV else _NEO4J_URI_DOCKER)
            )
            user = os.getenv("NEO4J_USER", "neo4j")
            password = os.getenv("NEO4J_PASSWORD", "")
            _driver = GraphDatabase.driver(uri, auth=(user, password)) if password else None

        if _driver is None:
            return {"healthy": False, "error": "Neo4j not configured (no NEO4J_PASSWORD set)"}
        with _driver.session() as session:
            val = await asyncio.wait_for(
                asyncio.to_thread(lambda: session.run("RETURN 1").single()), timeout=5.0
            )
        return {"healthy": val[0] == 1}
    except Exception as e:
        return {"healthy": False, "error": str(e)[:200]}


async def _check_qdrant() -> dict[str, Any]:
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{QDRANT_URL.rstrip(chr(47))}/")
            data = resp.json()
        return {
            "healthy": resp.status_code == 200,
            "details": {"version": data.get("version", "unknown")},
        }
    except Exception as e:
        return {"healthy": False, "error": str(e)[:200]}


async def _check_clickhouse() -> dict[str, Any]:
    try:
        import base64

        user = os.getenv("CLICKHOUSE_USER", "rmi")
        password = os.getenv("CLICKHOUSE_PASSWORD", os.getenv("CLICKHOUSE_PASS", ""))
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        headers = {"Authorization": f"Basic {token}"}
        client = await _get_http_client()
        resp = await client.post(CLICKHOUSE_URL, content="SELECT 1", headers=headers)
        db_resp = await client.post(CLICKHOUSE_URL, content="SHOW DATABASES", headers=headers)
        dbs = db_resp.text.strip().split("\n") if db_resp.status_code == 200 else []
        return {
            "healthy": resp.text.strip() == "1",
            "details": {"rmi_db_exists": "rmi" in dbs, "databases": dbs},
        }
    except Exception as e:
        return {"healthy": False, "error": str(e)[:200]}


async def _check_minio() -> dict[str, Any]:
    try:
        client = await _get_http_client()
        resp = await client.get(f"{MINIO_URL.rstrip(chr(47))}/minio/health/live")
        return {"healthy": resp.status_code == 200, "details": {"status_code": resp.status_code}}
    except Exception as e:
        return {"healthy": False, "error": str(e)[:200]}


async def _check_reth() -> dict[str, Any]:
    try:
        rpc_url = os.getenv("RETH_URL", "https://ethereum-rpc.publicnode.com")
        client = await _get_http_client()
        resp = await client.post(
            rpc_url,
            json={"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1},
        )
        data = resp.json()
        block_hex = data.get("result", "0x0")
        block_num = int(block_hex, 16) if block_hex.startswith("0x") else 0
        return {"healthy": block_num > 0, "details": {"block_number": block_num}}
    except Exception as e:
        return {"healthy": False, "error": str(e)[:200]}


def _sync_degradation_from_health(stores: dict[str, dict[str, Any]]) -> None:
    """Win #14: Sync health check results to the degradation registry."""
    try:
        from app.core.degradation import get_registry

        registry = get_registry()
        for name in ("clickhouse", "neo4j", "minio", "redis"):
            if name in stores:
                if stores[name].get("healthy"):
                    registry.mark_healthy(name)
                else:
                    registry.mark_down(name)
    except (AttributeError, ImportError):
        pass


def _get_degradation_section() -> dict[str, Any]:
    """Win #14: Get degradation section for health endpoint."""
    try:
        from app.core.degradation import get_degradation_health

        return {"degradation": get_degradation_health()}
    except (AttributeError, ImportError):
        return {}


# ── Endpoints ───────────────────────────────────────────────────


@router.get("/live", response_model=LivenessResponse, summary="Liveness", description="Kubernetes-style liveness probe. Returns 200 if the process is alive. No dependency checks.")
async def liveness() -> LivenessResponse:
    return LivenessResponse(status="alive")


@router.get("/ready", response_model=ReadinessResponse, summary="Readiness", description="Kubernetes-style readiness probe. Checks critical dependencies (Redis, PostgreSQL). Returns 200 only when all critical stores are reachable.")
async def readiness() -> ReadinessResponse:
    r1 = await _check_store("redis", _check_redis())
    r2 = await _check_store("postgres", _check_postgres())
    redis_ok = r1 if not isinstance(r1, Exception) else {"healthy": False}
    pg_ok = r2 if not isinstance(r2, Exception) else {"healthy": False}
    checks = {"redis": redis_ok.get("healthy", False), "postgres": pg_ok.get("healthy", False)}
    all_ok = all(checks.values())
    return ReadinessResponse(status="ready" if all_ok else "not_ready", checks=checks)


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    started = time.monotonic()
    store_checks = {
        "redis": asyncio.wait_for(_check_store("redis", _check_redis()), timeout=5.0),
        "postgres": asyncio.wait_for(_check_store("postgres", _check_postgres()), timeout=5.0),
        "neo4j": asyncio.wait_for(_check_store("neo4j", _check_neo4j()), timeout=5.0),
        "qdrant": asyncio.wait_for(_check_store("qdrant", _check_qdrant()), timeout=5.0),
        "clickhouse": asyncio.wait_for(
            _check_store("clickhouse", _check_clickhouse()), timeout=5.0
        ),
        "minio": asyncio.wait_for(_check_store("minio", _check_minio()), timeout=5.0),
        "eth_rpc": asyncio.wait_for(_check_store("eth_rpc", _check_reth()), timeout=5.0),
    }
    results = await asyncio.gather(*store_checks.values(), return_exceptions=True)
    stores = {}
    for (name, _), ref in zip(store_checks.items(), results, strict=False):
        if isinstance(ref, Exception):
            ref = {"healthy": False, "error": str(ref)[:200]}
        if "latency_ms" not in ref:
            ref["latency_ms"] = int((time.monotonic() - started) * 1000)
        stores[name] = ref

    # Sync health check results with degradation registry (Win #14)
    _sync_degradation_from_health(stores)

    # Critical = redis or postgres down. Non-critical stores can be degraded.
    critical_up = stores["redis"].get("healthy") and stores["postgres"].get("healthy")
    any_non_critical_down = not all(
        s.get("healthy", False) for n, s in stores.items() if n not in ("redis", "postgres")
    )
    status = "unhealthy" if not critical_up else "degraded" if any_non_critical_down else "healthy"

    # Include degradation info in response
    degradation = _get_degradation_section()

    return HealthResponse(
        status=status,
        version="2026.06.21",
        uptime_seconds=time.monotonic() - _START_TIME,
        stores=stores | degradation,
    )
    status = "unhealthy" if not critical_up else "degraded" if any_non_critical_down else "healthy"

    return HealthResponse(
        status=status,
        version="2026.06.21",
        uptime_seconds=time.monotonic() - _START_TIME,
        stores=stores,
        config_issues=check_config(),
    )
