"""Health routes — /health, /live, /ready.

Per RMIV5 v4.0 §T33. Provides basic Kubernetes-style health endpoints:
  - /health  — full health (deep checks)
  - /live    — liveness (process alive, no deps)
  - /ready   — readiness (critical deps reachable)

Emits Prometheus HEALTH_CHECK_DURATION + HEALTH_CHECK_STATUS gauges per store.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.metrics import HEALTH_CHECK_DURATION, HEALTH_CHECK_STATUS

router = APIRouter(tags=["health"])


QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
CLICKHOUSE_URL = os.getenv("CLICKHOUSE_URL", "http://127.0.0.1:8123/")
MINIO_URL = os.getenv("MINIO_URL", "http://127.0.0.1:9000")
RETH_URL = os.getenv("RETH_URL", "http://127.0.0.1:8545")
_SHARED_HTTP_CLIENT: httpx.AsyncClient | None = None


async def _get_http_client() -> httpx.AsyncClient:
    global _SHARED_HTTP_CLIENT
    if _SHARED_HTTP_CLIENT is None:
        import httpx

        _SHARED_HTTP_CLIENT = httpx.AsyncClient(timeout=5.0)
    return _SHARED_HTTP_CLIENT


class HealthResponse(BaseModel):
    status: str
    version: str
    uptime_seconds: float
    stores: dict[str, dict[str, Any]]


class LivenessResponse(BaseModel):
    status: str = "alive"


class ReadinessResponse(BaseModel):
    status: str
    checks: dict[str, bool]


_START_TIME = time.monotonic()


async def _check_store(name: str, coro) -> dict[str, Any]:
    """Run a health check and emit Prometheus metrics."""
    start = time.perf_counter()
    try:
        result = await coro
        elapsed = time.perf_counter() - start
        healthy = result.get("healthy", False)
        HEALTH_CHECK_DURATION.labels(store=name).set(elapsed)
        HEALTH_CHECK_STATUS.labels(store=name).set(1 if healthy else 0)
        return result
    except Exception as e:
        elapsed = time.perf_counter() - start
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

        conn = await asyncpg.connect(
            os.getenv("DATABASE_URL", "postgresql://rmi:***@rmi-postgres:5432/rmi")
        )
        await asyncio.wait_for(conn.fetchval("SELECT 1"), timeout=5.0)
        await conn.close()
        return {"healthy": True}
    except Exception as e:
        return {"healthy": False, "error": str(e)[:200]}


async def _check_neo4j() -> dict[str, Any]:
    try:
        from app.core.neo4j_connection import _driver

        if _driver is None:
            return {"healthy": False, "error": "Neo4j driver not initialized"}
        async with _driver.session() as session:
            result = await asyncio.wait_for(session.run("RETURN 1"), timeout=3.0)
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
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{QDRANT_URL.rstrip(chr(47))}/collections")
            data = resp.json()
            collections = data.get("result", {}).get("collections", [])
        return {
            "healthy": resp.status_code == 200,
            "details": {"collection_count": len(collections)},
        }
    except Exception as e:
        return {"healthy": False, "error": str(e)[:200]}


async def _check_clickhouse() -> dict[str, Any]:
    try:
        import base64
        import os

        user = os.getenv("CLICKHOUSE_USER", "rmi")
        password = os.getenv("CLICKHOUSE_PASSWORD", os.getenv("CLICKHOUSE_PASS", ""))
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        headers = {"Authorization": f"Basic {token}"}
        client = await _get_http_client()
        resp = await client.post("http://rmi-clickhouse:8123/", content="SELECT 1", headers=headers)
        db_resp = await client.post(
            "http://rmi-clickhouse:8123/", content="SHOW DATABASES", headers=headers
        )
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
        resp = await client.get("http://rmi-minio:9000/minio/health/live")
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


@router.get("/live", response_model=LivenessResponse)
async def liveness() -> LivenessResponse:
    return LivenessResponse(status="alive")


@router.get("/ready", response_model=ReadinessResponse)
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
        "redis": _check_store("redis", _check_redis()),
        "postgres": _check_store("postgres", _check_postgres()),
        "neo4j": _check_store("neo4j", _check_neo4j()),
        "qdrant": _check_store("qdrant", _check_qdrant()),
        "clickhouse": _check_store("clickhouse", _check_clickhouse()),
        "minio": _check_store("minio", _check_minio()),
        "eth_rpc": _check_store("eth_rpc", _check_reth()),
    }
    results = await asyncio.gather(*store_checks.values(), return_exceptions=True)
    stores = {}
    for (name, _), ref in zip(store_checks.items(), results, strict=False):
        if isinstance(ref, Exception):
            ref = {"healthy": False, "error": str(ref)[:200]}
        if "latency_ms" not in ref:
            ref["latency_ms"] = int((time.monotonic() - started) * 1000)
        stores[name] = ref

    critical_down = not (stores["redis"].get("healthy") and stores["postgres"].get("healthy"))
    any_down = not all(s.get("healthy", False) for s in stores.values())
    status = "unhealthy" if critical_down else "degraded" if any_down else "healthy"

    return HealthResponse(
        status=status,
        version="2026.06.21",
        uptime_seconds=time.monotonic() - _START_TIME,
        stores=stores,
    )
