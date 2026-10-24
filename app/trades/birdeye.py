"""Birdeye token-trades adapter (key-gated, Solana + a few EVM chains).

Endpoint: ``GET /defi/v2/token/trades`` on ``https://public-api.birdeye.so``,
header ``X-API-KEY`` (+ ``x-chain``). Requires ``BIRDEYE_API_KEY``.
Disabled (returns []) without a key — we never invent a trade.
"""

from __future__ import annotations

import os
from typing import Any

from app.trades.http import get_json
from app.trades.schema import Trade, canon_chain

BIRDEYE_BASE = "https://public-api.birdeye.so"
# Birdeye's x-chain values differ from GeckoTerminal network ids.
_X_CHAIN = {"eth": "ethereum", "arbitrum": "arbitrum", "bsc": "bsc", "polygon": "polygon"}


async def fetch_birdeye_trades(*, chain: str, token: str, limit: int = 100) -> list[Trade]:
    """Return Birdeye trades for one token, or [] when unavailable."""
    key = os.getenv("BIRDEYE_API_KEY", "").strip()
    if not key:
        return []
    network = canon_chain(chain)
    data: Any = await get_json(
        f"{BIRDEYE_BASE}/defi/v2/token/trades",
        params={
            "address": token,
            "limit": min(limit, 100),
            "offset": 0,
            "sort_by": "block_unix_time",
        },
        headers={"X-API-KEY": key, "x-chain": _X_CHAIN.get(network, network)},
        source="birdeye",
    )
    if not isinstance(data, dict):
        return []

    rows = (data.get("data") or {}).get("items") or []
    trades: list[Trade] = []
    for t in rows:
        tx = t.get("txHash") or t.get("tx_hash") or t.get("signature") or ""
        if not tx:
            continue
        side_raw = str(t.get("side") or "").lower()
        side = "buy" if side_raw.startswith("b") else "sell"
        wallet = t.get("owner") or t.get("from") or t.get("maker") or ""
        ts = t.get("blockUnixTime") or t.get("block_unix_time") or 0
        trades.append(
            Trade(
                chain=network,
                token=token,
                tx=tx,
                side=side,
                amount_token=_num(t.get("amount") or t.get("tokenAmount")),
                amount_usd=_num(t.get("volumeUSD") or t.get("volume_usd")),
                price_usd=_num(t.get("priceUsd") or t.get("price")),
                wallet=wallet,
                maker=wallet,
                timestamp=str(ts),
                timestamp_ms=_ms(ts),
                pool=t.get("poolId") or t.get("pool_id") or "",
                source="birdeye",
            )
        )
    return trades[:limit]


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _ms(v: Any) -> int:
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return 0
    return n * 1000 if n < 10_000_000_000 else n
