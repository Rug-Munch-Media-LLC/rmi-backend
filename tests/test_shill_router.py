"""Tests for the shill-campaign intelligence router (isolated, no app).

The router is mounted on a bare FastAPI app — no app startup, no database, no
network. Reads hit the bot's ``shill_evidence`` DB (read-only bridge) + the
reused wall-of-shame store and degrade to empty on failure; the profile probe
and Qdrant search fail open to ``None`` / ``[]`` by design.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.shill import router

if TYPE_CHECKING:
    from collections.abc import Generator

app = FastAPI()
app.include_router(router)


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as c:
        yield c


def test_health_shape(client: TestClient) -> None:
    resp = client.get("/api/v1/shill/health")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {
        "status",
        "apipass_key_configured",
        "apipass_calls_remaining",
        "evidence_count",
        "source",
    }
    assert body["status"] == "ok"
    assert isinstance(body["apipass_key_configured"], bool)
    assert body["source"] == "shill_service"


def test_campaigns_shape(client: TestClient) -> None:
    resp = client.get("/api/v1/shill/campaigns")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) >= {"campaigns", "total", "source"}
    assert isinstance(body["campaigns"], list)
    assert body["source"] == "shill_evidence"


def test_evidence_shape(client: TestClient) -> None:
    resp = client.get("/api/v1/shill/evidence")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"evidence", "total", "source"}
    assert isinstance(body["evidence"], list)
    assert isinstance(body["total"], int)


def test_feed_reuses_wall_of_shame(client: TestClient) -> None:
    # Deliberately reuses the existing kol wall-of-shame store — not a copy.
    resp = client.get("/api/v1/shill/feed")
    assert resp.status_code == 200
    body = resp.json()
    assert {"wall_of_shame", "total", "severity", "source"} <= set(body.keys())
    assert isinstance(body["wall_of_shame"], list)


def test_detect_campaigns_single_channel_not_a_campaign(client: TestClient) -> None:
    # 1 channel / 1 mention is NOT a coordinated campaign (needs >= 3 accounts).
    resp = client.post(
        "/api/v1/shill/campaigns/detect",
        json={
            "articles": [{"title": "PEPE", "entities": ["$PEPE"], "source_channel": "alpha", "url": "https://t.me/a"}]
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "shill_detector"
    assert body["campaigns"] == []
    assert body["total"] == 0


def test_probe_invalid_handle_is_none(client: TestClient) -> None:
    resp = client.get("/api/v1/shill/probe/   ")
    assert resp.status_code == 200
    assert resp.json() is None
