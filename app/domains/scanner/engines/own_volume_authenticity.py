"""
Own Volume Authenticity — ClickHouse-Powered Wash Trading Detection
====================================================================
Phase 4 of the Data Ownership Strategy.

Assesses how authentic a token's reported DEX volume is by comparing it
against actual on-chain activity and hunting wash-trading patterns:
  - DEX-reported volume (from dex_events) vs on-chain transfer volume
    (from evm_transfers)
  - Multi-swap transactions (circular routing inside one tx)
  - Self-swaps (sender == recipient)
  - Sender concentration (top few wallets driving most swaps)
  - Zero-net cycling (wallets that buy AND sell to net ~0)

Architecture:
  - Reuses own_dex_data swap queries for the DEX side
  - Direct evm_transfers aggregation for the transfer side
  - Returns an authentic_score (0-100) with wash signals and flags
  - Falls back gracefully when data is insufficient

Usage:
    from app.domains.scanner.engines.own_volume_authenticity import (
        get_own_volume_authenticity,
    )

    result = await get_own_volume_authenticity("0x...", "ethereum")
    # result = {
    #     "authentic_score": 78,
    #     "fake_volume_pct": 22.0,
    #     "risk_level": "low",
    #     "wash_signals": [{"name": "...", "score": 0.1, "detail": "..."}],
    #     "volume_comparison": {
    #         "dex_volume": 100000.0,
    #         "transfer_volume": 95000.0,
    #         "ratio": 1.05,
    #     },
    #     "flags": [],
    #     "confidence": 0.7,
    #     "source": "own",
    # }

Data Sources:
  Primary: wallet_memory.dex_events (swap events)
  Primary: wallet_memory.evm_transfers (transfer events)
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from app.core.logging import get_logger
from app.domains.scanner.engines._common import (
    clear_cache as _common_clear_cache,
    get_cache_stats as _common_get_cache_stats,
    run_sync,
)
from app.domains.scanner.engines.own_dex_data.client import (
    _get_pools_for_token,
    _load_pair_cache,
)
from app.domains.scanner.engines.own_dex_data.format import _normalize_amount

logger = get_logger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────
_VOLUME_CACHE: dict[str, dict[str, Any]] = {}
_VOLUME_CACHE_TTL = 600  # 10 minutes — volume patterns change slowly
_VOLUME_CACHE_MAX = 100

# ── Lazy ClickHouse client ────────────────────────────────────────────────
_ch_client: Any = None

# ── Chain normalization ───────────────────────────────────────────────────
_CHAIN_MAP: dict[str, str] = {
    "ethereum": "ethereum",
    "eth": "ethereum",
    "mainnet": "ethereum",
    "bsc": "bsc",
    "bnb": "bsc",
    "binance": "bsc",
    "polygon": "polygon",
    "matic": "polygon",
    "arbitrum": "arbitrum",
    "arb": "arbitrum",
    "optimism": "optimism",
    "op": "optimism",
    "base": "base",
    "avalanche": "avalanche",
    "avax": "avalanche",
    "fantom": "fantom",
    "ftm": "fantom",
    "solana": "solana",
}


def _normalize_chain(chain: str) -> str:
    """Normalize chain name to the format used in ClickHouse tables."""
    return _CHAIN_MAP.get(chain.lower(), chain.lower())


def _get_ch_client() -> Any | None:
    """Lazy-init ClickHouse native protocol client for wallet_memory database."""
    global _ch_client
    if _ch_client is not None:
        return _ch_client

    ch_host = os.getenv("CLICKHOUSE_HOST") or os.getenv("CH_HOST", "rmi-clickhouse")
    ch_port = int(os.getenv("CLICKHOUSE_PORT") or os.getenv("CH_PORT") or "9000")
    ch_user = os.getenv("CLICKHOUSE_USER") or os.getenv("CH_USER", "rmi")
    ch_pass = (os.getenv("CLICKHOUSE_PASSWORD") or os.getenv("CH_PASSWORD")) or ""
    ch_db = "wallet_memory"

    try:
        from clickhouse_driver import Client as CHClient

        _ch_client = CHClient(
            host=ch_host,
            port=ch_port,
            user=ch_user,
            password=ch_pass,
            database=ch_db,
            connect_timeout=3,
            send_receive_timeout=15,
        )
        _ch_client.execute("SELECT 1")
        logger.info("Own volume authenticity ClickHouse connected: %s:%s/%s", ch_host, ch_port, ch_db)
        return _ch_client
    except (ImportError, ConnectionError, TimeoutError, RuntimeError, OSError) as e:
        logger.warning("Own volume authenticity ClickHouse unavailable: %s", e)
        _ch_client = None
        return None


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def get_own_volume_authenticity(
    token_address: str,
    chain: str,
    lookback_hours: int = 24,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Score volume authenticity (0-100) from own ClickHouse data.

    A higher score means the volume looks organic. The score starts at 100
    and is reduced by wash-trading signals: multi-swap txs, self-swaps,
    sender concentration, zero-net cycling, and volume-ratio deviation.

    Args:
        token_address: Token contract address
        chain: Chain name (ethereum, bsc, etc.)
        lookback_hours: Analysis window (default 24h)
        force_fresh: Bypass cache

    Returns:
        Dict with authentic_score, wash signals, volume comparison, and
        flags, or an error dict when insufficient data.
    """
    token_address = token_address.lower()
    chain = _normalize_chain(chain)
    _start = time.monotonic()

    cache_key = f"{token_address}:{chain}:{lookback_hours}"
    if not force_fresh and cache_key in _VOLUME_CACHE:
        cached = _VOLUME_CACHE[cache_key]
        if time.time() - cached.get("_cached_at", 0) < _VOLUME_CACHE_TTL:
            return {k: v for k, v in cached.items() if not k.startswith("_")}

    result: dict[str, Any] = {
        "check": "volume_authenticity",
        "result": "scanned",
        "source": "own",
        "authentic_score": 100,
        "fake_volume_pct": 0.0,
        "risk_level": "low",
        "wash_signals": [],
        "volume_comparison": {
            "dex_volume": 0.0,
            "transfer_volume": 0.0,
            "ratio": None,
        },
        "flags": [],
        "total_swaps": 0,
        "confidence": 0.0,
    }

    try:
        # ── 1. Parallel: DEX swap stats + transfer volume ──
        dex_stats_task = asyncio.create_task(run_sync(_query_dex_wash_stats, token_address, chain, lookback_hours))
        transfer_task = asyncio.create_task(run_sync(_query_transfer_volume, token_address, chain, lookback_hours))
        dex_stats, transfer_stats = await asyncio.gather(dex_stats_task, transfer_task)
    except Exception as exc:
        logger.debug("volume_auth_query_failed for %s: %s", token_address[:16], exc)
        result["result"] = "unavailable"
        return result

    if not dex_stats or dex_stats.get("total_swaps", 0) < 5:
        logger.debug("insufficient dex data for volume authenticity on %s", token_address[:16])
        result["result"] = "unavailable"
        return result

    total_swaps = dex_stats["total_swaps"]
    result["total_swaps"] = total_swaps

    # ── 2. DEX volume (normalized token amount) ──
    dex_volume = dex_stats.get("dex_volume", 0.0)
    transfer_volume = transfer_stats.get("transfer_volume", 0.0) if transfer_stats else 0.0
    result["volume_comparison"]["dex_volume"] = round(dex_volume, 2)
    result["volume_comparison"]["transfer_volume"] = round(transfer_volume, 2)
    if transfer_volume > 0:
        result["volume_comparison"]["ratio"] = round(dex_volume / transfer_volume, 3)

    # ── 3. Wash signals ──
    signals: list[dict[str, Any]] = []
    score = 100.0

    # 3a. Multi-swap transactions (circular routing)
    multi_swap_ratio = dex_stats.get("multi_swap_tx_ratio", 0.0)
    if multi_swap_ratio > 0.05:
        penalty = min(30.0, multi_swap_ratio * 60)
        score -= penalty
        signals.append(
            {
                "name": "multi_swap_transactions",
                "score": round(multi_swap_ratio, 3),
                "detail": f"{dex_stats.get('multi_swap_txs', 0)} txs contain >=3 swaps",
            }
        )

    # 3b. Self-swaps (sender == recipient)
    self_swap_ratio = dex_stats.get("self_swap_ratio", 0.0)
    if self_swap_ratio > 0.02:
        penalty = min(20.0, self_swap_ratio * 50)
        score -= penalty
        signals.append(
            {
                "name": "self_swaps",
                "score": round(self_swap_ratio, 3),
                "detail": f"{dex_stats.get('self_swaps', 0)} swaps where sender == recipient",
            }
        )

    # 3c. Sender concentration (top 3 wallets)
    top3_sender_share = dex_stats.get("top3_sender_share", 0.0)
    if top3_sender_share > 0.6:
        penalty = min(25.0, (top3_sender_share - 0.6) * 80)
        score -= penalty
        signals.append(
            {
                "name": "sender_concentration",
                "score": round(top3_sender_share, 3),
                "detail": f"top 3 senders drive {round(top3_sender_share * 100, 1)}% of swaps",
            }
        )

    # 3d. Zero-net cycling
    cycling_ratio = dex_stats.get("cycling_ratio", 0.0)
    if cycling_ratio > 0.1:
        penalty = min(25.0, cycling_ratio * 50)
        score -= penalty
        signals.append(
            {
                "name": "zero_net_cycling",
                "score": round(cycling_ratio, 3),
                "detail": f"{dex_stats.get('cycling_wallets', 0)} wallets buy+sell to net ~0",
            }
        )

    # 3e. Volume ratio deviation (dex vs transfer)
    ratio = result["volume_comparison"]["ratio"]
    if ratio is not None:
        if ratio > 3.0:
            score -= min(15.0, (ratio - 3.0) * 4)
            signals.append(
                {
                    "name": "dex_volume_exceeds_transfers",
                    "score": round(ratio, 3),
                    "detail": "DEX volume is much higher than on-chain transfer volume",
                }
            )
        elif ratio < 0.3:
            score -= 5
            signals.append(
                {
                    "name": "transfer_volume_exceeds_dex",
                    "score": round(ratio, 3),
                    "detail": "Transfers without corresponding DEX swaps",
                }
            )

    # ── 4. Finalize ──
    score = max(0, min(100, round(score)))
    result["authentic_score"] = score
    result["fake_volume_pct"] = round(100 - score, 1)
    result["wash_signals"] = signals
    if score >= 70:
        result["risk_level"] = "low"
    elif score >= 45:
        result["risk_level"] = "medium"
    else:
        result["risk_level"] = "high"
        result["flags"].append("possible_wash_trading")

    result["confidence"] = round(min(1.0, 0.4 + 0.1 * min(total_swaps / 20, 1.0)), 2)

    _cache_volume(cache_key, result)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "own_volume_authenticity_latency",
        ms=round(_elapsed_ms, 1),
        token=token_address[:16],
        score=score,
        swaps=total_swaps,
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# CLICKHOUSE QUERIES
# ═══════════════════════════════════════════════════════════════════════════


def _query_dex_wash_stats(
    token_address: str,
    chain: str,
    lookback_hours: int,
) -> dict[str, Any] | None:
    """Compute wash-trading stats from dex_events.

    Returns:
        {
            "total_swaps": int,
            "dex_volume": float,        # normalized token amount traded
            "multi_swap_txs": int,
            "multi_swap_tx_ratio": float,
            "self_swaps": int,
            "self_swap_ratio": float,
            "top3_sender_share": float,
            "cycling_wallets": int,
            "cycling_ratio": float,
        }
    """
    client = _get_ch_client()
    if client is None:
        return None

    try:
        pair_cache = _load_pair_cache(chain)
        known_pools = _get_pools_for_token(pair_cache, token_address)

        params: dict[str, Any] = {
            "addr0": token_address,
            "addr1": token_address,
            "chain": chain,
            "hours": lookback_hours,
        }
        pool_clause = ""
        if known_pools:
            params["known_pools"] = known_pools
            pool_clause = " OR contract_address IN %(known_pools)s"

        where_swaps = f"""
            chain = %(chain)s
            AND type = 'swap'
            AND block_timestamp >= now() - INTERVAL %(hours)s HOUR
            AND (token0 = %(addr0)s OR token1 = %(addr1)s{pool_clause})
        """

        # Overall + multi-swap tx stats
        stats_rows = client.execute(
            f"""
            SELECT
                count() AS total_swaps,
                uniq(tx_hash) AS unique_txs,
                countIf(sender = recipient AND sender != '' AND recipient != '') AS self_swaps,
                sum(
                    toFloat64OrZero(amount0_in) + toFloat64OrZero(amount0_out)
                ) AS vol_token0_raw,
                sum(
                    toFloat64OrZero(amount1_in) + toFloat64OrZero(amount1_out)
                ) AS vol_token1_raw,
                count(DISTINCT sender) AS unique_senders
            FROM wallet_memory.dex_events
            WHERE {where_swaps}
            """,
            params,
        )

        tx_rows = client.execute(
            f"""
            SELECT tx_hash, count() AS n
            FROM wallet_memory.dex_events
            WHERE {where_swaps}
            GROUP BY tx_hash
            HAVING n >= 3
            """,
            params,
        )

        sender_rows = client.execute(
            f"""
            SELECT sender, count() AS n
            FROM wallet_memory.dex_events
            WHERE {where_swaps}
            GROUP BY sender
            ORDER BY n DESC
            LIMIT 3
            """,
            params,
        )

        cycling_rows = client.execute(
            f"""
            SELECT
                sender,
                countIf(toFloat64OrZero(amount0_out) + toFloat64OrZero(amount1_out) > 0) AS buys,
                countIf(toFloat64OrZero(amount0_in) + toFloat64OrZero(amount1_in) > 0) AS sells
            FROM wallet_memory.dex_events
            WHERE {where_swaps}
            GROUP BY sender
            HAVING buys > 3 AND sells > 3
            LIMIT 50
            """,
            params,
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("dex_wash_stats_query_failed: %s", exc)
        return None

    if not stats_rows or not stats_rows[0]:
        return None

    row = stats_rows[0]
    total_swaps = row[0] or 0
    unique_txs = row[1] or 0
    self_swaps = row[2] or 0
    vol_token0_raw = float(row[3] or 0)
    vol_token1_raw = float(row[4] or 0)
    unique_senders = row[5] or 0

    # DEX volume: the token-side raw volume, normalized
    is_token0_side = vol_token0_raw >= vol_token1_raw
    dex_volume = _normalize_amount(
        vol_token0_raw if is_token0_side else vol_token1_raw,
        token_address,
        chain,
    )

    multi_swap_txs = len(tx_rows)
    top3_swaps = sum(r[1] or 0 for r in sender_rows[:3])
    cycling_wallets = len(cycling_rows)

    return {
        "total_swaps": total_swaps,
        "unique_txs": unique_txs,
        "unique_senders": unique_senders,
        "dex_volume": dex_volume,
        "multi_swap_txs": multi_swap_txs,
        "multi_swap_tx_ratio": round(multi_swap_txs / unique_txs, 4) if unique_txs else 0.0,
        "self_swaps": self_swaps,
        "self_swap_ratio": round(self_swaps / total_swaps, 4) if total_swaps else 0.0,
        "top3_sender_share": round(top3_swaps / total_swaps, 4) if total_swaps else 0.0,
        "cycling_wallets": cycling_wallets,
        "cycling_ratio": round(cycling_wallets / unique_senders, 4) if unique_senders else 0.0,
    }


def _query_transfer_volume(
    token_address: str,
    chain: str,
    lookback_hours: int,
) -> dict[str, Any] | None:
    """Sum on-chain transfer volume for the token from evm_transfers."""
    client = _get_ch_client()
    if client is None:
        return None

    try:
        rows = client.execute(
            """
            SELECT
                count() AS transfer_count,
                sum(toFloat64OrZero(value)) AS raw_value_sum
            FROM wallet_memory.evm_transfers
            WHERE token_address = %(token)s
              AND chain = %(chain)s
              AND block_timestamp >= now() - INTERVAL %(hours)s HOUR
              AND to_address != '0x0000000000000000000000000000000000000000'
              AND to_address != ''
              AND from_address != '0x0000000000000000000000000000000000000000'
              AND from_address != ''
            """,
            {"token": token_address, "chain": chain, "hours": lookback_hours},
        )
        if not rows or not rows[0]:
            return None

        return {
            "transfer_count": rows[0][0] or 0,
            "transfer_volume": _normalize_amount(rows[0][1] or 0, token_address, chain),
        }
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("transfer_volume_query_failed: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# CACHING
# ═══════════════════════════════════════════════════════════════════════════


def _cache_volume(key: str, data: dict[str, Any]) -> None:
    """Cache volume authenticity data with timestamp."""
    data["_cached_at"] = time.time()
    _VOLUME_CACHE[key] = data
    if len(_VOLUME_CACHE) > _VOLUME_CACHE_MAX:
        _VOLUME_CACHE.pop(next(iter(_VOLUME_CACHE)), None)


def get_cache_stats() -> dict[str, Any]:
    """Return cache statistics for monitoring."""
    return _common_get_cache_stats(
        _VOLUME_CACHE,
        _VOLUME_CACHE_MAX,
        _VOLUME_CACHE_TTL,
        extra={"clickhouse_connected": _ch_client is not None},
    )


def clear_cache() -> None:
    """Clear the in-process cache (for testing)."""
    _common_clear_cache(_VOLUME_CACHE)
