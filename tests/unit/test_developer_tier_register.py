"""Tests for developer tier /register — regression for the create_api_key
call-arg mismatch (was: positional args to a single-request signature,
no await, and dict-indexing a Pydantic model)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    from app.routers import developer_tier

    # No real Redis in unit tests — endpoint tolerates None (skips dup check)
    monkeypatch.setattr(developer_tier, "get_redis", lambda: None)

    app = FastAPI()
    app.include_router(developer_tier.router)
    return TestClient(app)


def test_register_returns_key_and_tier(client) -> None:
    resp = client.post(
        "/api/v1/developer/register",
        json={"email": "dev@example.com", "tier": "pro", "wallet_address": "0xabc123"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["api_key"].startswith("rmi_")
    assert body["tier"] == "pro"
    assert body["daily_limit"] == 5000  # pro tier
    assert body["rate_limit_per_min"] == 120


def test_register_default_tier_free(client) -> None:
    resp = client.post(
        "/api/v1/developer/register",
        json={"email": "dev2@example.com"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["tier"] == "free"
    assert body["daily_limit"] == 100


def test_register_invalid_tier_rejected(client) -> None:
    resp = client.post(
        "/api/v1/developer/register",
        json={"email": "dev3@example.com", "tier": "bogus"},
    )
    assert resp.status_code == 400
    assert "Invalid tier" in resp.json()["error"]
