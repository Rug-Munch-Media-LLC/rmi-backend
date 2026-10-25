"""DEBT-51: deprecated caching_shield limiter removed; unified limiter is canonical."""

from __future__ import annotations

import importlib
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.rate_limiter import UnifiedRateLimiter, get_rate_limiter


def test_deprecated_module_is_gone() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.caching_shield.rate_limiter")


def test_router_binds_canonical_limiter() -> None:
    mod = importlib.import_module("app.caching_shield.router")
    assert mod.get_rate_limiter is get_rate_limiter


@pytest.mark.asyncio
async def test_over_limit_request_is_rejected() -> None:
    limiter = UnifiedRateLimiter()
    key = f"debt51:{uuid.uuid4().hex}"
    first, _ = await limiter.acquire(key, rate=0.0001, burst=1)
    second, headers = await limiter.acquire(key, rate=0.0001, burst=1)
    assert first is True
    assert second is False
    assert "Retry-After" in headers


def test_cache_rate_limits_endpoint_serves_unified_stats() -> None:
    from app.caching_shield.router import router

    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        resp = client.get("/api/v1/cache/rate-limits")
    assert resp.status_code == 200
    body = resp.json()
    assert {"timestamp", "redis_available", "providers"} <= set(body)
