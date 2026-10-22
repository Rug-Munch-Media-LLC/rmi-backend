"""The geo-blocked venues: Bybit and Binance.

Both refuse our egress (HTTP 403 and HTTP 451 "Service unavailable from a
restricted location") and also refuse a US exit, so they go through the
residential pool. Pool handling - rotation, and remembering which exits are
refused - lives in ``liqmap_proxy``; the other venues (OKX, Gate, Bitget,
MEXC, Kucoin) answer directly and live in ``liqmap_venues_direct``.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.routers.market_intel.routes.liqmap_proxy import fetch_geo_venue
from app.routers.market_intel.routes.liqmap_rows import (
    _binance_row,
    _bybit_row,
    collect_rows,
)

logger = logging.getLogger(__name__)

BYBIT_TICKERS = "https://api.bybit.com/v5/market/tickers"
BINANCE_PREMIUM = "https://fapi.binance.com/fapi/v1/premiumIndex"

#: How long a proxied venue snapshot stays usable. These two venues are the
#: ONLY ones that cost webshare bandwidth (~1 MB per call), and their
#: open-interest/funding data moves slowly, so a 15 minute cache bounds the
#: spend no matter how often the map is requested. The five direct venues
#: are free and stay live.
SNAPSHOT_TTL_S = 900.0

_snapshots: dict[str, tuple[float, dict[str, dict[str, Any]]]] = {}


def _cached_snapshot(name: str) -> dict[str, dict[str, Any]] | None:
    """The cached snapshot for a venue, or None when it is stale/absent."""
    hit = _snapshots.get(name)
    if hit is None:
        return None
    at, data = hit
    if time.monotonic() - at > SNAPSHOT_TTL_S:
        return None
    return data


def _cache_snapshot(name: str, data: dict[str, dict[str, Any]]) -> None:
    """Store a venue snapshot. Only non-empty results are stored: caching an
    offline venue would keep it offline for the whole TTL."""
    if not data:
        return
    _snapshots[name] = (time.monotonic(), data)


async def _bybit_liquidation_snapshot() -> dict[str, dict[str, Any]]:
    """Bybit v5 tickers: one call -> OI + funding + mark for all linear."""
    cached = _cached_snapshot("bybit")
    if cached is not None:
        return cached
    response, _proxy = await fetch_geo_venue(BYBIT_TICKERS, {"category": "linear"})
    if response is None:
        logger.warning("liqmap bybit offline: no pool exit answered")
        return {}
    rows: list[dict[str, Any]] = (response.json().get("result") or {}).get("list", [])
    data = collect_rows(rows, _bybit_row)
    _cache_snapshot("bybit", data)
    return data


async def _binance_liquidation_snapshot() -> dict[str, dict[str, Any]]:
    """Binance fapi premiumIndex: one call -> mark + funding for all symbols.

    OI needs per-symbol calls on Binance, so venues get OI from Bybit/OKX/HL;
    Binance contributes mark/funding for liq-level math when OI known elsewhere.
    """
    cached = _cached_snapshot("binance")
    if cached is not None:
        return cached
    response, _proxy = await fetch_geo_venue(BINANCE_PREMIUM)
    if response is None:
        logger.warning("liqmap binance offline: no pool exit answered")
        return {}
    payload = response.json()
    # The endpoint returns a LIST on success and an OBJECT on a
    # geo/availability refusal ({"code":..,"msg":..}). Iterating the
    # object yielded its KEYS, so every element was a str and
    # `t.get(...)` raised AttributeError - the venue never reported.
    if not isinstance(payload, list):
        return {}
    rows: list[dict[str, Any]] = [row for row in payload if isinstance(row, dict)]
    data = collect_rows(rows, _binance_row)
    _cache_snapshot("binance", data)
    return data
