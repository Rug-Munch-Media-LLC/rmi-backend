"""
Prediction Market — Manifold Source
Play-money, open-source sentiment signals.
"""

from __future__ import annotations

import json
import requests
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.domains.prediction.prediction_market_package.models import (
    PredictionMarket,
    extract_token_symbols,
)

logger = get_logger("prediction_market.manifold")

MANIFOLD_BASE = "https://api.manifold.markets/v0"


async def search(http_client: Any, query: str) -> list[PredictionMarket]:
    try:
        resp = await http_client.get(
            f"{MANIFOLD_BASE}/search-markets", params={"term": query, "limit": 20}, timeout=10.0
        )
        if resp.status_code != 200:
            logger.warning(f"Manifold search returned {resp.status_code}")
            return []
        data = resp.json()
        contracts = data if isinstance(data, list) else data.get("contracts", data.get("markets", []))
        results = []
        for c in contracts[:10]:
            pm = _parse_market(c)
            if pm:
                results.append(pm)
        return results
    except (requests.RequestException, json.JSONDecodeError, KeyError, TypeError) as e:
        logger.debug("search_error", exc_info=True)
        logger.warning(f"Manifold search error: {e}")
        return []


def _parse_market(c: dict) -> PredictionMarket | None:
    try:
        question = c.get("question", "")
        slug = c.get("slug", c.get("id", ""))
        prob = float(c.get("probability", c.get("prob", 0.5)))
        volume = float(c.get("volume", c.get("volume24Hours", 0)))
        tokens_mentioned = extract_token_symbols(question)
        tags = list(c.get("tags", []))
        return PredictionMarket(
            source="manifold",
            source_id=str(slug),
            question=question,
            slug=str(slug),
            probability_yes=prob,
            probability_no=1.0 - prob,
            volume_usd=volume,
            category="",
            tags=tags,
            tokens_mentioned=tokens_mentioned,
            url=f"https://manifold.markets/{c.get('creatorUsername', '')}/{slug}" if slug else "",
            updated_at=datetime.now(UTC).isoformat(),
        )
    except (ValueError, TypeError, KeyError) as e:
        logger.debug("_parse_market_error", exc_info=True)
        logger.warning(f"Failed to parse Manifold market: {e}")
        return None
