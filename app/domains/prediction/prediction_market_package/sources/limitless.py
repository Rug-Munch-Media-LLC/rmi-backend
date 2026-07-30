"""
Prediction Market — Limitless Source
REST API /markets (Base chain, crypto-native).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.domains.prediction.prediction_market_package.models import (
    PredictionMarket,
    extract_token_symbols,
)

logger = get_logger("prediction_market.limitless")

LIMITLESS_BASE = "https://api.limitless.exchange"


async def search(http_client: Any, query: str) -> list[PredictionMarket]:
    try:
        resp = await http_client.get(
            f"{LIMITLESS_BASE}/markets/active",
            params={"limit": 25},
            headers={"Accept": "application/json"},
            timeout=10.0,
        )
        if resp.status_code != 200:
            logger.warning(f"Limitless markets returned {resp.status_code}")
            return []
        data = resp.json()
        all_markets = data.get("data", [])
        query_lower = query.lower()
        query_terms = query_lower.split()
        results = []
        for m in all_markets:
            title = m.get("title", "").lower()
            if any(term in title for term in query_terms):
                pm = _parse_market(m)
                if pm:
                    results.append(pm)
        return results
    except (requests.RequestException, json.JSONDecodeError, KeyError, TypeError) as e:
        logger.debug("search_error", exc_info=True)
        logger.warning(f"Limitless search error: {e}")
        return []


async def trending(http_client: Any, limit: int) -> list[PredictionMarket]:
    try:
        limit = min(limit, 25)
        resp = await http_client.get(
            f"{LIMITLESS_BASE}/markets/active",
            params={"limit": limit},
            headers={"Accept": "application/json"},
            timeout=10.0,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        markets = data.get("data", [])
        return [pm for m in markets[:limit] if (pm := _parse_market(m))]
    except (requests.RequestException, json.JSONDecodeError, KeyError, TypeError) as e:
        logger.debug("trending_error", exc_info=True)
        logger.warning(f"Limitless trending error: {e}")
        return []


def _parse_market(m: dict) -> PredictionMarket | None:
    try:
        title = m.get("title", "")
        slug = m.get("slug", str(m.get("id", "")))
        prices = m.get("prices", [50, 50])
        prob_yes = float(prices[0]) / 100 if isinstance(prices, list) and len(prices) >= 1 else 0.5
        prob_no = float(prices[1]) / 100 if isinstance(prices, list) and len(prices) >= 2 else 0.5
        vol_str = m.get("volumeFormatted", str(m.get("volume", 0)))
        volume = float(vol_str) if vol_str else 0.0
        categories = m.get("categories", [])
        category = categories[0] if categories else ""
        tags = m.get("tags", [])
        tokens_mentioned = extract_token_symbols(title)
        return PredictionMarket(
            source="limitless",
            source_id=str(slug),
            question=title,
            slug=str(slug),
            probability_yes=prob_yes,
            probability_no=prob_no,
            volume_usd=volume,
            category=category,
            tags=tags,
            tokens_mentioned=tokens_mentioned,
            url=f"https://limitless.exchange/markets/{slug}" if slug else "",
            ends_at=m.get("expirationDate", ""),
            updated_at=datetime.now(UTC).isoformat(),
        )
    except (ValueError, TypeError, KeyError) as e:
        logger.debug("_parse_market_error", exc_info=True)
        logger.warning(f"Failed to parse Limitless market: {e}")
        return None
