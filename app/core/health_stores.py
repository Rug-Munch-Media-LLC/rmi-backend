"""Dependency (store) health probes for the /health + /ready endpoints.

Split out of ``app.core.health_route`` (de-godding, behavior-preserving).
Each probe returns ``{"healthy": bool, ...}`` and never raises — failures
are captured as ``{"healthy": False, "error": ...}`` so the health
endpoints aggregate instead of crashing.

The RAG pipeline probe lives in ``health_rag.py``; URL/env resolution and
config validation live in ``health_env.py``; the endpoints + aggregation
stay in ``health_route.py``.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import TYPE_CHECKING, Any

from app.core.health_env import (
    _DATABASE_URL_DEV,
    _DATABASE_URL_DOCKER,
    _IS_DEV,
    _NEO4J_URI_DEV,
    _NEO4J_URI_DOCKER,
    CLICKHOUSE_URL,
    MINIO_URL,
    QDRANT_URL,
    _get_url,
)
from app.core.logging import get_logger

logger = get_logger("health_stores")

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
    from collections.abc import Awaitable

    import httpx

_SHARED_HTTP_CLIENT: httpx.AsyncClient | None = None


async def _get_http_client() -> httpx.AsyncClient:
    global _SHARED_HTTP_CLIENT
    if _SHARED_HTTP_CLIENT is None:
        import httpx

        _SHARED_HTTP_CLIENT = httpx.AsyncClient(timeout=5.0)
    return _SHARED_HTTP_CLIENT


# ── Store check helpers ─────────────────────────────────────────


async def _check_store(name: str, coro: Awaitable[dict[str, Any]]) -> dict[str, Any]:
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
    # MinIO is an optional store (B2 is the primary object store). When not
    # deployed, mark as "unavailable" so it doesn't trigger degraded status.
    if not os.getenv("MINIO_URL"):
        return {
            "healthy": False,
            "status": "unavailable",
            "details": {"reason": "MINIO_URL not set — B2 is the object store"},
        }
    try:
        client = await _get_http_client()
        resp = await client.get(f"{MINIO_URL.rstrip(chr(47))}/minio/health/live")
        return {"healthy": resp.status_code == 200, "details": {"status_code": resp.status_code}}
    except Exception as e:
        return {"healthy": False, "status": "unavailable", "error": str(e)[:200]}


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


async def _check_phoenix() -> dict[str, Any]:
    """Probe Phoenix (Arize) RAG observability health.

    Returns ``healthy: True`` with ``status: "unavailable"`` when Phoenix
    is not configured or not reachable, so the overall health endpoint
    does not mark the system ``degraded`` just because an optional
    observability backend is absent. Phoenix is observability — not a
    liveness gate.
    """
    if os.getenv("PHOENIX_ENABLED", "false").lower() not in (
        "true",
        "1",
        "yes",
        "on",
    ):
        return {"healthy": True, "status": "skipped"}

    base_url = _get_url(
        "PHOENIX_UI_URL",
        docker="http://phoenix:6006",
        local="http://127.0.0.1:6006",
    )
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base_url.rstrip(chr(47))}/health")
        return {
            "healthy": resp.status_code == 200,
            "details": {"status_code": resp.status_code, "ui_url": base_url},
        }
    except Exception as e:
        return {
            "healthy": True,
            "status": "unavailable",
            "details": {
                "message": f"Phoenix not reachable at {base_url}",
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
    except (AttributeError, ImportError) as e:
        logger.debug(f"_sync_degradation_from_health failed: {e}")


def _get_degradation_section() -> dict[str, Any]:
    """Win #14: Get degradation section for health endpoint."""
    try:
        from app.core.degradation import get_degradation_health

        return {"degradation": get_degradation_health()}
    except (AttributeError, ImportError):
        return {}
