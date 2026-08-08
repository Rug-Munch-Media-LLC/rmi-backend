"""
Market Intelligence Data Providers
HTTP clients for CoinGecko, DexScreener, Polymarket, alternative.me, Binance, Hyperliquid.
All endpoints have in-memory caching and graceful fallbacks.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.logging import get_logger
from app.routers.market_intel.cache import (
    COINGECKO_API_KEY,
    COINGECKO_BASE,
    DEXSCREENER_BASE,
    _cget,
    _cset,
)
from app.routers.market_intel.utils import _safe_float

logger = get_logger(__name__)


# ── HTTP helpers ───────────────────────────────────────────
async def _aget(url: str, params=None, headers=None, timeout: int = 10) -> Any | None:
    """Async HTTP GET with error handling."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(url, params=params, headers=headers or {})
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        logger.warning(f"HTTP GET {url} failed: {e}")
    return None


# ── CoinGecko ─────────────────────────────────────────────
async def _cg(path: str, params=None, ttl: int = 120) -> Any:
    """Fetch from CoinGecko with caching."""
    key = f"cg:{path}:{json.dumps(params or {}, sort_keys=True)}"
    cached = _cget(key, ttl)
    if cached is not None:
        return cached
    headers = {}
    if COINGECKO_API_KEY:
        headers["x-cg-demo-api-key"] = COINGECKO_API_KEY
    data = await _aget(f"{COINGECKO_BASE}{path}", params=params, headers=headers)
    if data:
        _cset(key, data)
    return data


# ── Alternative.me Fear & Greed ───────────────────────────
async def _fng(ttl: int = 300) -> Any:
    """Fetch Fear & Greed Index."""
    cached = _cget("fng", ttl)
    if cached is not None:
        return cached
    data = await _aget("https://api.alternative.me/fng/?limit=1")
    if data:
        _cset("fng", data)
    return data


# ── Polymarket ────────────────────────────────────────────
async def _polymarket(limit: int = 6, ttl: int = 300) -> Any:
    """Fetch active Polymarket markets."""
    cached = _cget("pm", ttl)
    if cached is not None:
        return cached
    data = await _aget(
        "https://gamma-api.polymarket.com/markets",
        params={"limit": limit, "active": "true", "closed": "false"},
    )
    if data:
        _cset("pm", data)
    return data


# ── DexScreener ───────────────────────────────────────────
async def _ds(path: str, params=None, ttl: int = 60) -> Any:
    """Fetch from DexScreener with caching."""
    key = f"ds_{path}_{params}"
    cached = _cget(key, ttl)
    if cached is not None:
        return cached
    data = await _aget(f"{DEXSCREENER_BASE}{path}", params=params)
    if data:
        _cset(key, data)
    return data


# ── Binance ────────────────────────────────────────────────
async def _binance_funding(ttl: int = 120) -> Any:
    """Fetch Binance futures funding rates."""
    cached = _cget("bn_funding", ttl)
    if cached is not None:
        return cached
    data = await _aget("https://fapi.binance.com/fapi/v1/premiumIndex", params={"limit": 20})
    if data:
        _cset("bn_funding", data)
    return data


# ── Hyperliquid ────────────────────────────────────────────
async def _hyperliquid_markets(limit: int = 50, ttl: int = 120) -> list[Any]:
    """Hyperliquid perp markets with price, change, volume, OI, funding."""
    cached = _cget("hl_markets", ttl)
    if cached is not None:
        return cached[:limit]
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                "https://api.hyperliquid.xyz/info",
                json={"type": "metaAndAssetCtxs"},
                headers={"Content-Type": "application/json"},
            )
            if r.status_code == 200:
                data = r.json()
                if len(data) >= 2:
                    universe = data[0].get("universe", [])
                    ctxs = data[1]
                    markets = []
                    for i, asset in enumerate(universe):
                        if i < len(ctxs):
                            ctx = ctxs[i]
                            prev_px = float(ctx.get("prevDayPx", 1))
                            mark_px = float(ctx.get("markPx", 0))
                            change_24h = ((mark_px - prev_px) / prev_px * 100) if prev_px > 0 else 0
                            markets.append(
                                {
                                    "symbol": asset.get("name", ""),
                                    "price": mark_px,
                                    "change_24h": round(change_24h, 2),
                                    "volume_24h": _safe_float(ctx.get("dayNtlVlm")),
                                    "open_interest": _safe_float(ctx.get("openInterest")),
                                    "funding_rate": _safe_float(ctx.get("funding")),
                                    "funding_rate_pct": round(_safe_float(ctx.get("funding")) * 100, 4),
                                }
                            )
                    markets.sort(key=lambda x: x["volume_24h"], reverse=True)
                    _cset("hl_markets", markets)
                    return markets[:limit]
    except Exception as e:
        logger.warning(f"Hyperliquid markets fetch failed: {e}")
    return []


async def _hyperliquid_action(limit: int = 10, ttl: int = 60) -> dict[str, Any]:
    """Hyperliquid 'Gain/Loss Porn': Biggest movers and funding squeezes."""
    cached = _cget("hl_action", ttl)
    if cached is not None:
        return cached
    markets = await _hyperliquid_markets(limit=100)
    if not markets:
        return {}
    active = [m for m in markets if m["volume_24h"] >= 1_000_000]
    action_data = {
        "top_gainers": sorted([m for m in active if m["change_24h"] > 0], key=lambda x: x["change_24h"], reverse=True)[
            :limit
        ],
        "top_losers": sorted([m for m in active if m["change_24h"] < 0], key=lambda x: x["change_24h"])[:limit],
        "long_squeeze_highest_funding": sorted(
            [m for m in active if m["funding_rate"] > 0],
            key=lambda x: x["funding_rate"],
            reverse=True,
        )[:limit],
        "short_squeeze_lowest_funding": sorted(
            [m for m in active if m["funding_rate"] < 0], key=lambda x: x["funding_rate"]
        )[:limit],
        "highest_volume": sorted(active, key=lambda x: x["volume_24h"], reverse=True)[:limit],
    }
    _cset("hl_action", action_data)
    return action_data


async def _hyperliquid_funding(limit: int = 10, ttl: int = 120) -> list[Any]:
    """Top Hyperliquid funding rates (highest absolute)."""
    cached = _cget("hl_funding", ttl)
    if cached is not None:
        return cached[:limit]
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                "https://api.hyperliquid.xyz/info",
                json={"type": "metaAndAssetCtxs"},
                headers={"Content-Type": "application/json"},
            )
            if r.status_code == 200:
                data = r.json()
                if len(data) >= 2:
                    universe = data[0].get("universe", [])
                    ctxs = data[1]
                    funding_data = []
                    for i, asset in enumerate(universe):
                        if i < len(ctxs):
                            ctx = ctxs[i]
                            funding_rate = _safe_float(ctx.get("funding"))
                            funding_data.append(
                                {
                                    "symbol": asset.get("name", ""),
                                    "funding_rate": funding_rate,
                                    "funding_rate_pct": round(funding_rate * 100, 4),
                                    "price": _safe_float(ctx.get("markPx")),
                                    "open_interest": _safe_float(ctx.get("openInterest")),
                                }
                            )
                    funding_data.sort(key=lambda x: abs(x["funding_rate"]), reverse=True)
                    _cset("hl_funding", funding_data)
                    return funding_data[:limit]
    except Exception as e:
        logger.warning(f"Hyperliquid funding fetch failed: {e}")
    return []
