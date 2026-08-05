"""Market Agent — market sentiment, trending tokens, and 24h movers.

Specialist agent built on Pydantic AI. Emits a typed ``MarketResult``. The
``get_market_snapshot()`` tool pulls live Fear & Greed and trending data
from free public APIs (alternative.me, CoinGecko) — per the fleet frugality
rule, free data providers first. If the APIs are unreachable the tool
returns empty/None fields and the model synthesizes from its knowledge.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

from app.agents.config import HTTP_TIMEOUT, get_agent_model
from app.agents.models import MarketResult

market_agent = Agent(
    get_agent_model(),
    output_type=MarketResult,
    system_prompt=(
        "You are a crypto market analyst. Produce a MarketResult snapshot: "
        "fear & greed index, market sentiment, trending tokens, recent "
        "exploits, and top 24h movers. Call get_market_snapshot() for live "
        "data where available, and synthesize the rest from your knowledge. "
        "Be clear about uncertainty when live data is unavailable."
    ),
    model_settings=ModelSettings(temperature=0.4),
)


async def _fetch_fear_greed() -> dict[str, Any]:
    """Fetch the Fear & Greed index from the free alternative.me API."""
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get("https://api.alternative.me/fng/?limit=1")
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
        entry = data["data"][0]
        return {
            "fear_index": int(entry["value"]),
            "sentiment": entry["value_classification"],
        }
    except Exception as exc:  # tool must degrade gracefully
        return {"fear_index": None, "sentiment": None, "error": str(exc)}


async def _fetch_trending() -> list[str]:
    """Fetch trending coins from the free CoinGecko API."""
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get("https://api.coingecko.com/api/v3/search/trending")
            resp.raise_for_status()
            data = resp.json()
        return [coin["item"]["symbol"] for coin in data.get("coins", [])[:10]]
    except Exception:  # tool must degrade gracefully
        return []


@market_agent.tool_plain
async def get_market_snapshot() -> dict[str, Any]:
    """Fetch live fear & greed index and trending tokens from free public APIs.

    Returns:
        A dict with ``fear_greed`` (fear_index, sentiment) and ``trending``
        (list of token symbols). Fields may be null/empty when the free APIs
        are unreachable.
    """
    fear_greed, trending = await asyncio.gather(_fetch_fear_greed(), _fetch_trending())
    return {"fear_greed": fear_greed, "trending": trending}
