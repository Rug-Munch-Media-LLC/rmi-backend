"""V1 self-served market endpoints — holders, trending, new-pairs, whale feed.

Self-serve three data streams from our own lite-chain-indexer ClickHouse
(``wallet_memory.*`` tables) instead of external APIs:

  - GET /token/{chain}/{address}/holders — holder distribution from
    ``evm_transfers`` (EVM) / ``solana_spl_balance_changes`` (Solana).
    Falls back to the DexScreener holders provider only when ClickHouse
    has no data for that chain/token (fail-open).
  - GET /market/trending — recent launches from ``solana_launches`` /
    ``tron_launches`` / ``evm_launches`` ranked by 24h on-chain activity
    (transfer counts). Replaces the GMGN trending dependency. Fails open
    to the Solana Tracker trending provider.
  - GET /market/new-pairs — recent pair/pool creations from
    ``deployer_events`` (type='pool') + ``evm_launches``. Fails open to
    DexScreener token-pairs.
  - GET /whales/feed — large stablecoin-side swaps from ``dex_events``
    (same USD derivation as lite-chain-indexer /api/v1/whales), formatted
    as news items with ``image_url`` support (media route).

All ClickHouse access is fail-open: any query error or empty result
degrades to the external fallback (or an empty list) — never a 500.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.core.logging import get_logger

logger = get_logger("market-selfserved")

router = APIRouter(tags=["market-v1"])

_HTTP_TIMEOUT = 10.0
_TRENDING_WINDOW_HOURS = 24


# ═══════════════════════════════════════════════════════════════════════
# ClickHouse helpers (fail-open)
# ═══════════════════════════════════════════════════════════════════════


def _ch():
    """Per-thread ClickHouse client or None (fail-open)."""
    from app.domains.databus.providers.chains.lite_chain_indexer import _get_ch_client

    return _get_ch_client()


def _normalize_chain(chain: str) -> str:
    from app.domains.databus.providers.chains.lite_chain_indexer import _normalize_chain as norm

    return norm(chain)


_SOLANA_CHAINS = {"solana"}


def _is_solana(chain: str) -> bool:
    return chain.lower() in _SOLANA_CHAINS


def _iso(ts: Any) -> str:
    return ts.isoformat() if hasattr(ts, "isoformat") else str(ts)


# ═══════════════════════════════════════════════════════════════════════
# 1. Holders — own ClickHouse, DexScreener fallback
# ═══════════════════════════════════════════════════════════════════════


def _holders_evm(chain: str, address: str, limit: int) -> dict[str, Any] | None:
    """Holder distribution — pre-aggregated ``holder_balances`` first (fast,
    indexed by (chain, token_address, address)), falling back to a net-balance
    aggregation over ``evm_transfers`` (slow full scan, only when the
    holder_balances materialization has no rows for the token)."""
    client = _ch()
    if client is None:
        return None
    # ── Fast path: holder_balances (ReplacingMergeTree, pre-aggregated) ──
    try:
        rows = client.execute(
            """
            SELECT address, max(balance), max(transfer_count)
            FROM wallet_memory.holder_balances
            WHERE chain = %(chain)s AND token_address = %(addr)s AND balance > 0
            GROUP BY address
            ORDER BY max(balance) DESC
            LIMIT %(limit)s
            """,
            {"chain": chain, "addr": address, "limit": limit},
        )
        count_rows = client.execute(
            """
            SELECT uniq(address) FROM wallet_memory.holder_balances
            WHERE chain = %(chain)s AND token_address = %(addr)s AND balance > 0
            """,
            {"chain": chain, "addr": address},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("holders holder_balances query failed: %s", exc)
        rows, count_rows = None, None
    if rows:
        holders = [{"address": r[0], "balance": float(r[1] or 0), "transfers": int(r[2] or 0)} for r in rows]
        return {
            "chain": chain,
            "token_address": address,
            "total_holders": int(count_rows[0][0]) if count_rows else len(holders),
            "holders": holders,
            "source": "own_clickhouse:holder_balances",
        }

    # ── Slow path: aggregate evm_transfers (full scan per token) ──
    try:
        rows = client.execute(
            """
            SELECT addr, sum(delta) AS balance, count() AS transfers
            FROM (
                SELECT to_address AS addr, toFloat64OrZero(value) AS delta
                FROM wallet_memory.evm_transfers
                WHERE chain = %(chain)s AND token_address = %(addr)s
                  AND to_address != '0x0000000000000000000000000000000000000000'
                UNION ALL
                SELECT from_address AS addr, -toFloat64OrZero(value) AS delta
                FROM wallet_memory.evm_transfers
                WHERE chain = %(chain)s AND token_address = %(addr)s
                  AND from_address != '0x0000000000000000000000000000000000000000'
            )
            GROUP BY addr
            ORDER BY balance DESC
            LIMIT %(limit)s
            """,
            {"chain": chain, "addr": address, "limit": limit},
        )
        count_rows = client.execute(
            """
            SELECT uniq(to_address) FROM wallet_memory.evm_transfers
            WHERE chain = %(chain)s AND token_address = %(addr)s
              AND to_address != '0x0000000000000000000000000000000000000000'
            """,
            {"chain": chain, "addr": address},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("holders evm query failed: %s", exc)
        return None
    if not rows:
        return None
    holders = [{"address": r[0], "balance": float(r[1] or 0), "transfers": int(r[2])} for r in rows]
    return {
        "chain": chain,
        "token_address": address,
        "total_holders": int(count_rows[0][0]) if count_rows else len(holders),
        "holders": holders,
        "source": "own_clickhouse:evm_transfers",
    }


def _holders_solana(address: str, limit: int) -> dict[str, Any] | None:
    """Holder distribution from solana_spl_balance_changes latest balances."""
    client = _ch()
    if client is None:
        return None
    try:
        rows = client.execute(
            """
            SELECT owner, argMax(post_amount, (block_timestamp, slot)) AS balance,
                   count() AS changes
            FROM wallet_memory.solana_spl_balance_changes
            WHERE mint = %(mint)s AND owner != ''
            GROUP BY owner
            ORDER BY balance DESC
            LIMIT %(limit)s
            """,
            {"mint": address, "limit": limit},
        )
        count_rows = client.execute(
            """
            SELECT uniq(owner) FROM wallet_memory.solana_spl_balance_changes
            WHERE mint = %(mint)s AND owner != ''
            """,
            {"mint": address},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("holders solana query failed: %s", exc)
        return None
    if not rows:
        return None
    holders = [{"address": r[0], "balance": float(r[1] or 0), "changes": int(r[2])} for r in rows]
    return {
        "chain": "solana",
        "token_address": address,
        "total_holders": int(count_rows[0][0]) if count_rows else len(holders),
        "holders": holders,
        "source": "own_clickhouse:solana_spl_balance_changes",
    }


async def _holders_fallback(chain: str, address: str, limit: int) -> dict[str, Any] | None:
    """DexScreener fallback (free, no key) — only when ClickHouse is empty."""
    from app.domains.databus.providers.chains.dexscreener import _dexscreener_holders

    try:
        data = await _dexscreener_holders(mint=address, chain=chain)
    except Exception as exc:
        logger.debug("dexscreener holders fallback failed: %s", exc)
        return None
    if not data:
        return None
    return {
        "chain": chain,
        "token_address": address,
        "holders": data if isinstance(data, list) else data.get("holders", []),
        "total_holders": len(data) if isinstance(data, list) else len(data.get("holders", [])),
        "source": "dexscreener",
    }


@router.get("/token/{chain}/{address}/holders", summary="Token holders (own ClickHouse, DexScreener fallback)")
async def get_token_holders(
    chain: str,
    address: str,
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    """Holder distribution for a token, self-served from our chain indexer."""
    ch_chain = _normalize_chain(chain)
    addr = address.strip().lower()
    if not addr:
        raise HTTPException(status_code=400, detail="address required")

    if _is_solana(ch_chain):
        own = await asyncio.to_thread(_holders_solana_safe, addr, limit)
    else:
        own = await asyncio.to_thread(_holders_evm_safe, ch_chain, addr, limit)
    if own:
        return own
    fallback = await _holders_fallback(ch_chain, addr, limit)
    if fallback:
        return fallback
    # No data anywhere — explicit empty (fail-open, not an error).
    return {
        "chain": ch_chain,
        "token_address": addr,
        "total_holders": 0,
        "holders": [],
        "source": "none",
    }


def _holders_evm_safe(chain: str, address: str, limit: int) -> dict[str, Any] | None:
    try:
        return _holders_evm(chain, address, limit)
    except Exception as exc:
        logger.debug("holders evm failed: %s", exc)
        return None


def _holders_solana_safe(address: str, limit: int) -> dict[str, Any] | None:
    try:
        return _holders_solana(address, limit)
    except Exception as exc:
        logger.debug("holders solana failed: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════
# 2. Trending — launches + 24h activity (GMGN replacement)
# ═══════════════════════════════════════════════════════════════════════

# Normalized launch UNION — mirrors lite-chain-indexer api/routers/launches.py
_LAUNCH_UNION = """
SELECT chain, block_timestamp, address, creator, dex, tx_hash, event_type,
       launch_signal, graduation_signal, confidence
FROM (
    SELECT chain, block_timestamp, mint AS address, creator, venue AS dex,
           signature AS tx_hash, 'launchpad' AS event_type,
           launch_signal, graduation_signal, confidence
    FROM wallet_memory.solana_launches
    ORDER BY block_timestamp DESC LIMIT 500
    UNION ALL
    SELECT chain, block_timestamp, token0 AS address, '' AS creator,
           factory AS dex, tx_hash, 'pair_created' AS event_type, 0, 0, 'high'
    FROM wallet_memory.evm_launches
    ORDER BY block_timestamp DESC LIMIT 500
)
"""


def _trending_own(chain: str, limit: int, hours: int) -> list[dict[str, Any]] | None:
    """Recent launches ranked by 24h transfer activity from our ClickHouse."""
    client = _ch()
    if client is None:
        return None
    params: dict[str, Any] = {"limit": limit, "hours": hours}
    chain_sql = ""
    if chain:
        params["chain"] = _normalize_chain(chain)
        chain_sql = "WHERE chain = %(chain)s"
    try:
        rows = client.execute(
            f"""
            SELECT chain, block_timestamp, address, creator, dex, tx_hash,
                   event_type, launch_signal, graduation_signal, confidence,
                   if(tm.symbol = '', '', tm.symbol) AS symbol
            FROM ({_LAUNCH_UNION}) l
            LEFT JOIN wallet_memory.token_meta_ref tm
                ON tm.chain = l.chain AND tm.token_address = l.address
            {chain_sql}
            ORDER BY block_timestamp DESC
            LIMIT %(limit)s
            """,  # noqa: S608 — code-literal SQL (stablecoin map literals), values bound via params  # nosec B608
            params,
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("trending launches query failed: %s", exc)
        return None
    if not rows:
        return None

    tokens = [r[2] for r in rows if r[2]]
    activity = _activity_counts(tokens, hours)

    items = []
    now = time.time()
    for r in rows:
        ts = r[1]
        age_h = max(0.0, (now - (_iso_ts_epoch(ts))) / 3600.0)
        # Rank: recent + high launch signal + real 24h transfer activity.
        score = float(r[7] or 0) * 2.0 + activity.get(r[2], 0) - age_h * 0.5
        items.append(
            {
                "chain": r[0],
                "token_address": r[2],
                "symbol": r[10] or "",
                "creator": r[3],
                "dex": r[4],
                "tx_hash": r[5],
                "event_type": r[6],
                "launch_signal": float(r[7] or 0),
                "graduation_signal": float(r[8] or 0),
                "confidence": r[9],
                "block_timestamp": _iso(r[1]),
                "activity_24h": activity.get(r[2], 0),
                "score": round(score, 3),
                "source": "own_clickhouse:launches",
            }
        )
    items.sort(key=lambda x: x["score"], reverse=True)
    return items


def _iso_ts_epoch(ts: Any) -> float:
    try:
        return ts.timestamp() if hasattr(ts, "timestamp") else 0.0
    except (ValueError, OSError):
        return 0.0


def _activity_counts(tokens: list[str], hours: int) -> dict[str, int]:
    """24h transfer counts per token across EVM + Solana tables (best-effort)."""
    if not tokens:
        return {}
    client = _ch()
    if client is None:
        return {}
    counts: dict[str, int] = {}
    try:
        rows = client.execute(
            """
            SELECT token_address, count() FROM wallet_memory.evm_transfers
            WHERE token_address IN %(tokens)s
              AND block_timestamp >= now() - INTERVAL %(hours)s HOUR
            GROUP BY token_address
            """,
            {"tokens": tuple(tokens), "hours": hours},
        )
        counts.update({r[0]: int(r[1]) for r in rows})
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("evm activity query failed: %s", exc)
    try:
        rows = client.execute(
            """
            SELECT mint, count() FROM wallet_memory.solana_spl_balance_changes
            WHERE mint IN %(tokens)s
              AND block_timestamp >= now() - INTERVAL %(hours)s HOUR
            GROUP BY mint
            """,
            {"tokens": tuple(tokens), "hours": hours},
        )
        for r in rows:
            counts[r[0]] = counts.get(r[0], 0) + int(r[1])
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("solana activity query failed: %s", exc)
    return counts


async def _trending_fallback(chain: str, limit: int) -> list[dict[str, Any]] | None:
    """DexScreener recent-pairs fallback (free, no key) — trending proxy."""
    import httpx

    ch_chain = _normalize_chain(chain) if chain else "solana"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
            r = await c.get(f"https://api.dexscreener.com/token-pairs/v1/{ch_chain}/recent")
            if r.status_code == 200:
                data = r.json()
                return data if isinstance(data, list) else data.get("pairs", [])[:limit]
    except (httpx.HTTPError, ConnectionError, TimeoutError, ValueError) as exc:
        logger.debug("dexscreener trending fallback failed: %s", exc)
    return None


@router.get("/market/trending", summary="Trending tokens (own launches + activity, external fallback)")
async def get_trending(
    chain: str = Query(default="", description="Optional chain filter"),
    limit: int = Query(default=50, ge=1, le=200),
    hours: int = Query(default=_TRENDING_WINDOW_HOURS, ge=1, le=168),
) -> dict[str, Any]:
    """Trending tokens derived from our own indexed launches + transfer activity.

    NOTE: in the FULL app this route is shadowed by the canonical
    ``app/routers/market_intel/routes/market_data.py`` implementation
    (mounted earlier — items/total/updated_at contract, see
    tests/contract/test_api_contracts.py). This own-ClickHouse variant
    serves when this router is mounted standalone (unit tests, embeds).
    """
    own = await asyncio.to_thread(_trending_own_safe, chain, limit, hours)
    if own:
        return {"data": own, "source": "own_clickhouse", "count": len(own)}
    fallback = await _trending_fallback(chain, limit)
    if fallback:
        return {"data": fallback, "source": "dexscreener", "count": len(fallback)}
    return {"data": [], "source": "none", "count": 0}


def _trending_own_safe(chain: str, limit: int, hours: int) -> list[dict[str, Any]] | None:
    try:
        return _trending_own(chain, limit, hours)
    except Exception as exc:
        logger.debug("trending own failed: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════
# 3. New pairs — deployer_events pools + evm_launches pair_created
# ═══════════════════════════════════════════════════════════════════════


def _new_pairs_own(chain: str, limit: int) -> list[dict[str, Any]] | None:
    """Recent pair/pool creations from our ClickHouse."""
    client = _ch()
    if client is None:
        return None
    params: dict[str, Any] = {"limit": limit}
    chain_sql = ""
    if chain:
        params["chain"] = _normalize_chain(chain)
        chain_sql = "AND chain = %(chain)s"
    try:
        rows = client.execute(
            f"""
            SELECT chain, block_timestamp, token_address, deployer, factory, tx_hash, type
            FROM (
                SELECT chain, block_timestamp, token_address, deployer, factory, tx_hash, type
                FROM wallet_memory.deployer_events
                WHERE type IN ('v2_pool', 'v3_pool', 'solana_token') {chain_sql}
                ORDER BY block_timestamp DESC LIMIT %(limit)s
            )
            ORDER BY block_timestamp DESC
            LIMIT %(limit)s
            """,  # noqa: S608 — code-literal SQL (stablecoin map literals), values bound via params  # nosec B608
            params,
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("new pairs query failed: %s", exc)
        return None
    if not rows:
        return None
    return [
        {
            "chain": r[0],
            "block_timestamp": _iso(r[1]),
            "token_address": r[2],
            "deployer": r[3],
            "factory": r[4],
            "tx_hash": r[5],
            "type": r[6],
            "source": "own_clickhouse:deployer_events",
        }
        for r in rows
    ]


async def _new_pairs_fallback(chain: str, limit: int) -> list[dict[str, Any]] | None:
    """DexScreener token-pairs fallback (free, no key)."""
    import httpx

    ch_chain = _normalize_chain(chain) if chain else "solana"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
            r = await c.get(f"https://api.dexscreener.com/token-pairs/v1/{ch_chain}/recent")
            if r.status_code == 200:
                data = r.json()
                return data if isinstance(data, list) else data.get("pairs", [])[:limit]
    except (httpx.HTTPError, ConnectionError, TimeoutError, ValueError) as exc:
        logger.debug("dexscreener new-pairs fallback failed: %s", exc)
    return None


@router.get("/market/new-pairs", summary="New pairs (own ClickHouse, DexScreener fallback)")
async def get_new_pairs(
    chain: str = Query(default="", description="Optional chain filter"),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """Recent pair/pool creations from our own indexed data."""
    own = await asyncio.to_thread(_new_pairs_own_safe, chain, limit)
    if own:
        return {"data": own, "source": "own_clickhouse", "count": len(own)}
    fallback = await _new_pairs_fallback(chain, limit)
    if fallback:
        return {"data": fallback, "source": "dexscreener", "count": len(fallback)}
    return {"data": [], "source": "none", "count": 0}


def _new_pairs_own_safe(chain: str, limit: int) -> list[dict[str, Any]] | None:
    try:
        return _new_pairs_own(chain, limit)
    except Exception as exc:
        logger.debug("new pairs own failed: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════
# 4. Whale feed — dex_events stablecoin-side swaps as news items
# ═══════════════════════════════════════════════════════════════════════

# Stablecoin addresses per chain (lowercase) — same map as lite-chain-indexer
# api/stablecoins.py. USD value of a swap = stablecoin-side amount.
_STABLECOINS: dict[str, frozenset[str]] = {
    "ethereum": frozenset(
        {
            "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
            "0xdac17f958d2ee523a2206206994597c13d831ec7",
            "0x6b175474e89094c44da98b954eedeac495271d0f",
        }
    ),
    "base": frozenset({"0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"}),
    "bsc": frozenset(
        {
            "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",
            "0x55d398326f99059ff775485246999027b3197955",
        }
    ),
    "polygon": frozenset(
        {
            "0x2791bca1f2de4661ed88a30c99a7a9449aa84174",
            "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359",
            "0xc2132d05d31c914a87c6611c10748aeb04b58e8f",
        }
    ),
    "arbitrum": frozenset(
        {
            "0xaf88d065e77c8cc2239327c5edb3a432268e5831",
            "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8",
            "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9",
        }
    ),
    "optimism": frozenset(
        {
            "0x0b2c639c533813f4aa9d7837caf62653d097ff85",
            "0x94b008aa00579c1307b0ef2c499ad98a8ce58e58",
            "0x7f5c764cbc14f9669b88837ca1490cca17c31607",
        }
    ),
    "avalanche": frozenset(
        {
            "0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e",
            "0x9702230a8ea53601f5cd2dc00fdbc13d4df4a8c7",
        }
    ),
}


# Flat set of every stablecoin address across chains (lowercase) — used by
# _stablecoin_value for O(1) side detection.
_STABLECOIN_ADDR_SET: frozenset[str] = frozenset(addr for addrs in _STABLECOINS.values() for addr in addrs)


def _whale_feed_own(min_usd: float, limit: int, chain: str) -> list[dict[str, Any]] | None:
    """Whale swaps from dex_events, USD derived from the stablecoin side."""
    client = _ch()
    if client is None:
        return None
    chains = [chain.strip().lower()] if chain else list(_STABLECOINS)
    terms: list[str] = []
    for ch in chains:
        for addr in _STABLECOINS.get(ch, frozenset()):
            terms.append(f"(token0 = '{addr}' OR token1 = '{addr}')")
    if not terms:
        return []
    stablecoin_filter = "(" + " OR ".join(terms) + ")"

    chain_sql = ""
    params: dict[str, Any] = {"min_usd": min_usd, "limit": limit}
    if chain:
        params["chain"] = chain.strip().lower()
        chain_sql = "AND chain = %(chain)s"

    try:
        rows = client.execute(
            f"""
            SELECT chain, block_timestamp, tx_hash, dex, version, contract_address,
                   token0, token1, sender,
                   toFloat64OrZero(ifNull(amount0, '')) AS amount0_in,
                   toFloat64OrZero(ifNull(amount1, '')) AS amount1_in,
                   toFloat64OrZero(ifNull(amount0_out, '')) AS amount0_out,
                   toFloat64OrZero(ifNull(amount1_out, '')) AS amount1_out,
                   if(tm0.symbol = '', '', tm0.symbol) AS symbol0,
                   if(tm1.symbol = '', '', tm1.symbol) AS symbol1,
                   tm0.decimals AS decimals0,
                   tm1.decimals AS decimals1,
                   volume_usd
            FROM wallet_memory.dex_events
            LEFT JOIN wallet_memory.token_meta_ref tm0
                ON tm0.chain = dex_events.chain AND tm0.token_address = dex_events.token0
            LEFT JOIN wallet_memory.token_meta_ref tm1
                ON tm1.chain = dex_events.chain AND tm1.token_address = dex_events.token1
            WHERE {stablecoin_filter}
              AND volume_usd >= %(min_usd)s
              {chain_sql}
            ORDER BY block_timestamp DESC
            LIMIT %(limit)s
            """,  # noqa: S608 — code-literal SQL (stablecoin map literals), values bound via params  # nosec B608
            params,
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("whale feed query failed: %s", exc)
        return None

    items = []
    for r in rows:
        chain_name, ts, tx_hash, dex, version, pair, token0, token1, sender = r[:9]
        a0_in, a1_in, a0_out, a1_out = r[9], r[10], r[11], r[12]
        sym0, sym1 = r[13], r[14]
        dec0, dec1 = r[15], r[16]
        row_usd = float(r[17] or 0)
        usd, usd_asset = _stablecoin_value(token0, token1, a0_in, a1_in, a0_out, a1_out, dec0, dec1)
        # Prefer the indexer's precomputed notional when the stablecoin-side
        # derivation came up empty (e.g. non-standard pair layout).
        if usd <= 0 and row_usd > 0:
            usd = row_usd
        items.append(
            {
                "id": f"whale-{r[2]}",
                "title": (
                    f"Whale swap: ${usd:,.0f} {sym0 or _short(token0)}/{sym1 or _short(token1)} "
                    f"on {dex or chain_name} ({chain_name})"
                ),
                "summary": (
                    f"A ${usd:,.0f} swap involving {sym0 or _short(token0)} and "
                    f"{sym1 or _short(token1)} executed on {dex or 'DEX'} ({chain_name})."
                ),
                "category": "whale",
                "chain": chain_name,
                "block_timestamp": _iso(ts),
                "tx_hash": tx_hash,
                "dex": dex,
                "version": version,
                "pair_address": pair,
                "token0": token0,
                "token1": token1,
                "symbol0": sym0,
                "symbol1": sym1,
                "sender": sender,
                "usd_value": usd,
                "usd_asset": usd_asset,
                "image_url": "",  # media route serves /api/v1/media/image/{name}; no asset for raw swaps
                "source": "own_clickhouse:dex_events",
            }
        )
    # Enrichment pass (labels / context / smart money) — fail-open, batched.
    try:
        from app.routers.v1.whale_enrichment import enrich_feed_items

        items = enrich_feed_items(client, items)
    except Exception as exc:
        logger.debug("whale feed enrichment failed: %s", exc)
    return items


def _stablecoin_value(
    token0: str,
    token1: str,
    a0_in: float,
    a1_in: float,
    a0_out: float,
    a1_out: float,
    dec0: Any = None,
    dec1: Any = None,
) -> tuple[float, str]:
    """USD value = stablecoin-side amount (raw units → human via decimals).

    ``dex_events`` stores RAW on-chain amounts (the lite-indexer write path
    keeps raw log units; its pricing module divides by ``10 ** decimals``).
    Both swap directions are covered: the stable leg may be an IN leg
    (stable entered the pair — a buy) or an OUT leg (a sell); the larger of
    the two is the trade's stablecoin notional.
    """
    from app.routers.v1.whale_enrichment import stable_usd

    token0_l = (token0 or "").lower()
    token1_l = (token1 or "").lower()
    if token0_l in _STABLECOIN_ADDR_SET:
        return stable_usd(max(a0_in, a0_out), token0_l, dec0), token0_l
    if token1_l in _STABLECOIN_ADDR_SET:
        return stable_usd(max(a1_in, a1_out), token1_l, dec1), token1_l
    return 0.0, ""


def _short(addr: str) -> str:
    return f"{addr[:6]}…{addr[-4:]}" if len(addr) > 12 else addr


@router.get("/whales/feed", summary="Whale feed (own dex_events, news-item format)")
async def get_whale_feed(
    min_usd: float = Query(default=100_000.0, ge=1.0),
    limit: int = Query(default=50, ge=1, le=500),
    chain: str = Query(default="", description="Optional chain filter"),
    alpha_only: bool = Query(default=False, description="Only items flagged with an alpha signal"),
) -> dict[str, Any]:
    """Whale swaps across all lite-indexer chains, formatted as news items.

    Each item carries an ``alpha_signal`` object (large_swap /
    new_token_entry / exit_after_pump) with why-notable context and the
    wallet's historical performance — the premium alpha content. Pass
    ``alpha_only=true`` to get just the flagged moves.
    """
    items = await asyncio.to_thread(_whale_feed_own_safe, min_usd, limit, chain)
    if items is None:
        items = []
    if alpha_only:
        items = [i for i in items if i.get("alpha_signal")]
    alpha_count = sum(1 for i in items if i.get("alpha_signal"))
    return {
        "data": items,
        "source": "own_clickhouse",
        "count": len(items),
        "alpha_signal_count": alpha_count,
    }


def _whale_feed_own_safe(min_usd: float, limit: int, chain: str) -> list[dict[str, Any]] | None:
    try:
        return _whale_feed_own(min_usd, limit, chain)
    except Exception as exc:
        logger.debug("whale feed failed: %s", exc)
        return None
