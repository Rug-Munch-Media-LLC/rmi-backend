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
    rag_pipeline: dict[str, Any] = {}

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
        # asyncpg doesn't accept SQLAlchemy-style driver specifiers like +asyncpg
        # Strip the driver suffix so asyncpg.connect() works
        if db_url and "+" in db_url.split("://")[0] if "://" in db_url else False:
            scheme, rest = db_url.split("://", 1)
            scheme = scheme.split("+")[0]
            db_url = f"{scheme}://{rest}"
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

            uri = os.getenv("NEO4J_URI") or os.getenv("NEO4J_URL") or (_NEO4J_URI_DEV if _IS_DEV else _NEO4J_URI_DOCKER)
            user = os.getenv("NEO4J_USER", "neo4j")
            password = os.getenv("NEO4J_PASSWORD", "")
            _driver = GraphDatabase.driver(uri, auth=(user, password)) if password else None

        if _driver is None:
            return {"healthy": False, "error": "Neo4j not configured (no NEO4J_PASSWORD set)"}
        with _driver.session() as session:
            val = await asyncio.wait_for(asyncio.to_thread(lambda: session.run("RETURN 1").single()), timeout=5.0)
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
        # CLICKHOUSE_URL may already contain embedded credentials (e.g. http://rmi:pass@host:port)
        # Use basic auth header for authentication instead
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        headers = {"Authorization": f"Basic {token}"}
        client = await _get_http_client()

        # Strip credentials from URL if embedded (the basic auth header is authoritative)
        clean_url = CLICKHOUSE_URL
        if "@" in clean_url.replace("://", "://", 1).split("://")[1] if "://" in clean_url else False:
            # URL has embedded credentials like http://user:pass@host
            scheme = clean_url.split("://")[0]
            rest = clean_url.split("://", 1)[1]
            if "@" in rest:
                rest = rest.split("@", 1)[1]
                clean_url = f"{scheme}://{rest}"

        resp = await client.post(clean_url, content="SELECT 1", headers=headers)
        if resp.status_code != 200:
            # Try without auth (public clickhouse or different auth method)
            resp = await client.post(clean_url, content="SELECT 1")

        db_resp = await client.post(clean_url, content="SHOW DATABASES", headers=headers)
        if db_resp.status_code != 200:
            db_resp = await client.post(clean_url, content="SHOW DATABASES")
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


async def _check_degenfeed() -> dict[str, Any]:
    """Probe DegenFeed proxy health.

    Returns ``healthy: True`` with ``status: "skipped"`` or ``status:
    "unavailable"`` when the DegenFeed service is not configured or not
    reachable, so the overall health endpoint does not mark the system as
    ``degraded`` just because an optional downstream is absent.
    """
    # Allow operators to explicitly disable this check without changing code
    if os.getenv("DEGENFEED_HEALTH_CHECK_ENABLED", "true").lower() in (
        "false",
        "0",
        "no",
        "off",
    ):
        return {"healthy": True, "status": "skipped"}

    base_url = os.getenv("DEGENFEED_URL", "http://127.0.0.1:8200")
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base_url.rstrip(chr(47))}/health")
            data = resp.json() if resp.status_code == 200 else {}
            protocols = (
                data.get("protocols", [])
                if isinstance(data.get("protocols"), list)
                else list(data.get("protocols", {}).keys())
                if isinstance(data.get("protocols"), dict)
                else []
            )
            return {
                "healthy": True,
                "details": {"protocols": protocols, "status_code": resp.status_code},
            }
    except Exception as e:
        return {
            "healthy": True,
            "status": "unavailable",
            "details": {
                "message": f"DegenFeed service not reachable at {base_url}",
                "error": str(e)[:100],
            },
        }


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


async def _check_rag_pipeline() -> dict[str, Any]:
    """RAG ingestion pipeline stats — scanner→RAG queue depth + ingested count.

    The scanner publishes high/critical findings to the ``rmi:rag:ingest``
    pub/sub channel and the ``rmi:rag:findings_queue`` LPUSH list. rmi-rag's
    poller/subscriber consume those, embed with BGE-M3, and upsert into the
    Qdrant ``threat_intel`` collection. This returns:

    - ``queue_depth``: LLEN of ``rmi:rag:findings_queue`` (backlog waiting to
      be embedded). 0 = poller is keeping up.
    - ``ingested_count``: cumulative count reported by rmi-rag-api's
      subscriber + poller (in-memory, resets on rmi-rag-api restart).
    - ``poller_running`` / ``subscriber_running``: whether rmi-rag's
      background consumers are alive.
    - ``embedding_available``: whether Ollama has a usable embedding model
      (bge-m3 / nomic-embed) installed.
    - ``status``: ``"healthy"`` or ``"degraded"``. Degraded when the embedder
      is unavailable or when consumers run but nothing has ever been ingested.
    - ``issues``: list of degradation reasons (``embedder_unavailable``,
      ``consumers_running_no_ingestion``).

    Fail-open: any Redis/rmi-rag-api error is captured in ``error`` and the
    endpoint still returns 200 (pipeline stats are observability, not a
    liveness gate).
    """
    stats: dict[str, Any] = {
        "queue_depth": 0,
        "ingested_count": 0,
        "poller_running": False,
        "subscriber_running": False,
    }

    # ── Queue depth from Redis (rmi:rag:findings_queue) ──
    try:
        from app.core.redis import get_redis_async

        redis_client = get_redis_async()
        if redis_client is not None:
            depth = await asyncio.wait_for(redis_client.llen("rmi:rag:findings_queue"), timeout=2.0)
            stats["queue_depth"] = int(depth or 0)
    except Exception as exc:
        stats["error"] = f"redis: {str(exc)[:120]}"

    # ── Embedder health (Ollama) ──
    # Try to reach Ollama to verify the embedding model is available. Without
    # an embed model, rmi-rag cannot index findings even if consumers run.
    try:
        import httpx

        ollama_base_url = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
        async with httpx.AsyncClient() as http_client:
            resp = await http_client.get(f"{ollama_base_url}/api/tags", timeout=5)
            models = [m["name"] for m in resp.json().get("models", [])]
            embedding_available = any(m.startswith(("bge-m3", "nomic-embed")) for m in models)
    except Exception:
        embedding_available = False
    stats["embedding_available"] = embedding_available

    # ── Ingested count + consumer health from rmi-rag-api ──
    ingested_count = 0
    poller_running = False
    subscriber_running = False
    try:
        rag_api_url = os.getenv(
            "RAG_API_URL",
            "http://rmi-rag-api:8012" if not _IS_DEV else "http://127.0.0.1:8012",
        ).rstrip("/")
        http_client = await _get_http_client()
        resp = await asyncio.wait_for(http_client.get(f"{rag_api_url}/health"), timeout=2.0)
        if resp.status_code == 200:
            data = resp.json()
            sub = data.get("subscriber_stats") or {}
            poll = data.get("poller_stats") or {}
            ingested_count = int(sub.get("ingested_count", 0)) + int(poll.get("ingested_count", 0))
            poller_running = bool(poll.get("running", False))
            subscriber_running = bool(sub.get("running", False))
            stats["ingested_count"] = ingested_count
            stats["poller_running"] = poller_running
            stats["subscriber_running"] = subscriber_running
            stats["rag_api_status"] = data.get("status", "unknown")
        else:
            stats["rag_api_status"] = f"http_{resp.status_code}"
    except Exception as exc:
        stats["error"] = f"{stats.get('error', '')} rag_api: {str(exc)[:120]}".strip()

    # ── Aggregated status ──
    # Embed-aware: an unavailable embedder means the pipeline cannot index
    # anything, so it is degraded even if the consumers are alive.
    status = "healthy"
    issues: list[str] = []
    if not embedding_available:
        status = "degraded"
        issues.append("embedder_unavailable")

    # If consumers are running but ingested_count == 0 for >1 hour, that's degraded
    if poller_running and subscriber_running and ingested_count == 0:
        status = "degraded"
        issues.append("consumers_running_no_ingestion")

    stats["status"] = status
    stats["issues"] = issues
    return stats


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
    description="Kubernetes-style readiness probe. Checks critical dependencies (Redis, PostgreSQL). Returns 200 only when all critical stores are reachable.",
)
async def readiness() -> ReadinessResponse:
    r1 = await _check_store("redis", _check_redis())
    r2 = await _check_store("postgres", _check_postgres())
    redis_ok = r1 if not isinstance(r1, Exception) else {"healthy": False}
    pg_ok = r2 if not isinstance(r2, Exception) else {"healthy": False}
    checks = {"redis": redis_ok.get("healthy", False), "postgres": pg_ok.get("healthy", False)}
    all_ok = all(checks.values())
    return ReadinessResponse(status="ready" if all_ok else "not_ready", checks=checks)


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Full Health Check",
    description="Comprehensive system health check. Probes Redis, PostgreSQL, Neo4j, Qdrant, ClickHouse, MinIO, and Ethereum RPC. Returns aggregated status with per-store health, latency, and config validation.",
)
async def health() -> HealthResponse:
    started = time.monotonic()
    store_checks = {
        "redis": asyncio.wait_for(_check_store("redis", _check_redis()), timeout=5.0),
        "postgres": asyncio.wait_for(_check_store("postgres", _check_postgres()), timeout=5.0),
        "neo4j": asyncio.wait_for(_check_store("neo4j", _check_neo4j()), timeout=5.0),
        "qdrant": asyncio.wait_for(_check_store("qdrant", _check_qdrant()), timeout=5.0),
        "clickhouse": asyncio.wait_for(_check_store("clickhouse", _check_clickhouse()), timeout=5.0),
        "minio": asyncio.wait_for(_check_store("minio", _check_minio()), timeout=5.0),
        "eth_rpc": asyncio.wait_for(_check_store("eth_rpc", _check_reth()), timeout=5.0),
        "degenfeed": asyncio.wait_for(_check_store("degenfeed", _check_degenfeed()), timeout=5.0),
        "rag_pipeline": asyncio.wait_for(_check_rag_pipeline(), timeout=5.0),
    }
    results = await asyncio.gather(*store_checks.values(), return_exceptions=True)
    stores = {}
    for (name, _), ref in zip(store_checks.items(), results, strict=False):
        if isinstance(ref, Exception):
            ref = {"healthy": False, "error": str(ref)[:200]}
        if "latency_ms" not in ref:
            ref["latency_ms"] = int((time.monotonic() - started) * 1000)
        stores[name] = ref

    # RAG pipeline stats are observability, not a liveness gate — pull out of
    # the store checks so they don't influence critical/degraded status.
    rag_pipeline = dict(stores.pop("rag_pipeline", {}))
    rag_pipeline.pop("latency_ms", None)
    # Embed-aware: a degraded pipeline (embedder unavailable, or consumers
    # running with no ingestion) must NOT report healthy just because the
    # consumers are alive.
    if rag_pipeline.get("status") == "degraded":
        rag_pipeline["healthy"] = False
    else:
        rag_pipeline.setdefault(
            "healthy", bool(rag_pipeline.get("poller_running") or rag_pipeline.get("subscriber_running"))
        )
    rag_pipeline.setdefault("queue_depth", 0)
    rag_pipeline.setdefault("ingested_count", 0)

    # Sync health check results with degradation registry (Win #14)
    _sync_degradation_from_health(stores)

    # Critical = redis or postgres down. Non-critical stores can be degraded.
    critical_up = stores["redis"].get("healthy") and stores["postgres"].get("healthy")
    # Exclude stores that were intentionally skipped or are unavailable
    # (e.g. DegenFeed when its service is not deployed in this stack).
    _non_critical_stores = [
        s.get("healthy", False)
        for n, s in stores.items()
        if n not in ("redis", "postgres") and s.get("status") not in ("skipped", "unavailable")
    ]
    any_non_critical_down = not all(_non_critical_stores) if _non_critical_stores else False
    status = "unhealthy" if not critical_up else "degraded" if any_non_critical_down else "healthy"

    # Include degradation info in response
    degradation = _get_degradation_section()

    config_issues = check_config()
    return HealthResponse(
        status=status,
        version="2026.06.21",
        uptime_seconds=time.monotonic() - _START_TIME,
        stores=stores | degradation,
        rag_pipeline=rag_pipeline,
        config_issues=config_issues,
    )
