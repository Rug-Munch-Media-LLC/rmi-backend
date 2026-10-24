"""Tests for /api/v1/assistant/context/{page} (page context API)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
from fastapi import FastAPI

from app.api.v1.assistant import dispatch as dispatch_mod, router as router_mod
from app.api.v1.assistant.models import PageContextResponse, PageParams, Section
from app.api.v1.assistant.sources_rugcharts import build_rugcharts, key_levels
from app.api.v1.assistant.sources_rugmaps_clusters import (
    funding_summary,
    select_cluster,
    summarize_clusters,
)
from app.api.v1.assistant.sources_trades import maker_concentration
from app.auth import require_auth
from app.core.rate_limiter import get_rate_limit_dependency

if TYPE_CHECKING:
    import pytest

_PAGE = "scanner"
_TOKEN = "t" * 40


def _params(**overrides: Any) -> PageParams:
    return PageParams(token=_TOKEN, **overrides)


def _trade(amount: float, maker: str) -> dict[str, Any]:
    return {"amount_usd": amount, "maker": maker, "tx_hash": "sig", "side": "buy"}


def test_key_levels_from_candles() -> None:
    candles = [{"high": 10 + i, "low": 5 - i, "close": 7, "volume": 1} for i in range(30)]
    levels = key_levels(candles, {"sma_20": 7.0, "bb_upper": 9.0, "bb_lower": 5.0})
    assert levels is not None
    assert levels["period_high"] == 39
    assert levels["period_low"] == -24
    assert levels["basis"] == "computed_from_ohlcv"
    assert key_levels([], {}) is None


def test_maker_concentration_shares() -> None:
    result = maker_concentration([_trade(75, "a"), _trade(25, "b")])
    assert result is not None
    assert result["unique_makers"] == 2
    assert result["top_maker_share_pct"] == 75.0
    assert maker_concentration([]) is None


def test_summarize_clusters_groups_and_ranks() -> None:
    holders = [
        {"address": "w1", "percentage": 5, "cluster_id": "c1"},
        {"address": "w2", "percentage": 5, "cluster_id": "c1"},
        {"address": "w3", "percentage": 1, "cluster_id": "c2"},
    ]
    rows, largest = summarize_clusters(holders, {})
    assert [row["size"] for row in rows] == [2, 1]
    assert largest is not None
    assert largest["total_holder_pct"] == 10
    assert select_cluster(rows, rows[0]["cluster_id"]) is not None
    assert select_cluster(rows, "missing") is None
    assert funding_summary(holders, {})["distinct_sources"] == 1
    assert summarize_clusters([], {}) == ([], None)


async def test_rugcharts_null_plus_reason_without_candles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _empty(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr("app.api.v1.assistant.sources_rugcharts.fetch_ohlcv", _empty)
    sections = await build_rugcharts(_params())
    assert sections["market"].data is None
    assert sections["market"].reason == "no_ohlcv_candles"
    assert sections["technical"].reason == "no_candles"


async def test_dispatch_status_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _cites(_query: str, limit: int = 3) -> list[Any]:
        return []

    monkeypatch.setattr(dispatch_mod, "citations_for", _cites)

    async def _ok(_params: PageParams) -> dict[str, Section]:
        return {"a": Section(source="s", data={"x": 1})}

    async def _partial(_params: PageParams) -> dict[str, Section]:
        return {"a": Section(source="s", data={"x": 1}), "b": Section(source="s", reason="missing")}

    async def _empty(_params: PageParams) -> dict[str, Section]:
        return {"a": Section(source="s", reason="missing")}

    for builder, expected in ((_ok, "ok"), (_partial, "partial"), (_empty, "unavailable")):
        monkeypatch.setitem(dispatch_mod._BUILDERS, "rugcharts", builder)
        response = await dispatch_mod.build_page_context("rugcharts", _params())
        assert response.status == expected


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api/v1")
    app.dependency_overrides[require_auth] = lambda: {"id": "user-1"}
    app.dependency_overrides[get_rate_limit_dependency("market")] = lambda: None
    return app


async def test_endpoint_returns_page_context(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_build(page: str, params: PageParams) -> PageContextResponse:
        assert page == _PAGE
        assert params.token == _TOKEN
        return PageContextResponse(page="scanner", sections={"verdict": Section(source="s")})

    monkeypatch.setattr(router_mod, "build_page_context", _fake_build)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.get(f"/api/v1/assistant/context/{_PAGE}", params={"token": _TOKEN})
    assert response.status_code == 200
    assert response.json()["page"] == "scanner"


async def test_endpoint_rejects_unknown_page() -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.get("/api/v1/assistant/context/nope", params={"token": _TOKEN})
    assert response.status_code == 422


async def test_endpoint_requires_auth() -> None:
    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api/v1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/assistant/context/{_PAGE}", params={"token": _TOKEN})
    assert response.status_code in (401, 403)
