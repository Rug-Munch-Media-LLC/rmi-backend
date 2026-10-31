"""Result skeleton and merge helpers for :meth:`TokenFullMixin.get_token_full`."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

_DEX_MERGE_KEYS = (
    "price_usd",
    "liquidity_usd",
    "volume_24h",
    "fdv",
    "market_cap",
    "price_change_1h",
    "price_change_24h",
    "created_at",
    "dex",
    "pair_address",
)


def token_full_skeleton(mint: str, chain: str) -> dict[str, Any]:
    """Return the zero-valued result dict for one token."""
    return {
        "address": mint,
        "chain": chain,
        "name": "",
        "symbol": "",
        "price_usd": None,
        "liquidity_usd": None,
        "volume_24h": None,
        "market_cap": None,
        "fdv": None,
        "holders": None,
        "price_change_1h": None,
        "price_change_24h": None,
        "price_change_7d": None,
        "trades_24h": None,
        "buy_24h": None,
        "sell_24h": None,
        "created_at": None,
        "dex": None,
        "pair_address": None,
        "security": None,
        "top_holders": [],
        "smart_money": None,
        "ohlcv": [],
        "source_chain": [],
        "timestamp": datetime.now(UTC).isoformat(),
    }


def merge_metadata(result: dict[str, Any], meta: Any) -> None:
    """Merge the metadata fetch result (skips exceptions and non-dicts)."""
    if isinstance(meta, dict) and not isinstance(meta, Exception):
        result["name"] = meta.get("name", result["name"])
        result["symbol"] = meta.get("symbol", result["symbol"])


def merge_price(result: dict[str, Any], price: Any) -> None:
    """Merge the price fetch result (skips exceptions and falsy values)."""
    if isinstance(price, (int, float)) and price:
        result["price_usd"] = float(price)


def merge_dexscreener(result: dict[str, Any], dex_data: Any) -> None:
    """Merge DexScreener data for the keys the skeleton still leaves unset."""
    if isinstance(dex_data, dict) and not isinstance(dex_data, Exception):
        for key in _DEX_MERGE_KEYS:
            if dex_data.get(key) is not None and result.get(key) is None:
                result[key] = dex_data[key]
        result["source_chain"].append("dexscreener")
