"""Contract tests for the single trending engine (backend side).

The local scorer (``app.trending.scoring.score_row``) was deleted; these tests
lock the proxy contract instead: payload passthrough, paid-flag fidelity and
the 503 refusal envelope. No local ranking is exercised because none exists.
"""

from __future__ import annotations

import pathlib

import pytest
from fastapi import HTTPException

from app.trending.engine import ENGINE_ID, TrendingUnavailable

PAYLOAD = {
    "chain": "solana",
    "engine": ENGINE_ID,
    "generated_at": "2026-10-25T10:00:00Z",
    "ranked": [
        {
            "rank": 1,
            "chain": "solana",
            "token_address": "So11111111111111111111111111111111111111112",
            "symbol": "PAID",
            "score": 0.73,
            "evidence": [{"source": "dexscreener", "signal": "volume_24h", "value": 6500000, "paid": True}],
            "paid": True,
            "paid_only": False,
            "disclosures": ["dexscreener_boost$500"],
        }
    ],
    "paid_only_excluded": [],
    "missing_sources": ["gmgn"],
    "stale": False,
}


@pytest.mark.asyncio
async def test_build_trending_proxies_engine_payload(monkeypatch):
    from app.trending import service

    async def _fake(chain: str, limit: int):
        assert (chain, limit) == ("solana", 5)
        return PAYLOAD

    monkeypatch.setattr("app.trending.service.fetch_engine_trending", _fake)
    assert await service.build_trending("solana", 24, 5) == PAYLOAD


@pytest.mark.asyncio
async def test_paid_flags_pass_through_untouched(monkeypatch):
    from app.trending import service

    async def _fake(chain: str, limit: int):
        return PAYLOAD

    monkeypatch.setattr("app.trending.service.fetch_engine_trending", _fake)
    row = (await service.build_trending("solana", 24, 5))["ranked"][0]
    assert row["paid"] is True
    assert row["disclosures"] == ["dexscreener_boost$500"]
    assert row["score"] == 0.73


@pytest.mark.asyncio
async def test_engine_unavailable_raises_typed_refusal(monkeypatch):
    from app.trending import service

    async def _boom(chain: str, limit: int):
        raise TrendingUnavailable("transport:ConnectError")

    monkeypatch.setattr("app.trending.service.fetch_engine_trending", _boom)
    with pytest.raises(TrendingUnavailable) as exc:
        await service.build_trending("solana", 24, 5)
    assert exc.value.envelope() == {
        "status": "unavailable",
        "source": "rugbrain.trending_fusion",
        "reason": "transport:ConnectError",
    }


def test_no_local_ranker_remains():
    root = pathlib.Path(__file__).resolve().parents[1] / "app/trending"
    banned = ("S_vol", "score_row", "entity_penalty", "quote_penalty")
    offenders = [
        f"{p.name}:{n}" for p in root.rglob("*.py") for n in banned if n in p.read_text(errors="ignore")
    ]
    assert offenders == [], f"local trending ranking survived: {offenders}"
    assert not (pathlib.Path(__file__).resolve().parents[1] / "app/trending/scoring.py").exists()


def test_route_refusal_is_503():
    from app.routers.v1.market import get_trending

    async def _boom(chain: str, limit: int):
        raise TrendingUnavailable("http:503")

    get_trending.__globals__["fetch_engine_trending"] = _boom
    with pytest.raises(HTTPException) as exc:
        import asyncio

        asyncio.run(get_trending("solana", 5))
    assert exc.value.status_code == 503
    assert exc.value.detail["status"] == "unavailable"


@pytest.mark.asyncio
async def test_legacy_route_adapter_splits_paid_without_rescoring(monkeypatch):
    """market_data.py:116 consumes this shape: data/count/boosted/score_formula."""
    from app.services.trending import service as legacy

    organic = {"rank": 2, "token_address": "0xORG", "score": 0.4, "paid": False, "evidence": []}

    async def _fake(chain: str, limit: int):
        return {**PAYLOAD, "ranked": [*PAYLOAD["ranked"], organic]}

    monkeypatch.setattr(legacy, "fetch_engine_trending", _fake)
    out = await legacy.trending_scored(chain="solana", limit=5, hours=24)
    assert [r["token_address"] for r in out["data"]] == ["0xORG"]  # organic keeps the rank
    assert [r["token_address"] for r in out["boosted"]] == [PAYLOAD["ranked"][0]["token_address"]]
    assert out["score_formula"] == ENGINE_ID  # engine owns the formula, not the route
    assert out["data"][0]["score"] == 0.4  # passthrough: no local arithmetic
    for key in ("data", "count", "boosted", "chain", "score_formula", "reason", "updated_at"):
        assert key in out
