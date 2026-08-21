"""Tests for central health/readiness probes (win 7)."""

from __future__ import annotations

import httpx
import pytest


@pytest.fixture
def app():  # type: ignore[no-untyped-def]
    import os

    os.environ.setdefault("JWT_SECRET", "test-jwt-secret-key-thats-32-chars-long!")
    from app.factory import create_app

    return create_app()


@pytest.mark.asyncio
async def test_healthz_liveness_alive_without_dependency_checks(app):  # type: ignore[no-untyped-def]
    """Liveness never probes dependencies — a down DB must not fail /healthz."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "alive"}


@pytest.mark.asyncio
async def test_readyz_shape_and_status(app):  # type: ignore[no-untyped-def]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/readyz")
    assert r.status_code in (200, 503)  # healthy or degraded — never a crash
    body = r.json()
    assert body["status"] in ("healthy", "degraded")
    for dep in ("database", "redis", "qdrant"):
        assert dep in body["dependencies"]
        entry = body["dependencies"][dep]
        assert {"ok", "latency_ms", "error"} <= set(entry)


@pytest.mark.asyncio
async def test_probe_result_never_raises_on_garbage_url(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """A broken dependency URL degrades to ok=False, not an exception."""
    from app.core import health

    monkeypatch.setattr(health.settings, "redis_url", "redis://127.0.0.1:1/0")
    out = await health.check_redis()
    assert out["ok"] is False
    assert out["error"]


@pytest.mark.asyncio
async def test_qdrant_probe_skips_when_unconfigured(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    from app.core import health

    monkeypatch.setattr(health.settings, "qdrant_url", "", raising=False)
    out = await health.check_qdrant()
    assert out["ok"] is True
    assert out.get("skipped")


def test_register_health_routes_is_idempotent_safe(app):  # type: ignore[no-untyped-def]
    """Registering twice must not explode (factory re-entry safety)."""
    from app.core.health import register_health_routes

    register_health_routes(app)  # duplicate registration tolerated by FastAPI
