"""GeckoTerminal pool trades — the keyless, multi-chain primary source.

``GET /networks/{network}/pools/{pool}/trades`` returns up to 300 real swaps
per call with tx hash, wallet, side, amounts and USD value. Verified live on
solana, base, eth, bsc and arbitrum (2026-10-24).

Rate limit is tight (~30 req/min per IP); the caller MUST cache. A 429 is
reported to the caller as "no data" so the fallback chain can continue.
"""

from __future__ import annotations

from typing import Any

from app.trades.http import get_json
from app.trades.schema import Trade, canon_chain, normalize_geckoterminal_trade

GT_BASE = "https://api.geckoterminal.com/api/v2"
MAX_ROWS = 300


async def fetch_pool_trades(
    *,
    chain: str,
    pool: str,
    token: str,
    base_token: str,
    limit: int = 100,
    min_usd: float = 0.0,
) -> list[Trade]:
    """Fetch and normalize real pool trades. Returns [] when the pool is empty."""
    if not pool:
        return []
    network = canon_chain(chain)
    data = await get_json(
        f"{GT_BASE}/networks/{network}/pools/{pool}/trades",
        params={"trade_volume_in_usd_greater_than": min_usd} if min_usd > 0 else None,
        source="geckoterminal.trades",
    )
    if not isinstance(data, dict):
        return []

    trades: list[Trade] = []
    for row in data.get("data") or []:
        trade = normalize_geckoterminal_trade(row, chain=network, token=token, pool=pool, base_token=base_token)
        if trade is not None:
            trades.append(trade)
    trades.sort(key=lambda t: t.timestamp_ms, reverse=True)
    return trades[: min(limit, MAX_ROWS)]


def dedupe(trades: list[Trade]) -> list[Trade]:
    """Drop duplicate (tx, wallet, side) rows, keeping the first occurrence.

    One tx can legitimately contain several swaps by the same wallet, so the
    amount is part of no key beyond side; we keep both sides of a tx distinct.
    """
    seen: set[tuple[str, str, str]] = set()
    out: list[Trade] = []
    for t in trades:
        key: tuple[str, str, str] = (t.tx, t.wallet, t.side)
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def mark_whales(trades: list[Trade], threshold_usd: float = 10_000.0) -> list[Trade]:
    """Flag trades above ``threshold_usd`` in place."""
    for t in trades:
        t.is_whale = t.amount_usd > threshold_usd
    return trades


def serialize(trades: list[Trade]) -> list[dict[str, Any]]:
    """JSON-safe representation for cache and response bodies."""
    return [t.model_dump() for t in trades]
