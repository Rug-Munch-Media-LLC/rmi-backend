"""Live Trades Router — real onchain swaps for any token.

Endpoints:
  GET /api/v1/trades/{chain}/{address}         — recent trades (normalized)
  GET /api/v1/trades/{chain}/{address}/stats   — stats over the recent sample
  GET /api/v1/trades/{chain}/{address}/stream  — poll/cursor stream for the tape
  GET /api/v1/trades/feed                      — multi-token live tape

Every trade is a real swap from a real source. DexScreener is NOT a source:
it exposes 24h aggregates and no public trades endpoint, so deriving trades
from it (the previous implementation) fabricated tx hashes and prices.

Sources per chain live in ``app/trades/service.py``; GeckoTerminal (keyless)
is primary, then Solana Tracker / Birdeye (key-gated), then GMGN (opt-in).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.core.logging import get_logger
from app.core.rate_limiter import get_rate_limit_dependency
from app.trades.schema import Trade, canon_chain
from app.trades.service import get_token_trades as fetch_token_trades

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1/trades", tags=["trades"])

_RL = get_rate_limit_dependency("trades1", rate=2.0, burst=6)


def _stats(trades: list[Trade]) -> dict[str, Any]:
    """Stats over the returned sample — never extrapolated to 24h."""
    if not trades:
        return {
            "sample_size": 0,
            "scope": "recent_sample",
            "buy_count": 0,
            "sell_count": 0,
            "volume_usd": 0.0,
            "avg_trade_usd": 0.0,
            "largest_trade_usd": 0.0,
            "whale_trades": 0,
            "whale_volume_usd": 0.0,
            "buy_sell_ratio": 0.0,
        }
    buys = [t for t in trades if t.side == "buy"]
    sells = [t for t in trades if t.side == "sell"]
    usd = [t.amount_usd for t in trades]
    whales = [t for t in trades if t.is_whale]
    return {
        "sample_size": len(trades),
        "scope": "recent_sample",
        "buy_count": len(buys),
        "sell_count": len(sells),
        "volume_usd": round(sum(usd), 2),
        "avg_trade_usd": round(sum(usd) / len(usd), 2),
        "largest_trade_usd": round(max(usd), 2),
        "whale_trades": len(whales),
        "whale_volume_usd": round(sum(t.amount_usd for t in whales), 2),
        "buy_sell_ratio": round(len(buys) / max(len(sells), 1), 2),
    }


@router.get("/feed", dependencies=[Depends(_RL)])
async def get_trade_feed(
    chain: str = Query(default="solana"),
    tokens: str = Query(default="", description="Comma-separated token addresses"),
    min_amount: float = Query(default=1000, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """Live tape across several tokens. Requires ``tokens`` — no invented feed."""
    wanted = [t.strip() for t in tokens.split(",") if t.strip()][:10]
    if not wanted:
        return {
            "chain": canon_chain(chain),
            "feed": [],
            "sources": [],
            "error": "tokens_required",
            "detail": "Pass ?tokens=addr1,addr2 — an unfiltered feed is not synthesizable.",
            "updated_at": datetime.now(UTC).isoformat(),
        }

    feed: list[dict[str, Any]] = []
    sources: list[str] = []
    for token in wanted:
        result = await fetch_token_trades(
            chain=canon_chain(chain), token=token, limit=limit, min_usd=min_amount
        )
        sources.append(result.source)
        for t in result.trades:
            row = t.model_dump()
            row["token_address"] = token
            feed.append(row)
    feed.sort(key=lambda r: r["timestamp_ms"], reverse=True)
    feed = feed[:limit]
    return {
        "chain": canon_chain(chain),
        "feed": feed,
        "sources": sorted(set(sources)),
        "whale_count": sum(1 for r in feed if r["is_whale"]),
        "updated_at": datetime.now(UTC).isoformat(),
    }


@router.get("/{chain}/{address}", dependencies=[Depends(_RL)])
async def get_token_trades(
    chain: str,
    address: str,
    limit: int = Query(default=100, ge=1, le=300),
    min_amount: float = Query(default=0, ge=0, description="Minimum USD per trade"),
    side: str = Query(default="all", pattern=r"^(buy|sell|all)$"),
) -> dict[str, Any]:
    """Recent real trades for a token, newest first."""
    result = await fetch_token_trades(
        chain=canon_chain(chain), token=address, limit=limit, min_usd=min_amount
    )
    trades = result.trades
    if side != "all":
        trades = [t for t in trades if t.side == side]
    return {
        "chain": canon_chain(chain),
        "address": address,
        "trades": [t.model_dump() for t in trades],
        "stats": _stats(trades),
        "source": result.source,
        "cached": result.cached,
        "sources_tried": result.tried,
        "updated_at": datetime.now(UTC).isoformat(),
    }


@router.get("/{chain}/{address}/stats", dependencies=[Depends(_RL)])
async def get_trade_stats(chain: str, address: str) -> dict[str, Any]:
    """Aggregate stats over the recent trade sample."""
    result = await fetch_token_trades(chain=canon_chain(chain), token=address, limit=300)
    return {
        "chain": canon_chain(chain),
        "address": address,
        "stats": _stats(result.trades),
        "source": result.source,
        "cached": result.cached,
        "updated_at": datetime.now(UTC).isoformat(),
    }


@router.get("/{chain}/{address}/stream", dependencies=[Depends(_RL)])
async def stream_trades(
    chain: str,
    address: str,
    since_ms: int = Query(default=0, ge=0, description="Return only trades newer than this"),
    limit: int = Query(default=50, ge=1, le=300),
) -> dict[str, Any]:
    """Cursor-based poll for the live tape.

    Callers pass back ``next_since_ms`` from the previous response. Trades are
    deduped server-side by (tx, wallet, side), so a re-poll cannot duplicate.
    """
    result = await fetch_token_trades(chain=canon_chain(chain), token=address, limit=limit)
    fresh = [t for t in result.trades if t.timestamp_ms > since_ms] if since_ms else result.trades
    fresh.sort(key=lambda t: t.timestamp_ms)
    next_since = max((t.timestamp_ms for t in result.trades), default=since_ms)
    return {
        "chain": canon_chain(chain),
        "address": address,
        "trades": [t.model_dump() for t in fresh[:limit]],
        "next_since_ms": next_since,
        "source": result.source,
        "cached": result.cached,
        "updated_at": datetime.now(UTC).isoformat(),
    }
