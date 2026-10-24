"""CoinGecko free/demo OHLC adapter — keyless, rate-limited, asset-scoped.

The free tier's ``/coins/{id}/ohlc`` returns no volume and its bucket width is
fixed by ``days`` (30-minute buckets for days=1), so it is a coarse history
source: good enough to draw a major's trend, never a pool-precise chart.
"""

from __future__ import annotations

import os
from typing import Any

from .ohlcv_schema import normalize_candles
from .ohlcv_source_http import REASON_EMPTY, REASON_UNSUPPORTED, fetch_json

COINGECKO_OHLC_URL = "https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc"

# Asset ids, not pools: WETH on Base is still "ethereum".
COINGECKO_IDS = {
    "solana": {
        "so11111111111111111111111111111111111111112": "solana",
        "epjfwdd5aufqssqem2qn1xzybapc8g4wegkzwytdt1v": "usd-coin",
    },
    "ethereum": {
        "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": "ethereum",
        "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": "bitcoin",
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "usd-coin",
    },
    "base": {
        "0x4200000000000000000000000000000000000006": "ethereum",
        "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf": "bitcoin",
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": "usd-coin",
    },
}

# days controls the returned bucket width on the free tier.
COINGECKO_DAYS = {"1m": 1, "5m": 1, "15m": 1, "1h": 1, "4h": 7, "1d": 30, "7d": 30, "30d": 90}


def _resolve_coin_id(chain: str, asset: str) -> str | None:
    """Map an address on ``chain`` (or a bare coin id) to a CoinGecko id."""
    cleaned = (asset or "").strip().lower()
    if not cleaned:
        return None
    mapped = COINGECKO_IDS.get(chain, {}).get(cleaned)
    if mapped:
        return mapped
    if cleaned.replace("-", "").isalpha() and len(cleaned) >= 3:
        return cleaned
    return None


def _rows_to_candles(payload: Any, limit: int) -> list[dict[str, Any]]:
    """CoinGecko rows are ``[ts_ms, open, high, low, close]`` with no volume."""
    if not isinstance(payload, list):
        return []
    candles = normalize_candles(
        [
            [int(row[0]) / 1000, row[1], row[2], row[3], row[4], 0.0]
            for row in payload
            if isinstance(row, list) and len(row) >= 5
        ]
    )
    return candles[-limit:] if limit > 0 else candles


async def fetch_ohlcv_coingecko(
    chain: str, asset: str, timeframe: str = "1h", limit: int = 100
) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch asset-scoped OHLC candles from the keyless CoinGecko API."""
    coin_id = _resolve_coin_id(chain, asset)
    if not coin_id:
        return [], REASON_UNSUPPORTED
    params: dict[str, Any] = {"vs_currency": "usd", "days": COINGECKO_DAYS.get(timeframe, 1)}
    key = os.getenv("COINGECKO_DEMO_API_KEY", "").strip()
    headers = {"x-cg-demo-api-key": key} if key else None
    payload, reason = await fetch_json(COINGECKO_OHLC_URL.format(coin_id=coin_id), params, headers)
    if reason is not None:
        return [], reason
    candles = _rows_to_candles(payload, limit)
    return (candles, None) if candles else ([], REASON_EMPTY)
