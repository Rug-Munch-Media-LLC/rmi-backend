"""The geo-blocked venues: Bybit and Binance.

Both refuse our egress (HTTP 403 and HTTP 451 "Service unavailable from a
restricted location") and also refuse a US exit, so they go through the
residential pool. Pool handling - rotation, and remembering which exits are
refused - lives in ``liqmap_proxy``; the other venues (OKX, Gate, Bitget,
MEXC, Kucoin) answer directly and live in ``liqmap_venues_direct``.
"""

from __future__ import annotations

import logging
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


async def _bybit_liquidation_snapshot() -> dict[str, dict[str, Any]]:
    """Bybit v5 tickers: one call -> OI + funding + mark for all linear."""
    response, _proxy = await fetch_geo_venue(BYBIT_TICKERS, {"category": "linear"})
    if response is None:
        logger.warning("liqmap bybit offline: no pool exit answered")
        return {}
    rows: list[dict[str, Any]] = (response.json().get("result") or {}).get("list", [])
    return collect_rows(rows, _bybit_row)


async def _binance_liquidation_snapshot() -> dict[str, dict[str, Any]]:
    """Binance fapi premiumIndex: one call -> mark + funding for all symbols.

    OI needs per-symbol calls on Binance, so venues get OI from Bybit/OKX/HL;
    Binance contributes mark/funding for liq-level math when OI known elsewhere.
    """
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
    return collect_rows(rows, _binance_row)
