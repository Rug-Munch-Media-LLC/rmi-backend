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


# ── Stablecoin / mover filtering ──────────────────────────
# Flat stablecoins (~0.00% change) must never surface as "movers".
STABLECOIN_SYMBOLS: set[str] = {
    "USDT",
    "USDC",
    "DAI",
    "USDS",
    "TUSD",
    "FDUSD",
    "FRAX",
    "LUSD",
    "USDD",
    "USDP",
    "PYUSD",
    "GUSD",
    "BUSD",
    "CRVUSD",
    "SUSD",
}

# Known stablecoin identifiers: CoinGecko ids (the "address"/id field in
# /coins/markets rows) plus well-known EVM/Solana contract addresses.
STABLECOIN_ADDRESSES: set[str] = {
    # CoinGecko ids
    "tether",
    "usd-coin",
    "dai",
    "usds",
    "true-usd",
    "first-digital-usd",
    "frax",
    "liquity-usd",
    "usdd",
    "usdp",
    "paypal-usd",
    "gemini-dollar",
    "binance-usd",
    "crvusd",
    "synthetix-susd",
    # EVM contract addresses (lowercased)
    "0xdac17f958d2ee523a2206206994597c13d831ec7",  # USDT (ETH)
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",  # USDC (ETH)
    "0x6b175474e89094c44da98b954eedeac495271d0f",  # DAI (ETH)
    "0x8e870d67f660d95d5be530380d0ec0bd388289e1",  # USDP / PAX
    "0x4fabb145d64652a948d72533023f6e7a623c7c53",  # BUSD (ETH)
    "0x853d955acef822db058eb8505911ed77f175b99e",  # FRAX (ETH)
    "0x0000000000085d4780b73119b644ae5ecd22b376",  # TUSD (ETH)
    "0x5f98805a4e8be255a32880fdec8076726b333b49",  # LUSD (ETH)
    "0xf939e0a03fb07f59a73314e73794be0e57ac1b4e",  # crvUSD (ETH)
    "0x6c3ea9036406852006290770bedfcaba0e23a0e8",  # PYUSD (ETH)
    "0x056fd409e1d7a124bd7017459dfea2f387b6d5cd",  # GUSD (ETH)
    "0xc5f0f7b66764f6ec8c8dff7ba683102295e16409",  # FDUSD (ETH)
    "0xdc035d45d973e3ec169d2276ddab16f1e407384f",  # USDS (ETH)
    "0x57ab1ec28d129707052df4df418d58a2d46d5f51",  # sUSD (Synthetix, ETH)
    # Solana (base58, stored lowercase for case-insensitive compare)
    "es9vmfrzacermjfrf4h2fyd4kconky11mcce8benwnyb",  # USDT (Solana)
    "epjfwdd5aufqssqem2qn1xzybapc8g4weggkzwytdt1v",  # USDC (Solana)
}

# |change| below this % is not a "mover" — flat majors/stablecoins get dropped.
MOVER_MIN_CHANGE_PCT = 0.5


def _is_stablecoin(symbol: str, name: str = "", address: str = "") -> bool:
    """True for known stablecoins (by symbol, name hint, or known address)."""
    if (symbol or "").upper() in STABLECOIN_SYMBOLS:
        return True
    low_name = (name or "").lower()
    if "stable" in low_name or "tether" in low_name:
        return True
    return bool(address) and address.lower() in STABLECOIN_ADDRESSES


def _is_mover(market: dict[str, Any], change_key: str = "change_24h") -> bool:
    """Market qualifies as a mover: not a stablecoin and above the change floor."""
    if _is_stablecoin(market.get("symbol", ""), market.get("name", ""), market.get("address", "")):
        return False
    return abs(_safe_float(market.get(change_key))) >= MOVER_MIN_CHANGE_PCT


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
    # Real movers only: stablecoins and sub-threshold (flat) markets never
    # appear in gainers/losers. Funding/volume lists keep the full active set.
    movers = [m for m in active if _is_mover(m)]
    action_data = {
        "top_gainers": sorted([m for m in movers if m["change_24h"] > 0], key=lambda x: x["change_24h"], reverse=True)[
            :limit
        ],
        "top_losers": sorted([m for m in movers if m["change_24h"] < 0], key=lambda x: x["change_24h"])[:limit],
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
