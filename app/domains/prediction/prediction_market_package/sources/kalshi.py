"""
Prediction Market — Kalshi Source
REST API /series, /markets, /events, /orderbook (unauthenticated).
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.domains.prediction.prediction_market_package.models import (
    PredictionMarket,
    extract_token_symbols,
)

logger = get_logger("prediction_market.kalshi")

KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"

async def search(http_client: Any, query: str) -> list[PredictionMarket]:
    try:
        resp = await http_client.get(f"{KALSHI_BASE}/events", params={"status": "open", "limit": 50},
                                     headers={"Accept": "application/json"}, timeout=10.0)
        if resp.status_code != 200:
            logger.warning(f"Kalshi events returned {resp.status_code}")
            return []
        data = resp.json()
        events = data.get("events", [])
        query_lower = query.lower()
        query_terms = query_lower.split()
        results = []
        for i, event in enumerate(events[:10]):
            event_title = event.get("title", "").lower()
            event_ticker = event.get("ticker", "")
            if not any(term in event_title for term in query_terms):
                continue
            if i > 0:
                await asyncio.sleep(0.3)
            try:
                mr = await http_client.get(f"{KALSHI_BASE}/markets", params={"event_ticker": event_ticker, "status": "open", "limit": 10},
                                           headers={"Accept": "application/json"}, timeout=8.0)
                if mr.status_code == 200:
                    markets_data = mr.json()
                    for m in markets_data.get("markets", []):
                        pm = _parse_market(m)
                        if pm:
                            results.append(pm)
            except (ConnectionError, OSError, TimeoutError):
                continue
        return results
    except (json.JSONDecodeError, KeyError, TypeError) as e :
        logger.debug("search_error", exc_info=True)
        logger.warning(f"Kalshi search error: {e}")
        return []

async def trending(http_client: Any, limit: int) -> list[PredictionMarket]:
    try:
        resp = await http_client.get(f"{KALSHI_BASE}/events", params={"status": "open", "limit": min(limit * 2, 30)}, timeout=10.0)
        if resp.status_code != 200:
            return []
        data = resp.json()
        events = data.get("events", [])
        results = []
        for event in events[:limit]:
            event_ticker = event.get("ticker", "")
            try:
                mr = await http_client.get(f"{KALSHI_BASE}/markets", params={"event_ticker": event_ticker, "status": "open", "limit": 5}, timeout=8.0)
                if mr.status_code == 200:
                    markets_data = mr.json()
                    for m in markets_data.get("markets", []):
                        pm = _parse_market(m)
                        if pm:
                            results.append(pm)
            except (ConnectionError, OSError, TimeoutError):
                continue
        return results[:limit]
    except (json.JSONDecodeError, KeyError, TypeError) as e :
        logger.debug("trending_error", exc_info=True)
        logger.warning(f"Kalshi trending error: {e}")
        return []

async def detail(http_client: Any, ticker: str) -> PredictionMarket | None:
    try:
        resp = await http_client.get(f"{KALSHI_BASE}/markets/{ticker}/orderbook", timeout=10.0)
        if resp.status_code != 200:
            return None
        data = resp.json()
        orderbook = data.get("orderbook_fp", {})
        yes_bids = orderbook.get("yes_dollars", [])
        best_yes = float(yes_bids[0][0]) if yes_bids else 0.5
        meta_resp = await http_client.get(f"{KALSHI_BASE}/markets", params={"ticker": ticker}, timeout=10.0)
        if meta_resp.status_code == 200:
            meta_data = meta_resp.json()
            markets = meta_data.get("markets", [])
            if markets:
                pm = _parse_market(markets[0])
                if pm:
                    pm.probability_yes = best_yes
                    pm.probability_no = 1.0 - best_yes
                    return pm
        return None
    except (requests.RequestException, json.JSONDecodeError, ValueError, KeyError, TypeError) as e :
        logger.debug("detail_error", exc_info=True)
        logger.warning(f"Kalshi detail error for {ticker}: {e}")
        return None

def _parse_market(m: dict) -> PredictionMarket | None:
    try:
        ticker = m.get("ticker", "")
        title = m.get("title", "")
        yes_bid = float(m.get("yes_bid_dollars", 0))
        volume = float(m.get("volume_fp", 0))
        category = m.get("category", "")
        if yes_bid <= 0 or ",yes " in title.lower():
            return None
        prob_yes = yes_bid
        prob_no = 1.0 - prob_yes if prob_yes else 0.5
        tokens_mentioned = extract_token_symbols(title)
        return PredictionMarket(
            source="kalshi", source_id=ticker, question=title, slug=ticker,
            probability_yes=prob_yes, probability_no=prob_no,
            volume_usd=volume, category=category,
            tokens_mentioned=tokens_mentioned,
            url=f"https://kalshi.com/markets/{ticker}" if ticker else "",
            updated_at=datetime.now(UTC).isoformat(),
        )
    except (ValueError, TypeError, KeyError) as e :
        logger.debug("_parse_market_error", exc_info=True)
        logger.warning(f"Failed to parse Kalshi market: {e}")
        return None
