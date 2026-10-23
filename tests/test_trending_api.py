"""Contract tests for GET /api/v1/trending.

No database is required: the service boundary is monkeypatched. The live test
runs only when ``TRENDING_LIVE=1`` and a reachable ClickHouse is configured.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.public.trending import router
from app.trending.scoring import score_row

app = FastAPI()
app.include_router(router, prefix="/api/v1")
client = TestClient(app)


def _item(**overrides):
    base = {
        "chain": "solana",
        "token": "MintAddress111111111111111111111111111111",
        "symbol": "TEST",
        "name": "Test Token",
        "price": 0.0012,
        "market_cap": None,
        "liquidity": None,
        "price_change_pct": 12.5,
        "volume_usd": 84213.4,
        "trades": 311,
        "buys": 190,
        "sells": 121,
        "bonding_progress": None,
        "score": 71.4,
        "source": "dex_events",
        "last_event_at": datetime.now(UTC),
        "null_reasons": {
            "market_cap": "market_cap_not_indexed",
            "liquidity": "reserves_not_indexed",
            "bonding_progress": "not_a_launchpad_token",
        },
    }
    base.update(overrides)
    return base


@pytest.fixture
def payload():
    return {
        "window_hours": 24,
        "chain": None,
        "items": [_item()],
        "generated_at": datetime.now(UTC),
        "degraded": [],
        "source": "clickhouse",
    }


def test_response_contract(monkeypatch, payload):
    async def fake(chain, hours, limit):
        return payload

    monkeypatch.setattr("app.api.v1.public.trending.build_trending", fake)
    r = client.get("/api/v1/trending", params={"window": 24, "limit": 25})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) >= {"items", "generated_at", "window_hours", "degraded", "source"}
    item = body["items"][0]
    for field in (
        "chain",
        "token",
        "symbol",
        "price",
        "market_cap",
        "liquidity",
        "price_change_pct",
        "buys",
        "sells",
        "volume_usd",
        "bonding_progress",
        "score",
        "source",
    ):
        assert field in item, field
    # every null numeric field must carry a reason
    for field, value in item.items():
        if value is None:
            assert field in item["null_reasons"], field


def test_null_price_carries_reason():
    item = _item(price=None, null_reasons={"price": "price_series_unavailable"})
    assert item["null_reasons"]["price"] == "price_series_unavailable"


@pytest.mark.parametrize("window", [2, 48, 0])
def test_unsupported_window_rejected(window):
    assert client.get("/api/v1/trending", params={"window": window}).status_code == 422


def test_limit_cap_rejected():
    assert client.get("/api/v1/trending", params={"limit": 101}).status_code == 422


def test_unknown_chain_is_empty(monkeypatch):
    seen = {}

    async def fake(chain, hours, limit):
        seen["chain"] = chain
        return {
            "window_hours": hours,
            "chain": chain,
            "items": [],
            "generated_at": datetime.now(UTC),
            "degraded": [],
            "source": "empty",
        }

    monkeypatch.setattr("app.api.v1.public.trending.build_trending", fake)
    r = client.get("/api/v1/trending", params={"chain": "notachain"})
    assert r.status_code == 200, r.text
    assert r.json()["items"] == []
    assert seen["chain"] == "__unknown__"


def test_stablecoin_is_penalised():
    metrics = {"volume_usd": 90_000.0, "trades": 200, "traders": 60, "buys": 120}
    meme = score_row({"chain": "ethereum", "token": "0xdeadbeef", **metrics}, 24)
    usdc = score_row(
        {"chain": "ethereum", "token": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", **metrics},
        24,
    )
    wrapped = score_row(
        {"chain": "ethereum", "token": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", **metrics},
        24,
    )
    assert usdc < meme * 0.1
    assert wrapped < meme * 0.2
    assert meme > 0


def test_entity_label_penalised():
    metrics = {"volume_usd": 50_000.0, "trades": 100, "traders": 40, "buys": 50}
    clean = score_row({"chain": "ethereum", "token": "0xabc", **metrics}, 24)
    flagged = score_row(
        {"chain": "ethereum", "token": "0xabc", "entity": {"entity_type": "exchange"}, **metrics},
        24,
    )
    assert flagged < clean * 0.2


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("TRENDING_LIVE") != "1", reason="set TRENDING_LIVE=1")
def test_live_rows_across_three_chains():
    """Real rows from ClickHouse across at least three distinct chains."""
    import anyio

    from app.trending.service import build_trending

    result = anyio.run(build_trending, "", 24, 100)
    items = result["items"]
    assert result["degraded"] == []
    chains = {i["chain"] for i in items}
    assert len(chains) >= 3, chains
    assert all(i["token"] for i in items)
    assert all(0.0 <= i["score"] <= 100.0 for i in items)
