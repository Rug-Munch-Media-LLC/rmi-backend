"""HTTP contract for /api/v1/boosts — mounted standalone, no full app import."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db
from app.domains.boost.create import create_boost
from app.domains.boost.expiry import expire_due_boosts
from app.domains.boost.schemas import BoostCreateRequest
from app.routers.v1.boosts import get_verifier, router
from tests.unit.domains.boost.helpers import (
    EVM_TOKEN,
    NOW,
    SOL_TOKEN,
    RecordingVerifier,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
async def api(session: AsyncSession, verifier: RecordingVerifier):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def _override_db():
        yield session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_verifier] = lambda: verifier
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


def _payload(**overrides):
    body = {
        "chain": "solana",
        "token_address": SOL_TOKEN,
        "plan_id": "72h",
        "payment_ref": "ref-" + "p" * 20,
        "payment_provider": "x402",
    }
    body.update(overrides)
    return body


async def test_plans_expose_currency_and_tiers(api) -> None:
    resp = await api.get("/api/v1/boosts/plans")
    assert resp.status_code == 200
    body = resp.json()
    assert body["currency"] == "USD"
    assert [p["plan_id"] for p in body["plans"]] == ["24h", "72h", "7d"]
    assert float(body["plans"][1]["price_amount"]) == 119.0


async def test_create_then_list_boosted(api) -> None:
    created = await api.post("/api/v1/boosts", json=_payload())
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "active"

    listed = await api.get("/api/v1/boosts/boosted", params={"chain": "SOL"})
    assert listed.status_code == 200
    body = listed.json()
    assert body["chain"] == "solana"
    assert body["count"] == 1
    assert body["items"][0]["token_address"] == SOL_TOKEN


async def test_boosted_is_chain_scoped(api) -> None:
    await api.post("/api/v1/boosts", json=_payload())
    other = await api.get("/api/v1/boosts/boosted", params={"chain": "base"})
    assert other.json()["count"] == 0


async def test_duplicate_payment_ref_is_conflict(api) -> None:
    assert (await api.post("/api/v1/boosts", json=_payload())).status_code == 201
    dup = await api.post("/api/v1/boosts", json=_payload(chain="base", token_address=EVM_TOKEN))
    assert dup.status_code == 409
    assert "payment reference" in dup.json()["detail"]


async def test_denied_entitlement_is_402(session) -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def _override_db():
        yield session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_verifier] = lambda: RecordingVerifier(deny=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/v1/boosts", json=_payload())
    assert resp.status_code == 402


async def test_invalid_chain_and_token_are_422(api) -> None:
    assert (await api.post("/api/v1/boosts", json=_payload(chain="dogechain"))).status_code == 422
    assert (await api.post("/api/v1/boosts", json=_payload(token_address="0x123"))).status_code == 422
    assert (await api.post("/api/v1/boosts", json=_payload(plan_id="1y"))).status_code == 422


async def test_expire_requires_internal_key(api) -> None:
    resp = await api.post("/api/v1/boosts/expire")
    assert resp.status_code == 403


async def test_expired_boost_leaves_boosted_list(api, session) -> None:
    await create_boost(
        session,
        BoostCreateRequest(**_payload()),
        RecordingVerifier(),
        now=NOW - timedelta(days=10),
    )
    assert await expire_due_boosts(session, now=NOW) == 1
    listed = await api.get("/api/v1/boosts/boosted", params={"chain": "solana"})
    assert listed.json()["count"] == 0
