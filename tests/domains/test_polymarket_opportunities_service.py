"""Service + HTTP tests for GET /api/v1/polymarket/opportunities.

Upstream calls run through ``httpx.MockTransport`` using real Gamma field names,
so ranking, validation and degradation are exercised without network access.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx

from app.domains.prediction.opportunities import service as svc
from app.domains.prediction.opportunities.scoring import WEIGHTS


def _gamma_row(
    slug: str,
    condition_id: str,
    *,
    volume: float,
    liquidity: float,
    days: int,
    delta: float | None,
    question: str = "Will X happen?",
) -> dict:
    return {
        "conditionId": condition_id,
        "slug": slug,
        "question": question,
        "outcomes": json.dumps(["Yes", "No"]),
        "outcomePrices": json.dumps(["0.42", "0.58"]),
        "volumeNum": volume,
        "liquidityNum": liquidity,
        "endDate": (datetime.now(UTC) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "oneDayPriceChange": delta,
    }


def _handler(gamma_rows: list[dict], holders: list[dict] | None = None):
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "gamma-api.polymarket.com":
            assert request.url.params.get("active") == "true"
            assert request.url.params.get("closed") == "false"
            assert request.url.params.get("order") == "volumeNum"
            return httpx.Response(200, json=gamma_rows)
        if request.url.host == "data-api.polymarket.com":
            return httpx.Response(200, json=holders if holders is not None else [])
        return httpx.Response(404)

    return handle


async def _build(handler, **kwargs):
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, timeout=5.0) as client:
        return await svc.build_opportunities(client=client, use_cache=False, **kwargs)


def _run(coro):
    return asyncio.run(coro)


def test_build_opportunities_ranks_and_fills_required_fields():
    rows = [
        _gamma_row("small", "0xsmall", volume=20_000, liquidity=2_000, days=20, delta=0.01),
        _gamma_row(
            "big",
            "0xbig",
            volume=4_000_000,
            liquidity=400_000,
            days=2,
            delta=0.20,
            question="Will the SEC approve the ETF?",
        ),
        _gamma_row("mid", "0xmid", volume=300_000, liquidity=40_000, days=9, delta=0.05),
    ]
    holders = [{"proxyWallet": "0xaaa", "amount": 900.0}, {"proxyWallet": "0xbbb", "amount": 100.0}]
    response = _run(_build(_handler(rows, holders), limit=3, probe_holders=1))

    assert response.count == 3
    assert response.degraded is False
    assert response.weights == WEIGHTS
    assert [i.rank for i in response.items] == [1, 2, 3]
    assert response.items[0].condition_id == "0xbig"
    scores = [i.score for i in response.items]
    assert scores == sorted(scores, reverse=True)

    top = response.items[0]
    assert top.outcomes == ["Yes", "No"]
    assert top.prices == [0.42, 0.58]
    assert top.volume_usd == 4_000_000
    assert top.liquidity_usd == 400_000
    assert top.end_date is not None
    assert top.odds_delta == 0.20
    assert top.url.startswith("https://polymarket.com/")
    assert any("gamma-api.polymarket.com" in u for u in top.source_urls)
    assert top.whale_evidence is not None
    assert top.whale_evidence.available is True
    assert top.referral_url is None
    assert not hasattr(top, "order_id")


def test_referral_url_comes_from_env_only(monkeypatch):
    monkeypatch.setenv("POLYMARKET_OPPORTUNITIES_REFERRAL_URL", "https://example.org/ref")
    rows = [_gamma_row("one", "0xone", volume=500_000, liquidity=50_000, days=4, delta=0.1)]
    response = _run(_build(_handler(rows), limit=1, probe_holders=0))
    assert response.items[0].referral_url == "https://example.org/ref"
    assert response.items[0].url == "https://polymarket.com/market/one"


def test_build_opportunities_degrades_without_raising():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("upstream down", request=request)

    response = _run(_build(boom, limit=5, probe_holders=0))
    assert response.degraded is True
    assert response.degraded_reason == "gamma_unavailable_or_empty"
    assert response.items == []
    assert response.rate_limit["total_allowed"] >= 1


def test_build_opportunities_handles_rate_limit_response():
    def limited(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    response = _run(_build(limited, limit=5, probe_holders=0))
    assert response.degraded is True
    assert response.rate_limit["total_throttled"] == 1


def test_build_opportunities_drops_rows_below_thresholds():
    rows = [
        _gamma_row("tiny", "0xtiny", volume=10, liquidity=10, days=5, delta=0.1),
        _gamma_row("ok", "0xok", volume=900_000, liquidity=80_000, days=5, delta=0.1),
    ]
    response = _run(_build(_handler(rows), limit=10, probe_holders=0))
    assert [i.condition_id for i in response.items] == ["0xok"]
    assert response.dropped["below_thresholds"] == 1


def test_build_opportunities_drops_markets_outside_window():
    rows = [_gamma_row("far", "0xfar", volume=900_000, liquidity=80_000, days=400, delta=0.1)]
    response = _run(_build(_handler(rows), limit=10, window_hours=720, probe_holders=0))
    assert response.items == []
    assert response.dropped["outside_window"] == 1


def test_router_exposes_route_and_passes_filters(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routers import polymarket_opportunities as router_module

    seen: dict[str, object] = {}

    async def fake_build(**kwargs):
        seen.update(kwargs)
        return await svc.build_opportunities(
            client=httpx.AsyncClient(transport=httpx.MockTransport(_handler([]))),
            use_cache=False,
            **kwargs,
        )

    monkeypatch.setattr(router_module, "build_opportunities", fake_build)
    app = FastAPI()
    app.include_router(router_module.router)

    with TestClient(app) as client:
        resp = client.get("/api/v1/polymarket/opportunities", params={"limit": 7, "window_hours": 48})

    assert resp.status_code == 200, resp.text
    assert seen["limit"] == 7
    assert seen["window_hours"] == 48
    assert resp.json()["weights"] == WEIGHTS
