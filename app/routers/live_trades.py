"""
Live Trades Router — Real-time trade data for tokens.
======================================================
Endpoints:
  GET /api/v1/trades/{chain}/{address} — Recent trades with whale detection
  GET /api/v1/trades/{chain}/{address}/stats — Trade stats (volume, buys/sells, avg size)
  GET /api/v1/trades/feed — Real-time trade feed (WebSocket-compatible poll)

Data sources:
  1. GMGN (primary — if key available)
  2. DexScreener trades endpoint (free fallback)

Redis-backed with 5-min TTL. Whale trades (>$10K) are highlighted.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.core.logging import get_logger
from app.core.redis import get_redis_async

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1/trades", tags=["trades"])

TRADE_CACHE_TTL = 300  # 5 minutes
FEED_CACHE_TTL = 30  # 30 seconds for feed


class TradeEntry(BaseModel):
    tx_hash: str = ""
    timestamp: str = ""
    side: str = "buy"
    price_usd: float = 0
    amount_usd: float = 0
    amount_token: float = 0
    maker: str = ""
    is_whale: bool = False
    source: str = "dexscreener"


class TradeStats(BaseModel):
    total_trades: int = 0
    buy_count: int = 0
    sell_count: int = 0
    total_volume_usd: float = 0
    avg_trade_usd: float = 0
    whale_trades: int = 0
    whale_volume_usd: float = 0
    largest_trade_usd: float = 0
    source: str = "dexscreener"


# ── Helpers ──


def _detect_whale_trades(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flag trades over $10K as whale trades."""
    for t in trades:
        t["is_whale"] = float(t.get("amount_usd", 0)) > 10000
    return trades


def _compute_stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute trade statistics from trade list."""
    if not trades:
        return TradeStats().model_dump()

    buys = [t for t in trades if t.get("side") == "buy"]
    sells = [t for t in trades if t.get("side") == "sell"]
    amounts = [float(t.get("amount_usd", 0)) for t in trades]
    whales = [t for t in trades if t.get("is_whale")]
    whale_amounts = [float(t.get("amount_usd", 0)) for t in whales]

    return {
        "total_trades": len(trades),
        "buy_count": len(buys),
        "sell_count": len(sells),
        "total_volume_usd": round(sum(amounts), 2),
        "avg_trade_usd": round(sum(amounts) / len(amounts), 2) if amounts else 0,
        "whale_trades": len(whales),
        "whale_volume_usd": round(sum(whale_amounts), 2),
        "largest_trade_usd": round(max(amounts), 2) if amounts else 0,
        "buy_sell_ratio": round(len(buys) / max(len(sells), 1), 2),
    }


async def _fetch_trades_from_sources(address: str, chain: str, limit: int) -> list[dict[str, Any]]:
    """Fetch trades from all available sources, prioritizing GMGN."""
    trades: list[dict[str, Any]] = []

    # 1. Try GMGN
    try:
        from app.databus.gmgn_provider import get_gmgn_provider

        gmgn = get_gmgn_provider()
        gmgn_trades = await gmgn.get_token_trades(address, chain, limit)
        if gmgn_trades:
            trades = gmgn_trades
            logger.debug("trades_from_gmgn count=%d", len(trades))
    except Exception as e:
        logger.debug("gmgn_trades_failed: %s", e)

    # 2. Fallback to DexScreener
    if not trades:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                # Get pair address first
                resp = await client.get(f"https://api.dexscreener.com/latest/dex/tokens/{address}")
                if resp.status_code == 200:
                    pairs = resp.json().get("pairs", [])
                    if pairs:
                        pair_addr = pairs[0].get("pairAddress", "")
                        if pair_addr:
                            trades_resp = await client.get(
                                f"https://io.dexscreener.com/dex/log/latest/dex/trades/{pair_addr}"
                            )
                            if trades_resp.status_code == 200:
                                raw_trades = trades_resp.json()
                                if isinstance(raw_trades, list):
                                    for t in raw_trades[:limit]:
                                        trades.append(
                                            {
                                                "tx_hash": t.get("txHash", ""),
                                                "timestamp": t.get("time", ""),
                                                "side": "buy" if t.get("type") in ("buy", "b") else "sell",
                                                "price_usd": float(t.get("priceUsd", 0)),
                                                "amount_usd": float(
                                                    t.get("volume", {}).get("usd", t.get("amountUsd", 0))
                                                ),
                                                "amount_token": float(t.get("amount", 0)),
                                                "maker": t.get("maker", ""),
                                                "source": "dexscreener",
                                            }
                                        )
                                    logger.debug("trades_from_dexscreener count=%d", len(trades))
        except Exception as e:
            logger.warning("dexscreener_trades_failed: %s", e)

    return trades


# ── Routes ──


@router.get("/{chain}/{address}", summary="Get recent trades for a token")
async def get_token_trades(
    chain: str,
    address: str,
    limit: int = Query(default=20, ge=1, le=100),
    min_amount: float = Query(default=0, ge=0, description="Filter: minimum USD trade amount"),
    side: str = Query(default="all", pattern=r"^(buy|sell|all)$"),
) -> dict[str, Any]:
    """
    Get recent trades for a token.

    - **chain**: solana, ethereum, bsc, polygon, etc.
    - **address**: Token contract address
    - **limit**: Number of trades (1-100, default 20)
    - **min_amount**: Filter trades above this USD amount (whale filter)
    - **side**: Filter by trade side (buy/sell/all)

    Returns trades with whale flags, stats, and source info.
    """
    cache_key = f"trades:{chain}:{address}:{limit}"

    # Try Redis cache
    try:
        r = get_redis_async()
        if r:
            cached = await r.get(cache_key)
            if cached:
                trades = json.loads(cached)
                # Apply client-side filters
                trades = _apply_filters(trades, min_amount, side)
                return {
                    "address": address,
                    "chain": chain,
                    "trades": trades,
                    "stats": _compute_stats(trades),
                    "cached": True,
                    "source": trades[0].get("source", "cache") if trades else "cache",
                    "updated_at": datetime.now(UTC).isoformat(),
                }
    except Exception:
        pass

    # Fetch from sources
    trades = await _fetch_trades_from_sources(address, chain, limit)
    trades = _detect_whale_trades(trades)

    # Cache in Redis
    try:
        r = get_redis_async()
        if r:
            await r.setex(cache_key, TRADE_CACHE_TTL, json.dumps(trades))
    except Exception:
        pass

    # Apply filters
    trades = _apply_filters(trades, min_amount, side)

    source = trades[0].get("source", "unknown") if trades else "none"
    return {
        "address": address,
        "chain": chain,
        "trades": trades,
        "stats": _compute_stats(trades),
        "cached": False,
        "source": source,
        "updated_at": datetime.now(UTC).isoformat(),
    }


@router.get("/{chain}/{address}/stats", summary="Get trade statistics for a token")
async def get_trade_stats(
    chain: str,
    address: str,
) -> dict[str, Any]:
    """
    Get aggregated trade statistics: total trades, volume, buys/sells, whale activity.
    Uses the same cache as the trades endpoint.
    """
    cache_key = f"trades:{chain}:{address}:50"

    try:
        r = get_redis_async()
        if r:
            cached = await r.get(cache_key)
            if cached:
                trades = json.loads(cached)
                return {
                    "address": address,
                    "chain": chain,
                    "stats": _compute_stats(trades),
                    "cached": True,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
    except Exception:
        pass

    trades = await _fetch_trades_from_sources(address, chain, 50)
    trades = _detect_whale_trades(trades)

    try:
        r = get_redis_async()
        if r:
            await r.setex(cache_key, TRADE_CACHE_TTL, json.dumps(trades))
    except Exception:
        pass

    return {
        "address": address,
        "chain": chain,
        "stats": _compute_stats(trades),
        "cached": False,
        "updated_at": datetime.now(UTC).isoformat(),
    }


@router.get("/feed", summary="Live trade feed for monitored tokens")
async def get_trade_feed(
    chain: str = Query(default="solana", description="Blockchain to monitor"),
    min_amount: float = Query(default=1000, ge=0, description="Minimum USD trade to show"),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """
    Aggregated live trade feed across all monitored tokens.

    Returns the most recent trades across active tokens, highlighting whale trades.
    Useful for dashboards and real-time monitoring.
    """
    cache_key = f"trade_feed:{chain}:{min_amount}:{limit}"

    try:
        r = get_redis_async()
        if r:
            cached = await r.get(cache_key)
            if cached:
                return {
                    "feed": json.loads(cached),
                    "cached": True,
                    "chain": chain,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
    except Exception:
        pass

    # Get active/trending tokens from DexScreener
    trades_feed: list[dict[str, Any]] = []
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.dexscreener.com/latest/dex/search",
                params={"q": chain.upper() if chain != "solana" else "SOL"},
            )
            if resp.status_code == 200:
                pairs = resp.json().get("pairs", [])[:10]
                for pair in pairs:
                    pair_addr = pair.get("pairAddress", "")
                    token_addr = pair.get("baseToken", {}).get("address", "")
                    if not pair_addr or not token_addr:
                        continue

                    try:
                        trades_resp = await client.get(
                            f"https://io.dexscreener.com/dex/log/latest/dex/trades/{pair_addr}"
                        )
                        if trades_resp.status_code == 200:
                            raw_trades = trades_resp.json()
                            if isinstance(raw_trades, list):
                                for t in raw_trades:
                                    amount_usd = float(t.get("volume", {}).get("usd", t.get("amountUsd", 0)))
                                    if amount_usd >= min_amount:
                                        trades_feed.append(
                                            {
                                                "tx_hash": t.get("txHash", ""),
                                                "token_address": token_addr,
                                                "token_symbol": pair.get("baseToken", {}).get("symbol", ""),
                                                "pair_address": pair_addr,
                                                "timestamp": t.get("time", ""),
                                                "side": "buy" if t.get("type") in ("buy", "b") else "sell",
                                                "price_usd": float(t.get("priceUsd", 0)),
                                                "amount_usd": amount_usd,
                                                "maker": t.get("maker", ""),
                                                "is_whale": amount_usd > 10000,
                                                "source": "dexscreener",
                                            }
                                        )
                    except Exception:
                        continue
    except Exception as e:
        logger.warning("trade_feed_fetch_failed: %s", e)

    # Sort by recency and limit
    trades_feed.sort(key=lambda t: str(t.get("timestamp", "")), reverse=True)
    trades_feed = trades_feed[:limit]

    # Cache
    try:
        r = get_redis_async()
        if r:
            await r.setex(cache_key, FEED_CACHE_TTL, json.dumps(trades_feed))
    except Exception:
        pass

    return {
        "feed": trades_feed,
        "cached": False,
        "chain": chain,
        "whale_count": sum(1 for t in trades_feed if t.get("is_whale")),
        "updated_at": datetime.now(UTC).isoformat(),
    }


def _apply_filters(trades: list[dict[str, Any]], min_amount: float, side: str) -> list[dict[str, Any]]:
    """Apply client-side filters to trade list."""
    if min_amount > 0:
        trades = [t for t in trades if float(t.get("amount_usd", 0)) >= min_amount]
    if side != "all":
        trades = [t for t in trades if t.get("side") == side]
    return trades
