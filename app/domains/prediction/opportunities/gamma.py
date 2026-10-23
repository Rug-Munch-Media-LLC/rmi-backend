"""Polymarket Gamma market discovery (read-only, keyless).

Endpoints and parameter names are corpus-verified against
``nautechsystems/nautilus_trader`` (``crates/adapters/polymarket/src/http/query.rs``,
``GetGammaMarketsParams.validate_keyset``) which pins the same public surface:

    GET https://gamma-api.polymarket.com/markets
      active, closed, limit (cap 100), order, ascending,
      volume_num_min, volume_num_max, liquidity_num_min, liquidity_num_max,
      start_date_min/max, end_date_min/max, tag_id, condition_ids, slug

Market objects carry ``outcomes`` / ``outcomePrices`` as **JSON strings**, plus
``oneHourPriceChange`` / ``oneDayPriceChange`` / ``oneWeekPriceChange`` (verified
against ``rainbow-me/rainbow`` ``RawPolymarketMarket``). Those price-change
fields are the odds-movement source; there is no free odds history on Gamma.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.domains.prediction.opportunities.rate_limit import RateLimitTracker

logger = get_logger(__name__)

GAMMA_HOST = "gamma-api.polymarket.com"
GAMMA_MARKETS_URL = f"https://{GAMMA_HOST}/markets"
GAMMA_PAGE_LIMIT = 100
POLYMARKET_MARKET_URL = "https://polymarket.com/market/{slug}"

# Ordered: most responsive window first. First non-null wins.
ODDS_DELTA_FIELDS: tuple[tuple[str, str], ...] = (
    ("oneHourPriceChange", "1h"),
    ("oneDayPriceChange", "1d"),
    ("oneWeekPriceChange", "1w"),
)


def build_params(
    *,
    window_hours: int,
    min_volume_usd: float,
    min_liquidity_usd: float,
    page_limit: int = GAMMA_PAGE_LIMIT,
) -> dict[str, Any]:
    """Gamma query for unresolved markets resolving inside ``window_hours``."""
    now = datetime.now(UTC)
    end_max = now.timestamp() + window_hours * 3600
    return {
        "active": "true",
        "closed": "false",
        "limit": max(1, min(page_limit, GAMMA_PAGE_LIMIT)),
        "order": "volumeNum",
        "ascending": "false",
        "volume_num_min": max(0.0, min_volume_usd),
        "liquidity_num_min": max(0.0, min_liquidity_usd),
        "end_date_min": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_date_max": datetime.fromtimestamp(end_max, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out  # NaN guard


def _json_list(value: Any) -> list[Any]:
    """Gamma ships arrays as JSON *strings*; tolerate real arrays too."""
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _parse_end_date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def normalize_market(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Validate one Gamma row. Returns ``None`` when it cannot be trusted."""
    if not isinstance(raw, dict):
        return None
    condition_id = str(raw.get("conditionId") or raw.get("condition_id") or raw.get("id") or "").strip()
    question = str(raw.get("question") or "").strip()
    if not condition_id or not question:
        return None

    outcomes = [str(o) for o in _json_list(raw.get("outcomes"))]
    prices = [p for p in (_as_float(x) for x in _json_list(raw.get("outcomePrices"))) if p is not None]
    if not outcomes or not prices or len(outcomes) != len(prices):
        return None

    end_dt = _parse_end_date(raw.get("endDate") or raw.get("end_date"))
    hours_to_resolution: float | None = None
    if end_dt is not None:
        hours_to_resolution = round((end_dt - datetime.now(UTC)).total_seconds() / 3600.0, 2)

    odds_delta: float | None = None
    odds_delta_window: str | None = None
    for field, label in ODDS_DELTA_FIELDS:
        candidate = _as_float(raw.get(field))
        if candidate is not None:
            odds_delta, odds_delta_window = candidate, label
            break

    slug = str(raw.get("slug") or "").strip() or None
    return {
        "condition_id": condition_id,
        "slug": slug,
        "question": question,
        "outcomes": outcomes,
        "prices": prices,
        "yes_price": prices[0],
        "volume_usd": _as_float(raw.get("volumeNum") or raw.get("volume")) or 0.0,
        "liquidity_usd": _as_float(raw.get("liquidityNum") or raw.get("liquidity")) or 0.0,
        "end_date": end_dt.isoformat().replace("+00:00", "Z") if end_dt else None,
        "hours_to_resolution": hours_to_resolution,
        "odds_delta": odds_delta,
        "odds_delta_window": odds_delta_window,
        "odds_delta_reason": None if odds_delta is not None else "gamma_returned_no_price_change_field",
        "tags": _json_list(raw.get("tags")) or [],
        "events": raw.get("events") if isinstance(raw.get("events"), list) else [],
        "gamma_url": f"{GAMMA_MARKETS_URL}/{condition_id}",
        "market_url": POLYMARKET_MARKET_URL.format(slug=slug) if slug else POLYMARKET_MARKET_URL.format(slug=""),
    }


async def fetch_gamma_markets(
    client: httpx.AsyncClient,
    *,
    window_hours: int,
    min_volume_usd: float,
    min_liquidity_usd: float,
    tracker: RateLimitTracker,
    page_limit: int = GAMMA_PAGE_LIMIT,
) -> list[dict[str, Any]]:
    """Fetch + validate Gamma rows. Returns ``[]`` on any upstream failure."""
    params = build_params(
        window_hours=window_hours,
        min_volume_usd=min_volume_usd,
        min_liquidity_usd=min_liquidity_usd,
        page_limit=page_limit,
    )
    if not tracker.allow(GAMMA_HOST):
        logger.warning("polymarket.opportunities.gamma_budget_exhausted")
        return []
    try:
        response = await client.get(GAMMA_MARKETS_URL, params=params)
    except httpx.HTTPError as exc:
        logger.warning("polymarket.opportunities.gamma_transport_error", extra={"error": str(exc)})
        return []

    if response.status_code == 429:
        tracker.penalise(GAMMA_HOST, 30.0)
        logger.warning("polymarket.opportunities.gamma_rate_limited")
        return []
    if response.status_code != 200:
        logger.warning("polymarket.opportunities.gamma_http_error", extra={"status": response.status_code})
        return []
    try:
        payload = response.json()
    except ValueError:
        logger.warning("polymarket.opportunities.gamma_bad_json")
        return []
    rows = payload if isinstance(payload, list) else payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        logger.warning("polymarket.opportunities.gamma_unexpected_shape")
        return []
    return [m for m in (normalize_market(row) for row in rows) if m is not None]
