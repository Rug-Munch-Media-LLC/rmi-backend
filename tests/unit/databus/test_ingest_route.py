"""Tests for the canonical DataBus ingest route (service-key auth).

Covers the case-by-case service unblock model: internal services present
X-RMI-Key; anonymous or invalid-key writes are rejected with 401.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.databus.router.ingest import get_rate_limit_dependency, router


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Minimal FastAPI app mounting only the ingest router."""
    monkeypatch.setenv("RMI_INTERNAL_KEY", "test-secret-123")
    # Reload the key singleton so tests don't inherit the dev placeholder.
    import app.core.internal_key as ik

    monkeypatch.setattr(ik, "INTERNAL_API_KEY", "test-secret-123", raising=False)
    import app.databus.router.ingest as ingest_mod

    monkeypatch.setattr(ingest_mod, "INTERNAL_API_KEY", "test-secret-123", raising=False)

    application = FastAPI()
    application.include_router(router)

    # Neutral rate limiter (no redis in unit tests)
    application.dependency_overrides[get_rate_limit_dependency("databus_ingest", rate=10.0, burst=20)] = lambda: None
    return application


@pytest.fixture
def mock_session() -> AsyncMock:
    session = AsyncMock()
    session.refresh.side_effect = lambda obj: setattr(obj, "id", 42)
    return session


class TestIngestAuth:
    @pytest.mark.asyncio
    async def test_missing_key_rejected_401(self, app: FastAPI):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            resp = await ac.post(
                "/ingest",
                json={"source": "rugmaps", "data_type": "wallet_cluster", "content": {"a": 1}},
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_wrong_key_rejected_401(self, app: FastAPI):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            resp = await ac.post(
                "/ingest",
                headers={"X-RMI-Key": "wrong"},
                json={"source": "rugmaps", "data_type": "wallet_cluster", "content": {"a": 1}},
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_key_accepted_202(self, app: FastAPI, mock_session: Any):
        from app.core.database import get_db

        app.dependency_overrides[get_db] = lambda: mock_session
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            resp = await ac.post(
                "/ingest",
                headers={"X-RMI-Key": "test-secret-123"},
                json={
                    "source": "rugmaps",
                    "data_type": "wallet_cluster",
                    "content": {"risk_score": 72.3},
                    "token_address": "0xabc",
                    "chain": "ethereum",
                },
            )
        assert resp.status_code == 202
        body = resp.json()
        assert body["success"] is True
        assert body["id"] == 42
        mock_session.add.assert_called_once()
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_payload_validation_requires_content(self, app: FastAPI):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            resp = await ac.post(
                "/ingest",
                headers={"X-RMI-Key": "test-secret-123"},
                json={"source": "rugmaps", "data_type": "wallet_cluster"},
            )
        assert resp.status_code == 422
