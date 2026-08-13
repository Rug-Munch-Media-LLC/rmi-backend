"""Prediction-market data module — Polymarket Gamma API (free, no key).

Surfaces interesting prediction markets for the RugMunchBot chat tool layer
(``app/domains/chat_tools.py`` ``_exec_prediction``) and for later ingestion
into a ``prediction_markets`` RAG collection.

Data source
-----------
Polymarket public Gamma API, no API key required:
    https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=N
Gamma market objects (verified live 2026-08-13) carry:
    question      str        "Will X happen?"
    slug          str        URL-safe market id
    conditionId   str        on-chain condition id
    endDate       str        ISO-8601 (e.g. "2026-12-31T00:00:00Z")
    outcomes      str        JSON array of outcome labels: '["Yes", "No"]'
    outcomePrices str        JSON array of prices 0..1: '["0.42", "0.58"]'
    volume        str        total volume in USD (stringified float)
    liquidity     str        total liquidity in USD (stringified float)
    category      str|None   often null at the market level
    events        list       event objects; category/tags usually live here
Note the prices come from ``outcomePrices`` — NOT ``outcomes`` (labels).
Each normalized market dict is:
    {question, category, volume, liquidity, end_date, slug, url, yes_price}
where ``yes_price`` is the price of the first outcome (YES), 0..1 or None.

RAG ingestion note
------------------
To feed a ``prediction_markets`` RAG collection, take the normalized dicts
from ``fetch_trending_markets`` (or the ranked output of ``rank_interesting``)
and store one document per market with ``id=slug`` and payload:
    question, category, yes_price, volume, liquidity, end_date, url
The news/news_v2 RAG path can then pull "what does Polymarket say about X?"
questions from the same Qdrant instance the bot already queries — see
``app/routers/news.py`` for the scroll pattern to mirror.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"
POLYMARKET_URL_TMPL = "https://polymarket.com/event/{slug}"

# Timeout for the whole Gamma fetch (the chat dispatcher wraps executors in an
# 8s cap, so keep this comfortably under it).
_FETCH_TIMEOUT = 6.0

# Category keyword sets for ``rank_interesting`` tagging (question + category
# text, lower-cased). "insider" is the Polymarket "insider trading"-style
# category (SEC/DOJ/indictment/arrest — the markets the bot wants to surface).
_CRYPTO_KEYWORDS = (
    "bitcoin",
    "btc",
    "ethereum",
    "eth",
    "solana",
    "sol",
    "crypto",
    "defi",
    "token",
    "blockchain",
    "exchange",
    "binance",
    "coinbase",
    "stablecoin",
    "etf",
    "altcoin",
)
_POLITICS_KEYWORDS = (
    "president",
    "election",
    "senate",
    "congress",
    "trump",
    "biden",
    "nominee",
    "governor",
    "prime minister",
    "vote",
    "gop",
    "democrat",
    "ukraine",
    "russia",
    "china",
    "israel",
    "cabinet",
    "parliament",
)
_SPORTS_KEYWORDS = (
    "super bowl",
    "nba",
    "nfl",
    "mlb",
    "soccer",
    "football",
    "basketball",
    "tennis",
    "world cup",
    "olympics",
    "champion",
    "playoff",
    "ufc",
    "boxing",
)
_INSIDER_KEYWORDS = (
    "insider",
    "sec",
    "doj",
    "indict",
    "arrest",
    "sentenc",
    "prosecut",
    "subpoena",
    "guilty",
    "plea",
    "fraud",
    "leak",
    "fbi",
    "cfpb",
    "investigat",
)
_CATEGORY_KEYWORD_SETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("crypto", _CRYPTO_KEYWORDS),
    ("politics", _POLITICS_KEYWORDS),
    ("sports", _SPORTS_KEYWORDS),
    ("insider", _INSIDER_KEYWORDS),
)


def _to_float(value: Any) -> float | None:
    """Best-effort float conversion (Gamma volume/liquidity are strings)."""
    try:
        f = float(value)
        return f if f == f else None  # NaN guard
    except (TypeError, ValueError):
        return None


def _parse_json_array(value: Any) -> list[Any]:
    """Gamma stores lists as JSON strings (``outcomePrices``); parse them."""
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _yes_price_from_gamma(market: dict[str, Any]) -> float | None:
    """YES price (0..1) from Gamma ``outcomePrices``; None when unavailable.

    The first element of ``outcomePrices`` is the YES outcome price. Fall back
    to a numeric ``outcomes`` element defensively, but the real field is
    ``outcomePrices`` — verified against the live API.
    """
    prices = _parse_json_array(market.get("outcomePrices"))
    if prices:
        price = _to_float(prices[0])
        if price is not None:
            return max(0.0, min(1.0, price))
    # Defensive fallback: a few legacy shapes put prices in ``outcomes``.
    outcomes = _parse_json_array(market.get("outcomes"))
    for outcome in outcomes:
        price = _to_float(outcome)
        if price is not None and 0.0 <= price <= 1.0:
            return price
    return None


def _market_category(market: dict[str, Any]) -> str:
    """Category from the market, else from its event tags (Gamma shape)."""
    category = str(market.get("category") or "").strip()
    if category:
        return category
    for event in market.get("events") or []:
        if not isinstance(event, dict):
            continue
        category = str(event.get("category") or "").strip()
        if category:
            return category
        for tag in event.get("tags") or []:
            if isinstance(tag, dict):
                label = str(tag.get("label") or "").strip()
                if label:
                    return label
            elif isinstance(tag, str) and tag.strip():
                return tag.strip()
    return ""


def normalize_market(market: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a raw Gamma market object to the module's market dict."""
    if not isinstance(market, dict):
        return None
    question = str(market.get("question") or "").strip()
    slug = str(market.get("slug") or "").strip()
    if not question or not slug:
        return None
    return {
        "question": question,
        "category": _market_category(market),
        "volume": _to_float(market.get("volume")) or 0.0,
        "liquidity": _to_float(market.get("liquidity")) or 0.0,
        "end_date": str(market.get("endDate") or "").strip(),
        "slug": slug,
        "url": POLYMARKET_URL_TMPL.format(slug=slug),
        "yes_price": _yes_price_from_gamma(market),
    }


async def fetch_trending_markets(
    limit: int = 15,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Fetch active Polymarket markets sorted by volume (Gamma API).

    Fail-open: returns ``[]`` on any network/HTTP/parse error — callers never
    see a raise. Pass ``client`` to inject a mock/preconfigured
    ``httpx.AsyncClient`` (tests); otherwise a short-lived client is created
    and closed internally. ``limit`` is passed straight to Gamma.
    """
    own_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=_FETCH_TIMEOUT)
    try:
        resp = await client.get(
            GAMMA_MARKETS_URL,
            params={
                "active": "true",
                "closed": "false",
                "limit": limit,
                "order": "volume",
                "ascending": "false",
            },
        )
        if resp.status_code != 200:
            logger.debug("prediction_markets_http", status=resp.status_code)
            return []
        payload = resp.json()
        if not isinstance(payload, list):
            return []
        markets = [normalize_market(m) for m in payload]
        return [m for m in markets if m is not None]
    except Exception as e:  # fail-open: never raise to chat
        logger.debug("prediction_markets_fetch_failed", error=str(e)[:120])
        return []
    finally:
        if own_client:
            await client.aclose()


def _days_until_end(end_date: str | None) -> float | None:
    """Days until the market's end date; None when unparseable or past."""
    if not end_date:
        return None
    try:
        raw = str(end_date).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return max(0.0, (dt - datetime.now(UTC)).total_seconds() / 86400.0)
    except (ValueError, TypeError, OverflowError):
        return None


def _tag_categories(market: dict[str, Any]) -> list[str]:
    """Category tags for a market based on question + category text."""
    text = f"{market.get('question', '')} {market.get('category', '')}".lower()
    return [label for label, keywords in _CATEGORY_KEYWORD_SETS if any(kw in text for kw in keywords)]


def _interest_score(market: dict[str, Any]) -> float:
    """Simple interest score: volume + liquidity/10 + recency bonus.

    Recency: markets closing sooner are more interesting — up to +20 for a
    market ending today, decaying to 0 at 30 days out.
    """
    volume = _to_float(market.get("volume")) or 0.0
    liquidity = _to_float(market.get("liquidity")) or 0.0
    days = _days_until_end(market.get("end_date"))
    recency = 20.0 * (1.0 - min(days, 30.0) / 30.0) if days is not None else 0.0
    return volume + (liquidity * 0.1) + recency


def rank_interesting(
    markets: list[dict[str, Any]],
    *,
    min_volume: float | None = None,
) -> list[dict[str, Any]]:
    """Rank markets by interest score, tagging categories. Pure.

    Drops closed/illiquid markets (zero volume or zero liquidity) and applies
    ``min_volume`` when given. Returns NEW dicts (input is not mutated), each
    enriched with ``tags`` (list[str]) and ``interest`` (float).
    """
    ranked: list[dict[str, Any]] = []
    for market in markets:
        if not isinstance(market, dict):
            continue
        volume = _to_float(market.get("volume")) or 0.0
        liquidity = _to_float(market.get("liquidity")) or 0.0
        if volume <= 0 or liquidity <= 0:
            continue  # closed/illiquid
        if min_volume is not None and volume < min_volume:
            continue
        enriched = dict(market)
        enriched["tags"] = _tag_categories(market)
        enriched["interest"] = _interest_score(market)
        ranked.append(enriched)
    ranked.sort(key=lambda m: float(m.get("interest") or 0.0), reverse=True)
    return ranked


def format_market_list(markets: list[dict[str, Any]], top_n: int = 8) -> str:
    """Render a numbered list of markets: ``📊 Q — YES price · vol $X · cat · url``."""
    if not markets:
        return ""
    lines: list[str] = []
    for i, market in enumerate(markets[:top_n], start=1):
        question = str(market.get("question") or "?").strip()
        yes_price = _to_float(market.get("yes_price"))
        if yes_price is None:
            price_str = "?"
        else:
            pct = yes_price * 100
            price_str = f"{pct:.1f}%" if pct < 10 else f"{pct:.0f}%"
        volume = _to_float(market.get("volume")) or 0.0
        category = str(market.get("category") or "").strip()
        if not category:
            tags = market.get("tags")
            category = str(tags[0]) if isinstance(tags, list) and tags else "?"
        url = str(market.get("url") or "").strip()
        lines.append(f"{i}. 📊 {question} — YES {price_str} · vol ${volume:,.0f} · {category} · {url}")
    return "\n".join(lines)
