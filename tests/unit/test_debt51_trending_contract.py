"""DEBT-51: /market/trending consumer must accept the consolidated engine payload."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import app.services.trending.service as trending_service
from app.routers.market_intel.routes.market_data import get_market_trending

if TYPE_CHECKING:
    import pytest

if TYPE_CHECKING:
    import pytest

ENGINE_PAYLOAD = {
    "chain": "unknownchain",
    "data": [],
    "boosted": [],
    "count": 0,
    "score_formula": "test-formula",
    "reason": "unsupported_chain: 'unknownchain' is not indexed by our ClickHouse",
    "supported_chains": ["ethereum"],
}


def test_trending_consumer_accepts_engine_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _stub(*_args: object, **_kwargs: object) -> dict:
        return dict(ENGINE_PAYLOAD)

    monkeypatch.setattr(trending_service, "trending_scored", _stub)
    result = asyncio.run(get_market_trending(limit=8, chain="unknownchain", hours=24))
    assert result["items"] == []
    assert result["total"] == 0
    assert result["reason"].startswith("unsupported_chain")
    assert result["score"] is None
