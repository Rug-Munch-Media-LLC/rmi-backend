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

logger = logging.getLogger(__name__)


async def _okx_liquidation_snapshot() -> dict[str, dict[str, Any]]:
    """OKX v5 public open-interest: one call -> oiUsd for all SWAPs."""
    out: dict[str, dict[str, Any]] = {}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://www.okx.com/api/v5/public/open-interest", params={"instType": "SWAP"})
            for t in r.json().get("data") or []:
                inst = str(t.get("instId") or "")  # e.g. BTC-USDT-SWAP
                sym = inst.split("-")[0] if inst else ""
                if not sym:
                    continue
                try:
                    out.setdefault(
                        sym,
                        {
                            "oi_usd": float(t.get("oiUsd") or 0),
                            "funding": None,
                            "mark": float(t.get("instPx") or 0) or None,
                        },
                    )
                except (TypeError, ValueError):
                    continue
    except Exception as exc:
        logger.warning("liqmap okx failed: %s", str(exc)[:100])
    return out


async def _bitget_liquidation_snapshot() -> dict[str, dict[str, Any]]:
    """Bitget v2 tickers: one call -> OI(USD) + funding for all USDT perps."""
    out: dict[str, dict[str, Any]] = {}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://api.bitget.com/api/v2/mix/market/tickers", params={"productType": "USDT-FUTURES"})
            for t in r.json().get("data") or []:
                sym = str(t.get("symbol") or "").replace("USDT", "")
                if not sym:
                    continue
                try:
                    out.setdefault(
                        sym,
                        {
                            "oi_usd": float(t.get("holdingAmount") or 0) * float(t.get("markPrice") or 0),
                            "funding": float(t.get("fundingRate") or 0),
                            "mark": float(t.get("markPrice") or 0),
                        },
                    )
                except (TypeError, ValueError):
                    continue
    except Exception as exc:
        logger.warning("liqmap bitget failed: %s", str(exc)[:100])
    return out


async def _gate_liquidation_snapshot() -> dict[str, dict[str, Any]]:
    """Gate.io v4 futures contracts: one call -> OI + funding for all USDT perps."""
    out: dict[str, dict[str, Any]] = {}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://api.gateio.ws/api/v4/futures/usdt/contracts")
            for t in r.json():
                sym = str(t.get("name") or "").replace("_USDT", "").replace("_USDC", "")
                if not sym:
                    continue
                try:
                    mark = float(t.get("mark_price") or 0)
                    oi_usd = float(t.get("open_interest_usd") or 0)
                    out.setdefault(
                        sym,
                        {
                            "oi_usd": oi_usd,
                            "funding": float(t.get("funding_rate") or 0),
                            "mark": mark,
                        },
                    )
                except (TypeError, ValueError):
                    continue
    except Exception as exc:
        logger.warning("liqmap gate failed: %s", str(exc)[:100])
    return out


async def _mexc_liquidation_snapshot() -> dict[str, dict[str, Any]]:
    """MEXC contract ticker: one call -> OI + funding for all perps."""
    out: dict[str, dict[str, Any]] = {}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://contract.mexc.com/api/v1/contract/ticker")
            for t in r.json().get("data") or []:
                sym = str(t.get("symbol") or "").replace("_USDT", "")
                if not sym:
                    continue
                try:
                    mark = float(t.get("lastPrice") or 0)
                    # holdVol (contracts) intentionally unused for OI — see note.
                    # MEXC holdVol is in contracts; contract size via cVol fallback.
                    out.setdefault(
                        sym,
                        {
                            # holdVol is in CONTRACTS (size varies per market) --
                            # using it as coin OI inflated BTC to \$48T. Funding +
                            # mark only until contract sizes come from /detail.
                            "oi_usd": 0.0,
                            "funding": float(t.get("fundingRate") or 0),
                            "mark": mark,
                        },
                    )
                except (TypeError, ValueError):
                    continue
    except Exception as exc:
        logger.warning("liqmap mexc failed: %s", str(exc)[:100])
    return out


async def _kucoin_liquidation_snapshot() -> dict[str, dict[str, Any]]:
    """KuCoin futures active contracts: one call -> OI + funding."""
    out: dict[str, dict[str, Any]] = {}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://api-futures.kucoin.com/api/v1/contracts/active")
            for t in r.json().get("data") or []:
                sym = str(t.get("baseCurrency") or "")
                if not sym:
                    continue
                try:
                    mark = float(t.get("markPrice") or 0)
                    oi_coins = float(t.get("openInterest") or 0)  # in coins
                    lot = float(t.get("lotSize") or 1) or 1.0
                    multiplier = float(t.get("multiplier") or 1) or 1.0
                    out.setdefault(
                        sym,
                        {
                            "oi_usd": oi_coins * mark * multiplier * lot,
                            "funding": float(t.get("fundingFeeRate") or 0),
                            "mark": mark,
                        },
                    )
                except (TypeError, ValueError):
                    continue
    except Exception as exc:
        logger.warning("liqmap kucoin failed: %s", str(exc)[:100])
    return out
