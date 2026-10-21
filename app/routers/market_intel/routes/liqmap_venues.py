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

from app.routers.market_intel.routes.liqmap_proxy import (
    VENUE_GEO_REFUSALS,
    note_failure,
    note_refusal,
    proxy_client,
)

logger = logging.getLogger(__name__)


async def _bybit_liquidation_snapshot() -> dict[str, dict[str, Any]]:
    """Bybit v5 tickers: one call -> OI + funding + mark for all linear."""
    out: dict[str, dict[str, Any]] = {}
    client, proxy = proxy_client()
    try:
        async with client as c:
            r = await c.get("https://api.bybit.com/v5/market/tickers", params={"category": "linear"})
            if r.status_code in VENUE_GEO_REFUSALS:
                note_refusal(proxy, r.status_code)
                logger.warning("liqmap bybit refused exit (status=%s)", r.status_code)
                return out
            for t in (r.json().get("result") or {}).get("list", []):
                sym = str(t.get("symbol") or "").replace("USDT", "").replace("USDC", "")
                if not sym:
                    continue
                try:
                    out.setdefault(
                        sym,
                        {
                            "oi_usd": float(t.get("open_interest") or 0) * float(t.get("markPrice") or 0),
                            "funding": float(t.get("fundingRate") or 0),
                            "mark": float(t.get("markPrice") or 0),
                        },
                    )
                except (TypeError, ValueError):
                    continue
    except Exception as exc:
        note_failure(proxy)
        logger.warning("liqmap bybit failed: %s", str(exc)[:100])
    return out


async def _binance_liquidation_snapshot() -> dict[str, dict[str, Any]]:
    """Binance fapi premiumIndex: one call -> mark + funding for all symbols.

    OI needs per-symbol calls on Binance, so venues get OI from Bybit/OKX/HL;
    Binance contributes mark/funding for liq-level math when OI known elsewhere.
    """
    out: dict[str, dict[str, Any]] = {}
    client, proxy = proxy_client()
    try:
        # Binance futures is geo-restricted from our egress (HTTP 451
        # "Service unavailable from a restricted location"), so this venue
        # needs the residential pool. A US exit is refused too - the pool
        # entry that answers is a non-US one.
        async with client as c:
            r = await c.get("https://fapi.binance.com/fapi/v1/premiumIndex")
            if r.status_code in VENUE_GEO_REFUSALS:
                note_refusal(proxy, r.status_code)
                logger.warning("liqmap binance refused exit (status=%s)", r.status_code)
                return out
            payload = r.json()
            # The endpoint returns a LIST on success and an OBJECT on a
            # geo/availability refusal ({"code":..,"msg":..}). Iterating the
            # object yielded its KEYS, so every element was a str and
            # `t.get(...)` raised AttributeError - the venue never reported.
            rows = payload if isinstance(payload, list) else []
            for t in rows:
                sym = str(t.get("symbol") or "").replace("USDT", "").replace("USDC", "").replace("PERP", "")
                if not sym:
                    continue
                try:
                    out.setdefault(
                        sym,
                        {
                            "oi_usd": 0.0,
                            "funding": float(t.get("lastFundingRate") or 0),
                            "mark": float(t.get("markPrice") or 0),
                        },
                    )
                except (TypeError, ValueError):
                    continue
    except Exception as exc:
        note_failure(proxy)
        logger.warning("liqmap binance failed: %s", str(exc)[:100])
    return out
