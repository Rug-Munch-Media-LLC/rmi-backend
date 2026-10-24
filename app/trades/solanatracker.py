"""Solana Tracker trades adapter (key-gated).

Endpoint: ``GET https://data.solanatracker.io/trades/{mint}`` with ``x-api-key``.
Requires ``SOLANATRACKER_API_KEY``; free tier is ~5k requests/month. Solana only.
Disabled (returns []) when the key is absent — we never invent a trade.
"""

from __future__ import annotations

import os
from typing import Any

from app.trades.http import get_json
from app.trades.numeric import as_number, epoch_ms
from app.trades.schema import Trade, canon_chain

ST_BASE = "https://data.solanatracker.io"


async def fetch_solanatracker_trades(*, chain: str, token: str, limit: int = 100) -> list[Trade]:
    """Return Solana Tracker trades for one mint, or [] when unavailable."""
    if canon_chain(chain) != "solana":
        return []
    key = os.getenv("SOLANATRACKER_API_KEY", "").strip()
    if not key:
        return []

    data: Any = await get_json(
        f"{ST_BASE}/trades/{token}",
        params={"limit": min(limit, 100)},
        headers={"x-api-key": key},
        source="solanatracker",
    )
    if not isinstance(data, dict):
        return []

    rows = data.get("trades") or data.get("data") or []
    trades: list[Trade] = []
    for t in rows if isinstance(rows, list) else []:
        tx = t.get("tx") or t.get("signature") or t.get("txHash") or ""
        if not tx:
            continue
        side = "buy" if str(t.get("type") or t.get("side") or "").lower().startswith("b") else "sell"
        usd = as_number(t.get("volume") if t.get("volume") is not None else t.get("amountUsd"))
        trades.append(
            Trade(
                chain="solana",
                token=token,
                tx=tx,
                side=side,
                amount_token=as_number(t.get("amount") or t.get("tokenAmount")),
                amount_usd=usd,
                price_usd=as_number(t.get("price") or t.get("priceUsd")),
                wallet=t.get("wallet") or t.get("from") or "",
                maker=t.get("wallet") or t.get("from") or "",
                timestamp=str(t.get("time") or t.get("blockUnixTime") or ""),
                timestamp_ms=epoch_ms(t.get("time") or t.get("blockUnixTime")),
                pool=t.get("pool") or t.get("pairAddress") or "",
                source="solanatracker",
            )
        )
    return trades[:limit]

