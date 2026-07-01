"""Health routes — /health, /live, /ready.

Per RMIV5 v4.0 §T33. Provides basic Kubernetes-style health endpoints:
  - /health  — full health (deep checks)
  - /live    — liveness (process alive, no deps)
  - /ready   — readiness (critical deps reachable)

Each returns a JSON body with status + per-store details.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Response shape for /health endpoint."""

    status: str  # "healthy" | "degraded" | "unhealthy"
    version: str
    uptime_seconds: float
    stores: dict[str, dict[str, Any]]


class LivenessResponse(BaseModel):
    """Response for /live — process is alive."""

    status: str = "alive"


class ReadinessResponse(BaseModel):
    """Response for /ready — process can serve traffic."""

    status: str  # "ready" | "not_ready"
    checks: dict[str, bool]


_START_TIME = time.monotonic()


async def _check_redis() -> dict[str, Any]:
    """Check Redis connectivity (PING)."""
    try:
        from app.core.redis import get_redis

        r = get_redis()
        await asyncio.wait_for(r.ping(), timeout=2.0)
        return {"healthy": True}
    except Exception as e:
        return {"healthy": False, "error": str(e)[:200]}


async def _check_postgres() -> dict[str, Any]:
    """Check Postgres connectivity (SELECT 1)."""
    try:
        from app.core.db_pool import get_pg_pool

        pool = get_pg_pool()
        async with pool.acquire() as conn:
            await asyncio.wait_for(conn.fetchval("SELECT 1"), timeout=2.0)
        return {"healthy": True}
    except Exception as e:
        return {"healthy": False, "error": str(e)[:200]}

async def _check_neo4j() -> dict[str, Any]:
    """Check Neo4j connectivity (RETURN 1 + node count)."""
    try:
        from app.core.neo4j_connection import _driver
        if _driver is None:
            return {"healthy": False, "error": "Neo4j driver not initialized"}
        async with _driver.session() as session:
            result = await asyncio.wait_for(
                session.run("RETURN 1"),
                timeout=3.0,
            )
            val = await result.single()
            node_count_result = await session.run("MATCH (n) RETURN count(n)")
            node_count = await node_count_result.single()
        return {
            "healthy": val[0] == 1,
            "details": {"node_count": node_count["count(n)"] if node_count else 0},
        }
    except Exception as e:
        return {"healthy": False, "error": str(e)[:200]}


async def _check_qdrant() -> dict[str, Any]:
    """Check Qdrant connectivity (GET /collections + collection count)."""
    try:
        import httpx
        qdrant_url = "http://localhost:6333/collections"
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(qdrant_url)
            data = resp.json()
            collections = data.get("result", {}).get("collections", [])
        return {
            "healthy": resp.status_code == 200,
            "details": {"collection_count": len(collections)},
        }
    except Exception as e:
        return {"healthy": False, "error": str(e)[:200]}


async def _check_clickhouse() -> dict[str, Any]:
    """Check ClickHouse connectivity (SELECT 1 + rmi DB exists)."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.post(
                "http://localhost:8123/",
                content="SELECT 1",
            )
            db_resp = await client.post(
                "http://localhost:8123/",
                content="SHOW DATABASES",
            )
            dbs = db_resp.text.strip().split("\n") if db_resp.status_code == 200 else []
        return {
            "healthy": resp.text.strip() == "1",
            "details": {"rmi_db_exists": "rmi" in dbs, "databases": dbs},
        }
    except Exception as e:
        return {"healthy": False, "error": str(e)[:200]}


async def _check_minio() -> dict[str, Any]:
    """Check MinIO connectivity (health endpoint)."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get("http://localhost:9000/minio/health/live")
        return {
            "healthy": resp.status_code == 200,
            "details": {"status_code": resp.status_code},
        }
    except Exception as e:
        return {"healthy": False, "error": str(e)[:200]}


async def _check_reth() -> dict[str, Any]:
    """Check Reth (Ethereum node) connectivity (eth_blockNumber)."""
    try:

        import httpx
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.post(
                "http://localhost:8545",
                json={"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1},
            )
            data = resp.json()
            block_hex = data.get("result", "0x0")
            block_num = int(block_hex, 16) if block_hex.startswith("0x") else 0
        return {
            "healthy": block_num > 0,
            "details": {"block_number": block_num},
        }
    except Exception as e:
        return {"healthy": False, "error": str(e)[:200]}




@router.get("/live", response_model=LivenessResponse)
async def liveness() -> LivenessResponse:
    """Liveness probe — confirms the process is running.

    Used by Kubernetes/load balancers to decide whether to restart
    the container. Does NOT check dependencies.
    """
    return LivenessResponse(status="alive")


@router.get("/ready", response_model=ReadinessResponse)
async def readiness() -> ReadinessResponse:
    """Readiness probe — confirms critical deps are reachable.

    Used to decide whether to send traffic. Fails fast (2s per check).
    """
    redis_ok, pg_ok = await asyncio.gather(
        _check_redis(),
        _check_postgres(),
        return_exceptions=True,
    )
    # Normalize exceptions
    if isinstance(redis_ok, Exception):
        redis_ok = {"healthy": False, "error": str(redis_ok)[:200]}
    if isinstance(pg_ok, Exception):
        pg_ok = {"healthy": False, "error": str(pg_ok)[:200]}
    checks = {
        "redis": bool(redis_ok.get("healthy")),
        "postgres": bool(pg_ok.get("healthy")),
    }
    all_ok = all(checks.values())
    return ReadinessResponse(
        status="ready" if all_ok else "not_ready",
        checks=checks,
    )


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Full health check — deep validation of every store.

    Returns per-store health with latency. Slower than /ready but
    gives ops full visibility.
    """
    started = time.monotonic()
    (redis_h, pg_h, neo4j_h, qdrant_h, clickhouse_h, minio_h, reth_h) = await asyncio.gather(
        _check_redis(),
        _check_postgres(),
        _check_neo4j(),
        _check_qdrant(),
        _check_clickhouse(),
        _check_minio(),
        _check_reth(),
        return_exceptions=True,
    )
    for ref in [redis_h, pg_h, neo4j_h, qdrant_h, clickhouse_h, minio_h, reth_h]:
        if isinstance(ref, Exception):
            ref = {"healthy": False, "error": str(ref)[:200]}
        if "latency_ms" not in ref:
            ref["latency_ms"] = int((time.monotonic() - started) * 1000)

    stores = {
        "redis": redis_h,
        "postgres": pg_h,
        "neo4j": neo4j_h,
        "qdrant": qdrant_h,
        "clickhouse": clickhouse_h,
        "minio": minio_h,
        "reth": reth_h,
    }
    critical_down = not (redis_h.get("healthy") and pg_h.get("healthy"))
    any_down = not all(s.get("healthy", False) for s in stores.values())
    status = "unhealthy" if critical_down else "degraded" if any_down else "healthy"

    return HealthResponse(
        status=status,
        version="2026.06.21",
        uptime_seconds=time.monotonic() - _START_TIME,
        stores=stores,
    )
