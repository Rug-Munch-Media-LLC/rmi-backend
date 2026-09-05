"""Trade-tape / whale signal functions for RugCharts (ClickHouse dex_events)."""

from __future__ import annotations

from typing import Any

from .common import RECENT_TRADES_TTL, WHALE_MIN_USD, WHALE_TTL, _cache_get_json, _cache_set_json, _ch_query, logger


def _whale_side(
    token: str,
    token0: str,
    token1: str,
    amount0_in: Any,
    amount1_in: Any,
    amount0_out: Any,
    amount1_out: Any,
) -> str:
    """Derive a whale trade's side for a target token.

    Uniswap V2 Swap semantics: exactly one leg flows IN and the other OUT.
      - a0_out > 0 → token0 bought → token0: BUY, token1: SELL
      - a1_out > 0 → token1 bought → token1: BUY, token0: SELL
    V3 swaps store absolute amounts (no out-flow) → side unknowable.
    """
    try:
        a0_out = float(amount0_out or 0)
        a1_out = float(amount1_out or 0)
    except (TypeError, ValueError):
        return "unknown"

    token_l = (token or "").lower()
    token0_l = (token0 or "").lower()
    token1_l = (token1 or "").lower()

    if a0_out > 0:
        return "buy" if token_l == token0_l else "sell" if token_l == token1_l else "unknown"
    if a1_out > 0:
        return "buy" if token_l == token1_l else "sell" if token_l == token0_l else "unknown"
    return "unknown"


async def _fetch_recent_trades_clickhouse(
    token_address: str,
    chain: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Latest dex swaps touching a token — feeds /ws/trades live tape.

    Unlike the whale query this has NO whale/min_usd filter: the tape shows
    every trade, labeled by classifier.py. Short TTL (5s) keeps polling
    viewers fresh without hammering ClickHouse.
    """
    token_l = (token_address or "").lower()
    if not token_l or not chain:
        return []

    cache_key = f"rugcharts:tape:{chain}:{token_l}:{limit}"
    cached = await _cache_get_json(cache_key)
    if cached:
        return cached

    sql = (
        "SELECT toUnixTimestamp(block_timestamp), price_usd, volume_usd, "
        "tx_hash, token0, token1, recipient, "
        "amount0_in, amount1_in, amount0_out, amount1_out "
        "FROM dex_events "
        "WHERE chain = %(chain)s "
        "AND (lower(token0) = %(token)s OR lower(token1) = %(token)s) "
        "ORDER BY block_timestamp DESC LIMIT %(limit)s"
    )
    try:
        rows = await _ch_query(sql, {"chain": chain, "token": token_l, "limit": int(limit)})
    except Exception as e:
        logger.debug(f"recent_trades_query_failed: {e}")
        return []

    trades: list[dict[str, Any]] = []
    for row in rows:
        trades.append(
            {
                "time": int(row[0]),
                "price_usd": float(row[1] or 0),
                "volume_usd": float(row[2] or 0),
                "tx_hash": row[3] or "",
                "token0": row[4] or "",
                "token1": row[5] or "",
                "wallet": row[6] or "",
                "side": _whale_side(token_l, row[4] or "", row[5] or "", row[7], row[8], row[9], row[10]),
                "chain": chain,
            }
        )

    if trades:
        await _cache_set_json(cache_key, trades, RECENT_TRADES_TTL)

    return trades


async def _fetch_whale_trades_clickhouse(
    token_address: str,
    chain: str,
    limit: int = 100,
    min_usd: float = WHALE_MIN_USD,
) -> list[dict[str, Any]]:
    """Fetch large DEX swaps for a token from chain-indexer ClickHouse.

    Queries ``wallet_memory.dex_events`` for events where the token is on
    either side of the pair and USD notional >= min_usd. Each marker carries
    ``wallet`` (taker proxy = output receiver — heuristic attribution, see
    classifier.py) alongside time/price/side/tx_hash.
    """
    token_l = (token_address or "").lower()
    if not token_l or not chain:
        return []

    cache_key = f"rugcharts:whales:{chain}:{token_l}:{limit}:{min_usd}"
    cached = await _cache_get_json(cache_key)
    if cached:
        return cached

    sql_base = (
        "SELECT toUnixTimestamp(block_timestamp), price_usd, volume_usd, "
        "tx_hash, token0, token1, recipient, "
        "amount0_in, amount1_in, amount0_out, amount1_out "
        "FROM dex_events "
        "WHERE chain = %(chain)s AND whale = 1 "
    )
    sql = sql_base + (
        "AND volume_usd >= %(min_usd)s "
        "AND (lower(token0) = %(token)s OR lower(token1) = %(token)s) "
        "ORDER BY block_timestamp DESC LIMIT %(limit)s"
        if min_usd > 0
        else "AND (lower(token0) = %(token)s OR lower(token1) = %(token)s) "
        "ORDER BY block_timestamp DESC LIMIT %(limit)s"
    )
    params: dict[str, Any] = {"chain": chain, "token": token_l, "limit": int(limit)}
    if min_usd > 0:
        params["min_usd"] = float(min_usd)

    try:
        rows = await _ch_query(sql, params)
    except Exception as e:
        logger.debug(f"whale_trades_query_failed: {e}")
        return []

    trades: list[dict[str, Any]] = []
    for row in rows:
        trades.append(
            {
                "time": int(row[0]),
                "price_usd": float(row[1] or 0),
                "volume_usd": float(row[2] or 0),
                "tx_hash": row[3] or "",
                "token0": row[4] or "",
                "token1": row[5] or "",
                "wallet": row[6] or "",
                "side": _whale_side(token_l, row[4] or "", row[5] or "", row[7], row[8], row[9], row[10]),
                "chain": chain,
            }
        )

    if trades:
        await _cache_set_json(cache_key, trades, WHALE_TTL)

    return trades
