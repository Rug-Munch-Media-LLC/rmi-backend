"""
Prediction Market — Polymarket Source
Uses Gamma API (search/discovery), CLOB API (prices), and Data API (trades).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.domains.prediction.prediction_market_package.models import (
    PredictionMarket,
    extract_token_symbols,
)

logger = get_logger("prediction_market.polymarket")

POLYMARKET_GAMMA = "https://gamma-api.polymarket.com"
POLYMARKET_CLOB = "https://clob.polymarket.com"


async def search(http_client: Any, query: str) -> list[PredictionMarket]:
    """Search Polymarket Gamma API."""
    try:
        resp = await http_client.get(f"{POLYMARKET_GAMMA}/public-search", params={"q": query}, timeout=10.0)
        if resp.status_code != 200:
            logger.warning(f"Polymarket search returned {resp.status_code}")
            return []
        data = resp.json()
        events = data.get("events", [])
        markets = []
        for event in events[:10]:
            for m in event.get("markets", [])[:5]:
                pm = _parse_market(m, event)
                if pm:
                    markets.append(pm)
        return markets
    except (requests.RequestException, json.JSONDecodeError, KeyError, TypeError) as e:
        logger.debug("search_error", exc_info=True)
        logger.warning(f"Polymarket search error: {e}")
        return []


async def trending(http_client: Any, limit: int) -> list[PredictionMarket]:
    """Get trending Polymarket events by volume."""
    try:
        resp = await http_client.get(
            f"{POLYMARKET_GAMMA}/events",
            params={
                "limit": limit,
                "active": "true",
                "closed": "false",
                "order": "volume",
                "ascending": "false",
            },
            timeout=10.0,
        )
        if resp.status_code != 200:
            return []
        events = resp.json()
        markets = []
        for event in events[:limit]:
            for m in event.get("markets", [])[:3]:
                pm = _parse_market(m, event)
                if pm:
                    markets.append(pm)
        return markets
    except (requests.RequestException, json.JSONDecodeError, KeyError, TypeError) as e:
        logger.debug("trending_error", exc_info=True)
        logger.warning(f"Polymarket trending error: {e}")
        return []


async def detail(http_client: Any, slug: str) -> PredictionMarket | None:
    """Get detailed Polymarket market data including CLOB prices."""
    try:
        resp = await http_client.get(f"{POLYMARKET_GAMMA}/markets", params={"slug": slug}, timeout=10.0)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not data:
            return None
        m = data[0]
        pm = _parse_market(m)
        if pm:
            tokens = _parse_json_field(m.get("clobTokenIds", "[]"))
            if isinstance(tokens, list) and len(tokens) >= 2:
                try:
                    price_resp = await http_client.get(
                        f"{POLYMARKET_CLOB}/price", params={"token_id": tokens[0], "side": "buy"}, timeout=5.0
                    )
                    if price_resp.status_code == 200:
                        price_data = price_resp.json()
                        live_price = float(price_data.get("price", pm.probability_yes))
                        pm.probability_yes = live_price
                        pm.probability_no = 1.0 - live_price
                except (ConnectionError, OSError, TimeoutError):
                    pass
        return pm
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
        logger.debug("detail_error", exc_info=True)
        logger.warning(f"Polymarket detail error for {slug}: {e}")
        return None


def _parse_market(m: dict, event: dict | None = None) -> PredictionMarket | None:
    try:
        question = m.get("question", "")
        slug = m.get("slug", "")
        prices = _parse_json_field(m.get("outcomePrices", "[]"))
        if isinstance(prices, list) and len(prices) >= 2:
            prob_yes = float(prices[0])
            prob_no = float(prices[1])
        else:
            prob_yes = 0.5
            prob_no = 0.5
        volume = float(m.get("volume", 0))
        liquidity = float(m.get("liquidity", 0))
        tokens_mentioned = extract_token_symbols(question)
        tags = []
        if event:
            tags.extend([t.get("label", "") for t in event.get("tags", [])])
        return PredictionMarket(
            source="polymarket",
            source_id=slug,
            question=question,
            slug=slug,
            probability_yes=prob_yes,
            probability_no=prob_no,
            volume_usd=volume,
            liquidity_usd=liquidity,
            category=m.get("category", event.get("category", "") if event else ""),
            tags=tags,
            tokens_mentioned=tokens_mentioned,
            url=f"https://polymarket.com/event/{slug}" if slug else "",
            ends_at=m.get("endDate", event.get("endDate", "") if event else ""),
            updated_at=datetime.now(UTC).isoformat(),
        )
    except (ValueError, TypeError, KeyError) as e:
        logger.debug("_parse_market_error", exc_info=True)
        logger.warning(f"Failed to parse Polymarket market: {e}")
        return None


def _parse_json_field(val) -> Any:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return val
    return val
