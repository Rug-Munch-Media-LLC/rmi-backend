"""Characterization tests for ``app.core.health_route`` (de-godding guard).

Pins the OBSERVABLE behavior of the /health, /live, /ready endpoints and the
module's public import surface so the structural decomposition into
``health_env`` / ``health_stores`` / ``health_rag`` stays behavior-preserving:

- endpoint paths, status aggregation rules, and response schemas are frozen;
- every name that external code imports from ``app.core.health_route``
  (mount.py, app/routers/v1/health.py, app/routers/news_helpers.py, and the
  benchmark/schemathesis test suites that PATCH these attributes) stays
  importable from the SAME module path;
- patching ``app.core.health_route._check_*`` must still affect the
  ``/health`` and ``/ready`` handlers (call-time global resolution contract
  relied on by tests/benchmark/* and tests/test_api_schemathesis.py).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.core.health_route as health_route

# Every name external importers rely on (see app/routers/v1/health.py,
# app/routers/news_helpers.py, tests/test_db.py, tests/test_routers.py,
# tests/benchmark/*, tests/test_api_schemathesis.py).
REQUIRED_SURFACE = [
    "router",
    "check_config",
    "HealthResponse",
    "LivenessResponse",
    "ReadinessResponse",
    "QDRANT_URL",
    "CLICKHOUSE_URL",
    "MINIO_URL",
    "RETH_URL",
    "_get_url",
    "_get_http_client",
    "_check_store",
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
    "_sync_degradation_from_health",
    "_get_degradation_section",
]

STORE_CHECK_NAMES = [
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
]


@pytest.fixture
def client(monkeypatch):
    # All store probes return healthy by default — no real I/O.
    async def _ok() -> dict[str, Any]:
        return {"healthy": True}

    for name in STORE_CHECK_NAMES:
        monkeypatch.setattr(health_route, name, _ok)

    app = FastAPI()
    app.include_router(health_route.router)
    with TestClient(app) as c:
        yield c


# ── Import surface (the shim contract) ────────────────────────────────


def test_public_surface_intact() -> None:
    for name in REQUIRED_SURFACE:
        assert hasattr(health_route, name), f"app.core.health_route lost '{name}'"


# ── Endpoint paths + schemas ──────────────────────────────────────────


def test_live_returns_alive(client) -> None:
    resp = client.get("/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "alive"}


def test_ready_when_critical_stores_up(client) -> None:
    resp = client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"redis": True, "postgres": True}


def test_not_ready_when_redis_down(client, monkeypatch) -> None:
    async def _down() -> dict[str, Any]:
        return {"healthy": False, "error": "redis down"}

    monkeypatch.setattr(health_route, "_check_redis", _down)
    body = client.get("/ready").json()
    assert body["status"] == "not_ready"
    assert body["checks"] == {"redis": False, "postgres": True}


def test_health_all_healthy(client) -> None:
    body = client.get("/health").json()
    assert body["status"] == "healthy"
    assert body["version"] == "2026.06.21"
    assert isinstance(body["uptime_seconds"], float)
    # Every real store is reported (rag_pipeline is pulled OUT of stores).
    for store in ("redis", "postgres", "neo4j", "qdrant", "clickhouse", "minio", "eth_rpc", "degenfeed", "phoenix"):
        assert store in body["stores"], f"missing store '{store}'"
        assert body["stores"][store]["healthy"] is True
        assert "latency_ms" in body["stores"][store]
    assert "rag_pipeline" not in body["stores"]
    assert isinstance(body["rag_pipeline"], dict)
    assert isinstance(body["config_issues"], list)


def test_health_unhealthy_when_redis_down(client, monkeypatch) -> None:
    async def _down() -> dict[str, Any]:
        return {"healthy": False, "error": "down"}

    monkeypatch.setattr(health_route, "_check_redis", _down)
    assert client.get("/health").json()["status"] == "unhealthy"


def test_health_unhealthy_when_postgres_down(client, monkeypatch) -> None:
    async def _down() -> dict[str, Any]:
        return {"healthy": False, "error": "down"}

    monkeypatch.setattr(health_route, "_check_postgres", _down)
    assert client.get("/health").json()["status"] == "unhealthy"


def test_health_degraded_when_non_critical_down(client, monkeypatch) -> None:
    async def _down() -> dict[str, Any]:
        return {"healthy": False, "error": "qdrant down"}

    monkeypatch.setattr(health_route, "_check_qdrant", _down)
    assert client.get("/health").json()["status"] == "degraded"


def test_health_not_degraded_by_skipped_optional_stores(client, monkeypatch) -> None:
    async def _skipped() -> dict[str, Any]:
        return {"healthy": True, "status": "skipped"}

    monkeypatch.setattr(health_route, "_check_degenfeed", _skipped)
    monkeypatch.setattr(health_route, "_check_phoenix", _skipped)
    assert client.get("/health").json()["status"] == "healthy"


def test_health_rag_pipeline_degraded_sets_unhealthy_flag_but_not_status(client, monkeypatch) -> None:
    """rag_pipeline degradation is observability — it must NOT drag the
    overall status down, but its own ``healthy`` flag must be False."""

    async def _rag_degraded() -> dict[str, Any]:
        return {"status": "degraded", "issues": ["embedder_unavailable"], "queue_depth": 3, "ingested_count": 0}

    monkeypatch.setattr(health_route, "_check_rag_pipeline", _rag_degraded)
    body = client.get("/health").json()
    assert body["status"] == "healthy"  # not a liveness gate
    assert body["rag_pipeline"]["healthy"] is False
    assert body["rag_pipeline"]["queue_depth"] == 3
    assert "latency_ms" not in body["rag_pipeline"]


def test_health_rag_pipeline_healthy_flag_from_consumers(client, monkeypatch) -> None:
    async def _rag_ok() -> dict[str, Any]:
        return {"status": "healthy", "poller_running": True, "subscriber_running": False}

    monkeypatch.setattr(health_route, "_check_rag_pipeline", _rag_ok)
    rag = client.get("/health").json()["rag_pipeline"]
    assert rag["healthy"] is True
    assert rag["queue_depth"] == 0
    assert rag["ingested_count"] == 0


# ── Patch-resolution contract (benchmarks/schemathesis rely on it) ───


def test_module_attribute_patch_reaches_health_handler(client, monkeypatch) -> None:
    """patching ``app.core.health_route._check_redis`` must change /health
    output — the handlers resolve check functions via module globals at
    call time."""
    calls: list[str] = []

    async def _spy() -> dict[str, Any]:
        calls.append("redis")
        return {"healthy": True}

    monkeypatch.setattr(health_route, "_check_redis", _spy)
    client.get("/health")
    assert calls == ["redis"]


# ── _check_store semantics ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_store_success_passthrough() -> None:
    async def _ok() -> dict[str, Any]:
        return {"healthy": True, "details": {"version": "1.0"}}

    result = await health_route._check_store("ok_store", _ok())
    assert result["healthy"] is True
    assert result["details"]["version"] == "1.0"


@pytest.mark.asyncio
async def test_check_store_exception_becomes_error_dict() -> None:
    async def _boom() -> dict[str, Any]:
        raise ConnectionError("Connection refused")

    result = await health_route._check_store("failing", _boom())
    assert result["healthy"] is False
    assert "Connection refused" in result["error"]
    assert len(result["error"]) <= 200


# ── check_config semantics ────────────────────────────────────────────


def test_check_config_flags_missing_and_placeholder(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://real-host:6379/0")
    monkeypatch.setenv("CLICKHOUSE_URL", "http://***@ch:8123/")
    issues = {i["key"]: i["severity"] for i in health_route.check_config()}
    assert issues["DATABASE_URL"] == "missing"
    assert issues["CLICKHOUSE_URL"] == "placeholder"
    assert "REDIS_URL" not in issues


# ── RAG pipeline wrapper honors the shim's _get_http_client patch ─────


@pytest.mark.asyncio
async def test_check_rag_pipeline_uses_module_get_http_client(monkeypatch) -> None:
    """The rag check must obtain its HTTP client via
    ``app.core.health_route._get_http_client`` so existing monkeypatches
    keep working after the split."""

    class _Resp:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "status": "ok",
                "subscriber_stats": {"running": True, "ingested_count": 1},
                "poller_stats": {"running": True, "ingested_count": 2},
            }

    class _Client:
        async def get(self, url: str) -> _Resp:
            return _Resp()

    async def _fake_client() -> _Client:
        return _Client()

    monkeypatch.setattr(health_route, "_get_http_client", _fake_client)
    stats = await health_route._check_rag_pipeline()
    assert stats["ingested_count"] == 3
    assert stats["rag_api_status"] == "ok"
