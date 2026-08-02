"""Unit tests for RAG pipeline stats in the health route.

Covers ``app.core.health_route._check_rag_pipeline`` — the observability
block added to ``GET /health`` that reports scanner→RAG queue depth and
ingested count from rmi-rag-api.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_check_rag_pipeline_fail_open_when_redis_down(monkeypatch) -> None:
    """Redis/rmi-rag-api errors must not raise — health stays 200."""
    from app.core import health_route

    async def _fake_redis_none():
        return None

    monkeypatch.setattr(health_route, "_get_http_client", _fake_redis_none)

    # Simulate Redis client being unavailable (get_redis_async returns None)
    import app.core.redis as redis_mod

    monkeypatch.setattr(redis_mod, "get_redis_async", lambda: None)

    stats = await health_route._check_rag_pipeline()

    assert isinstance(stats, dict)
    assert "queue_depth" in stats
    assert "ingested_count" in stats
    assert "poller_running" in stats
    assert "subscriber_running" in stats
    # No exception propagated — fail-open contract
    assert stats["queue_depth"] == 0


@pytest.mark.asyncio
async def test_check_rag_pipeline_reports_health_from_rag_api(monkeypatch) -> None:
    """When rmi-rag-api responds, ingested_count and consumer flags are surfaced."""
    from app.core import health_route

    class _FakeResponse:
        status_code = 200

        def json(self) -> dict:
            return {
                "status": "ok",
                "subscriber_stats": {"running": True, "ingested_count": 2},
                "poller_stats": {"running": True, "ingested_count": 5},
            }

    class _FakeClient:
        async def get(self, url: str) -> _FakeResponse:
            return _FakeResponse()

    async def _fake_http_client() -> _FakeClient:
        return _FakeClient()

    monkeypatch.setattr(
        health_route,
        "_get_http_client",
        _fake_http_client,
    )

    stats = await health_route._check_rag_pipeline()

    assert stats["ingested_count"] == 7
    assert stats["poller_running"] is True
    assert stats["subscriber_running"] is True
    assert stats["rag_api_status"] == "ok"
