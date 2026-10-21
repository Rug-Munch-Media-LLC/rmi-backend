"""Venues that answer our egress directly (no residential proxy).

OKX, Gate, Bitget, MEXC and Kucoin all answer from our own IP, so they use
a plain direct client. Split from the geo-blocked venues
(``liqmap_venues``) so each module stays small and each concern - "needs a
pool" vs "does not" - is in one place.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.routers.market_intel.routes.liqmap_rows import (
    _bitget_row,
    _gate_row,
    _kucoin_row,
    _mexc_row,
    _okx_row,
    collect_rows,
)

logger = logging.getLogger(__name__)


async def _okx_liquidation_snapshot() -> dict[str, dict[str, Any]]:
    """OKX v5 public open-interest: one call -> oiUsd for all SWAPs."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                "https://www.okx.com/api/v5/public/open-interest",
                params={"instType": "SWAP"},
            )
            return collect_rows(r.json().get("data") or [], _okx_row)
    except Exception as exc:
        logger.warning("liqmap okx failed: %s", str(exc)[:100])
    return {}


async def _bitget_liquidation_snapshot() -> dict[str, dict[str, Any]]:
    """Bitget v2 tickers: one call -> OI(USD) + funding for all USDT perps."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                "https://api.bitget.com/api/v2/mix/market/tickers",
                params={"productType": "USDT-FUTURES"},
            )
            return collect_rows(r.json().get("data") or [], _bitget_row)
    except Exception as exc:
        logger.warning("liqmap bitget failed: %s", str(exc)[:100])
    return {}


async def _gate_liquidation_snapshot() -> dict[str, dict[str, Any]]:
    """Gate.io v4 futures contracts: one call -> OI + funding for all USDT perps."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://api.gateio.ws/api/v4/futures/usdt/contracts")
            return collect_rows(r.json(), _gate_row)
    except Exception as exc:
        logger.warning("liqmap gate failed: %s", str(exc)[:100])
    return {}


async def _mexc_liquidation_snapshot() -> dict[str, dict[str, Any]]:
    """MEXC contract ticker: one call -> OI + funding for all perps."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://contract.mexc.com/api/v1/contract/ticker")
            return collect_rows(r.json().get("data") or [], _mexc_row)
    except Exception as exc:
        logger.warning("liqmap mexc failed: %s", str(exc)[:100])
    return {}


async def _kucoin_liquidation_snapshot() -> dict[str, dict[str, Any]]:
    """KuCoin futures active contracts: one call -> OI + funding."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://api-futures.kucoin.com/api/v1/contracts/active")
            return collect_rows(r.json().get("data") or [], _kucoin_row)
    except Exception as exc:
        logger.warning("liqmap kucoin failed: %s", str(exc)[:100])
    return {}
