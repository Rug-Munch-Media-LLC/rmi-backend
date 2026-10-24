"""DexScreener pair-snapshot adapter — keyless, pool-scoped, no history.

DexScreener publishes live pair state (price, 24h volume, liquidity), NOT
candles. This adapter therefore returns a single current-price candle so a
brand-new pool still renders one point, and the caller must surface
``degraded=true``: a one-candle chart is a price, not a trend.
"""

from __future__ import annotations

import time
from typing import Any

from .common import logger
from .ohlcv_schema import normalize_candles
from .ohlcv_source_http import REASON_EMPTY, REASON_UNSUPPORTED, fetch_json

DEXSCREENER_PAIRS_URL = "https://api.dexscreener.com/latest/dex/pairs/{chain}/{pair}"

DEXSCREENER_CHAINS = {
    "solana": "solana",
    "ethereum": "ethereum",
    "base": "base",
    "bsc": "bsc",
    "arbitrum": "arbitrum",
    "polygon": "polygon",
    "optimism": "optimism",
    "avalanche": "avalanche",
    "tron": "tron",
}


def _snapshot_candle(pair: dict[str, Any], bucket_seconds: int = 60) -> list[dict[str, Any]]:
    """Fold the live price into one flat candle at the current bucket."""
    try:
        price = float(pair.get("priceUsd") or 0)
    except (TypeError, ValueError) as exc:
        logger.debug("dropping unusable pair price: %s", str(exc)[:80])
        return []
    if price <= 0:
        return []
    now = int(time.time()) // bucket_seconds * bucket_seconds
    return normalize_candles([[now, price, price, price, price, 0.0]])


async def fetch_ohlcv_dexscreener_snapshot(chain: str, pair_address: str) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch one degraded candle from DexScreener's live pair snapshot."""
    dex_chain = DEXSCREENER_CHAINS.get(chain, chain)
    payload, reason = await fetch_json(DEXSCREENER_PAIRS_URL.format(chain=dex_chain, pair=pair_address))
    if reason is not None:
        return [], reason
    pairs = payload.get("pairs") if isinstance(payload, dict) else None
    if not pairs:
        return [], REASON_EMPTY
    pair = pairs[0]
    candles = _snapshot_candle(pair) if isinstance(pair, dict) else []
    return (candles, None) if candles else ([], REASON_UNSUPPORTED)
