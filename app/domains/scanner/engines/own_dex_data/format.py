"""Data transformation — DexScreener-compatible pair building."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.core.logging import get_logger
from app.domains.scanner.engines.own_dex_data.client import (
    _load_pair_cache,
)

logger = get_logger(__name__)


def _build_pairs(
    swaps: list[dict[str, Any]],
    our_token: str,
    chain: str,
) -> list[dict[str, Any]]:
    """Build DexScreener-compatible pairs from our dex_events data.

    Groups swaps by pool (contract_address) and computes:
      - volume_24h: sum of amounts for our token in the pair
      - liquidity_usd: estimated from largest swap (proxy)
      - price_usd: volume-weighted average price
      - txns_24h: buy/sell count (determined by direction)
      - price_change_24h: relative change from first to last swap
    """
    pair_cache = _load_pair_cache(chain)

    def _get_tokens(pool_addr: str, dex_info: dict) -> tuple[str, str]:
        """Resolve token0/token1 for a pool, falling back to pair cache."""
        t0 = dex_info.get("token0", "")
        t1 = dex_info.get("token1", "")
        if t0 and t1:
            return (t0, t1)
        cached = pair_cache.get(pool_addr.lower())
        if cached:
            return cached
        return (t0, t1)

    # Group swaps by pool
    pools: dict[str, list[dict[str, Any]]] = {}
    for swap in swaps:
        pool = swap["contract_address"]
        if pool not in pools:
            pools[pool] = []
        pools[pool].append(swap)

    pairs: list[dict[str, Any]] = []
    for pool_addr, pool_swaps in pools.items():
        if not pool_swaps:
            continue

        dex_info = pool_swaps[0]
        dex_name = f"{dex_info['dex']}_{dex_info['version']}"

        resolved_t0, resolved_t1 = _get_tokens(pool_addr, dex_info)

        is_token0 = resolved_t0.lower() == our_token

        volume_24h = 0.0
        buy_count = 0
        sell_count = 0
        prices_weighted: list[tuple[float, float]] = []

        for swap in pool_swaps:
            if is_token0:
                amt_in = float(swap["amount0_in"] or 0)
                amt_out = float(swap["amount0_out"] or 0)
                paired_in = float(swap["amount1_in"] or 0)
                paired_out = float(swap["amount1_out"] or 0)
            else:
                amt_in = float(swap["amount1_in"] or 0)
                amt_out = float(swap["amount1_out"] or 0)
                paired_in = float(swap["amount0_in"] or 0)
                paired_out = float(swap["amount0_out"] or 0)

            volume_24h += amt_in + amt_out

            is_buy = float(swap["amount0_out"] or 0) > 0 if is_token0 else float(swap["amount1_out"] or 0) > 0

            if is_buy:
                buy_count += 1
            else:
                sell_count += 1

            if amt_in + amt_out > 0:
                if is_token0:
                    if amt_out > 0:
                        price = (paired_in or 1) / amt_out if amt_out else 0
                    else:
                        price = (paired_out or 1) / amt_in if amt_in else 0
                else:
                    if amt_out > 0:
                        price = (paired_in or 1) / amt_out if amt_out else 0
                    else:
                        price = (paired_out or 1) / amt_in if amt_in else 0

                if price > 0 and price < 1_000_000:
                    prices_weighted.append((price, amt_in + amt_out))

        price_usd = 0.0
        if prices_weighted:
            total_weight = sum(w for _, w in prices_weighted)
            if total_weight > 0:
                price_usd = sum(p * w for p, w in prices_weighted) / total_weight

        price_change_24h = 0.0
        if len(prices_weighted) > 1:
            first_price = prices_weighted[0][0]
            last_price = prices_weighted[-1][0]
            if first_price > 0:
                price_change_24h = ((last_price - first_price) / first_price) * 100

        max_swap_volume = (
            max(
                (
                    float(s["amount0_in"] or 0) + float(s["amount0_out"] or 0)
                    if is_token0
                    else float(s["amount1_in"] or 0) + float(s["amount1_out"] or 0)
                )
                for s in pool_swaps
            )
            if pool_swaps
            else 0
        )
        liquidity_est = max(max_swap_volume * 100, volume_24h * 0.3)

        pair_entry = {
            "chainId": chain,
            "dexId": dex_name,
            "pairAddress": pool_addr,
            "priceUsd": str(round(price_usd, 18)) if price_usd > 0 else "0",
            "priceNative": "",
            "volume": {
                "h24": round(volume_24h, 2),
                "h6": round(volume_24h * 0.25, 2),
                "h1": round(volume_24h * 0.04, 2),
            },
            "liquidity": {
                "usd": round(liquidity_est, 2),
                "base": str(round(volume_24h * 0.5, 2)),
                "quote": str(round(volume_24h * 0.5, 2)),
            },
            "txns": {
                "h24": {
                    "buys": buy_count,
                    "sells": sell_count,
                }
            },
            "priceChange": {
                "h24": round(price_change_24h, 2),
            },
            "labels": ["v3" if "v3" in dex_name else "v2"],
            "fdv": 0,
        }

        pairs.append(pair_entry)

    pairs.sort(key=lambda p: p["volume"]["h24"], reverse=True)
    return pairs


def _parse_decimal(value: Any) -> Decimal | None:
    """Safely parse a decimal value from ClickHouse."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ValueError, TypeError, ArithmeticError):
        return None
