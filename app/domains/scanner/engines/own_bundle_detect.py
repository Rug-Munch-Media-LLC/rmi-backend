"""
Own Bundle Buy Detection — ClickHouse-Powered Bundle Analysis
================================================================
Phase 3 of the Data Ownership Strategy.

Detects coordinated bundle buys from our own ClickHouse data:
  - Same-block multi-wallet buys (multiple wallets buying in same block)
  - Common funder detection (same source wallet funded multiple buyers)
  - Temporal clustering (purchases within tight time windows)
  - Distribution anomaly (skewed holder distribution suggesting bundling)

Architecture:
  - Queries evm_transfers for token purchase patterns
  - Uses block-level grouping to identify bundled launches
  - Falls back gracefully when data is insufficient

Usage:
    from app.domains.scanner.engines.own_bundle_detect import detect_bundled_launch, get_own_bundle_analysis

    result = await get_own_bundle_analysis("0x...", "ethereum")
    # result = {
    #     "is_bundled": bool,
    #     "confidence": float,
    #     "atomic_block_score": float,
    #     "common_funder_score": float,
    #     "distribution_anomaly_score": float,
    #     "top3_holder_pct": float,
    #     "top10_holder_pct": float,
    #     "bundle_blocks": [...],
    #     "source": "own",
    # }

    # Launch-window bundle detection (used by the unified scanner pipeline):
    launch = await detect_bundled_launch("0x...", "ethereum")
    # launch = {
    #     "is_bundled": bool,
    #     "confidence": float,
    #     "launch_cluster_score": float,
    #     "first_time_buyer_pct": float,
    #     "similar_amount_score": float,
    #     "deployer_funded_pct": float,
    #     "common_funder_score": float,
    #     "deploy_block": int | None,
    #     "cluster_blocks": [...],
    #     "source": "own",
    # }

Data Sources:
  Primary: wallet_memory.evm_transfers (transfer events)
  Primary: wallet_memory.deployer_events (deployment info)
"""

from __future__ import annotations

import os
import time
from typing import Any

try:
    from clickhouse_driver.errors import Error as CHDriverError
except ImportError:  # pragma: no cover
    CHDriverError = Exception

from app.core.logging import get_logger
from app.domains.scanner.engines._common import (
    clear_cache as _common_clear_cache,
    get_cache_stats as _common_get_cache_stats,
    run_sync,
)

logger = get_logger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────
_BUNDLE_CACHE: dict[str, dict[str, Any]] = {}
_BUNDLE_CACHE_TTL = 600  # 10 minutes
_BUNDLE_CACHE_MAX = 100

# ── Launch-window bundle detection constants ─────────────────────────────
_QUERY_TIMEOUT_SECONDS = 10  # time-box for ClickHouse queries
_LAUNCH_WINDOW_BLOCKS = 5  # first N blocks after deployment
_SIMILAR_AMOUNT_CV_BANDS = ((0.1, 100.0), (0.3, 80.0), (0.5, 50.0), (0.8, 20.0))

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
    return _CHAIN_MAP.get(chain.lower(), chain.lower())


def _get_ch_client() -> Any | None:
    """Lazy-init ClickHouse native protocol client."""
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
        logger.info("Own bundle detect ClickHouse connected: %s:%s/%s", ch_host, ch_port, ch_db)
        return _ch_client
    except (ImportError, ConnectionError, TimeoutError, RuntimeError, OSError) as e:
        logger.warning("Own bundle detect ClickHouse unavailable: %s", e)
        _ch_client = None
        return None


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def get_own_bundle_analysis(
    token_address: str,
    chain: str,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Get bundle buy analysis from own ClickHouse data.

    Analyzes transfer events to detect coordinated buying patterns:
    - Atomic block buys: multiple wallets buying in the same block
    - Common funder: multiple wallets funded by the same source
    - Distribution anomaly: extreme top-holder concentration

    Args:
        token_address: Token contract address
        chain: Chain name
        force_fresh: Bypass cache

    Returns:
        {
            "is_bundled": bool,
            "confidence": float,  # 0-100
            "risk_label": str,
            "atomic_block_score": float,  # 0-100
            "common_funder_score": float,  # 0-100
            "temporal_score": float,  # 0-100
            "distribution_anomaly_score": float,  # 0-100
            "concentration_score": float,  # 0-100
            "top3_holder_pct": float,
            "top10_holder_pct": float,
            "holder_count": int,
            "bundle_blocks": [{"block": int, "buyers": int, "buys": int}],
            "common_funders": [{"funder": str, "funded_count": int}],
            "source": str,
        }
    """
    token_address = token_address.lower()
    chain = _normalize_chain(chain)
    _start = time.monotonic()

    # Check cache
    cache_key = f"{token_address}:{chain}"
    if not force_fresh and cache_key in _BUNDLE_CACHE:
        cached = _BUNDLE_CACHE[cache_key]
        if time.time() - cached.get("_cached_at", 0) < _BUNDLE_CACHE_TTL:
            return {k: v for k, v in cached.items() if not k.startswith("_")}

    result: dict[str, Any] = {
        "is_bundled": False,
        "confidence": 0.0,
        "risk_label": "unknown",
        "atomic_block_score": 0.0,
        "common_funder_score": 0.0,
        "temporal_score": 0.0,
        "distribution_anomaly_score": 0.0,
        "concentration_score": 0.0,
        "top3_holder_pct": 0.0,
        "top10_holder_pct": 0.0,
        "holder_count": 0,
        "bundle_blocks": [],
        "common_funders": [],
        "source": "unavailable",
    }

    try:
        import asyncio

        # Run all queries in parallel
        atomic_task = asyncio.create_task(run_sync(_query_atomic_blocks, token_address, chain))
        funder_task = asyncio.create_task(run_sync(_query_common_funders, token_address, chain))
        holder_task = asyncio.create_task(run_sync(_query_holder_concentration, token_address, chain))
        launch_task = asyncio.create_task(run_sync(_query_launch_cluster, token_address, chain))

        atomic_blocks, funders, holder_data, launch_cluster = await asyncio.gather(
            atomic_task,
            funder_task,
            holder_task,
            launch_task,
        )

        # Compute scores
        if atomic_blocks:
            _score_atomic_blocks(result, atomic_blocks)

        if funders:
            _score_common_funders(result, funders)

        if holder_data:
            _score_holder_concentration(result, holder_data)

        if launch_cluster:
            _score_launch_cluster(result, launch_cluster)

        # Aggregate score
        _compute_bundle_score(result)

        if result["atomic_block_score"] > 0 or result["common_funder_score"] > 0:
            result["source"] = "own"

    except (
        ConnectionError,
        OSError,
        TimeoutError,
        RuntimeError,
        ValueError,
        KeyError,
        TypeError,
        CHDriverError,
    ) as exc:
        logger.debug("own_bundle_query_failed for %s: %s", token_address[:16], exc)
        result["source"] = "unavailable"

    # Cache
    _cache_bundle(cache_key, result)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "own_bundle_detect_latency",
        ms=round(_elapsed_ms, 1),
        token=token_address[:16],
        chain=chain,
        bundled=result["is_bundled"],
        confidence=result["confidence"],
    )

    return result


async def detect_bundled_launch(
    token_address: str,
    chain: str,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Detect a coordinated bundle launch (sniper/bot buys at deployment).

    Analyzes the FIRST ``_LAUNCH_WINDOW_BLOCKS`` blocks after the token's
    deploy event (from ``deployer_events``; falls back to the token's first
    transfer block) and flags coordinated clusters:

      (a) multiple distinct wallets buying in the same launch blocks,
      (b) wallets whose first-ever on-chain tx is this token (no prior
          token history),
      (c) launch buyers funded by the same wallet (deployer or a shared
          common funder),
      (d) similar purchase amounts (bot-sized buys — low coefficient of
          variation).

    Best-effort: returns an ``unavailable`` result when ClickHouse has no
    deploy/transfer data for the token.

    Args:
        token_address: Token contract address
        chain: Chain name
        force_fresh: Bypass cache

    Returns:
        {
            "check": "bundle_launch",
            "result": "scanned" | "unavailable" | "error",
            "source": "own",
            "token_address": str,
            "chain": str,
            "is_bundled": bool,
            "confidence": float,  # 0-100
            "risk_label": str,
            "launch_cluster_score": float,  # 0-100
            "first_time_buyer_pct": float,
            "similar_amount_score": float,  # 0-100
            "deployer_funded_pct": float,
            "common_funder_score": float,  # 0-100
            "deploy_block": int | None,
            "cluster_blocks": [{"block_number": int, "buyer_count": int}],
            "launch_buyers": int,
            "common_funders": [...],
        }
    """
    token_address = token_address.lower()
    chain = _normalize_chain(chain)
    _start = time.monotonic()

    cache_key = f"launch:{token_address}:{chain}"
    if not force_fresh and cache_key in _BUNDLE_CACHE:
        cached = _BUNDLE_CACHE[cache_key]
        if time.time() - cached.get("_cached_at", 0) < _BUNDLE_CACHE_TTL:
            return {k: v for k, v in cached.items() if not k.startswith("_")}

    result: dict[str, Any] = {
        "check": "bundle_launch",
        "result": "unavailable",
        "source": "own",
        "token_address": token_address,
        "chain": chain,
        "is_bundled": False,
        "confidence": 0.0,
        "risk_label": "low",
        "launch_cluster_score": 0.0,
        "first_time_buyer_pct": 0.0,
        "similar_amount_score": 0.0,
        "deployer_funded_pct": 0.0,
        "common_funder_score": 0.0,
        "deploy_block": None,
        "cluster_blocks": [],
        "launch_buyers": 0,
        "common_funders": [],
    }

    try:
        import asyncio

        cluster_task = asyncio.create_task(run_sync(_query_launch_cluster, token_address, chain))
        funder_task = asyncio.create_task(run_sync(_query_common_funders, token_address, chain))

        cluster, funders = await asyncio.gather(cluster_task, funder_task)

        if cluster:
            _score_launch_cluster(result, cluster)
        if funders:
            _score_common_funders(result, funders)

        _compute_launch_bundle_verdict(result)

        if result["launch_cluster_score"] > 0 or result["common_funder_score"] > 0:
            result["result"] = "scanned"
    except (
        ConnectionError,
        OSError,
        TimeoutError,
        RuntimeError,
        ValueError,
        KeyError,
        TypeError,
        CHDriverError,
    ) as exc:
        logger.debug("launch_bundle_query_failed for %s: %s", token_address[:16], exc)
        result["result"] = "unavailable"

    _cache_bundle(cache_key, result)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "launch_bundle_detect_latency",
        ms=round(_elapsed_ms, 1),
        token=token_address[:16],
        chain=chain,
        bundled=result["is_bundled"],
        launch_score=result["launch_cluster_score"],
    )

    return result


# ═══════════════════════════════════════════════════════════════════════════
# CLICKHOUSE QUERIES
# ═══════════════════════════════════════════════════════════════════════════


def _query_atomic_blocks(
    token_address: str,
    chain: str,
) -> list[dict[str, Any]] | None:
    """Query for blocks where multiple wallets bought the token.

    A high number of buyers in a single block is a strong signal
    of coordinated launch bundling.

    Returns: [{"block_number": int, "buyer_count": int, "buy_count": int}, ...]
    """
    client = _get_ch_client()
    if client is None:
        return None

    try:
        rows = client.execute(
            """
            SELECT
                block_number,
                uniqExact(from_address) AS buyer_count,
                count() AS total_buys
            FROM wallet_memory.evm_transfers
            WHERE token_address = %(token)s
              AND chain = %(chain)s
              AND to_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                      '0x000000000000000000000000000000000000dead')
              AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                        '0x000000000000000000000000000000000000dead')
              AND block_timestamp >= now() - INTERVAL 7 DAY
            GROUP BY block_number
            HAVING buyer_count >= 3
            ORDER BY buyer_count DESC
            LIMIT 20
            """,
            {"token": token_address, "chain": chain},
        )

        if not rows:
            return None

        return [
            {
                "block_number": r[0],
                "buyer_count": r[1],
                "total_buys": r[2],
            }
            for r in rows
        ]
    except (
        ConnectionError,
        OSError,
        TimeoutError,
        RuntimeError,
        ValueError,
        KeyError,
        TypeError,
        CHDriverError,
    ) as exc:
        logger.debug("atomic_blocks_query_failed: %s", exc)
        return None


def _query_common_funders(
    token_address: str,
    chain: str,
) -> list[dict[str, Any]] | None:
    """Query for common funding sources among buyers.

    When multiple token buyers were funded by the same source wallet,
    it suggests coordinated bundling.

    Returns: [{"funder": str, "funded_count": int, "funded_addresses": [str]}]
    """
    client = _get_ch_client()
    if client is None:
        return None

    try:
        # First, get all buyers of this token
        buyers_rows = client.execute(
            """
            SELECT DISTINCT from_address
            FROM wallet_memory.evm_transfers
            WHERE token_address = %(token)s
              AND chain = %(chain)s
              AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                        '0x000000000000000000000000000000000000dead')
              AND block_timestamp >= now() - INTERVAL 7 DAY
            LIMIT 200
            """,
            {"token": token_address, "chain": chain},
        )

        if not buyers_rows or len(buyers_rows) < 3:
            return None

        buyer_addresses = [r[0] for r in buyers_rows]

        # Now find who funded these buyers (they received native token from a funder)
        if chain == "solana":
            # Solana uses native_transfers table if available, otherwise skip
            return None

        # For EVM: find common senders to these buyers
        funder_rows = client.execute(
            """
            SELECT
                from_address AS funder,
                uniqExact(to_address) AS funded_count,
                groupUniqArray(to_address) AS funded_addresses_sample
            FROM wallet_memory.evm_transfers
            WHERE token_address = '0x0000000000000000000000000000000000000000'
              AND chain = %(chain)s
              AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                        '0x000000000000000000000000000000000000dead',
                                        '0xdead000000000000000000000000000000000000')
              AND to_address IN %(buyers)s
              AND block_timestamp >= now() - INTERVAL 14 DAY
              AND block_timestamp <= now() - INTERVAL 1 HOUR
              AND value > '0'
            GROUP BY funder
            HAVING funded_count >= 2
            ORDER BY funded_count DESC
            LIMIT 10
            """,
            {"chain": chain, "buyers": buyer_addresses},
        )

        if not funder_rows:
            return None

        return [
            {
                "funder": r[0],
                "funded_count": r[1],
                "funded_addresses_sample": r[2][:10] if r[2] else [],
            }
            for r in funder_rows
        ]

    except (
        ConnectionError,
        OSError,
        TimeoutError,
        RuntimeError,
        ValueError,
        KeyError,
        TypeError,
        CHDriverError,
    ) as exc:
        logger.debug("common_funders_query_failed: %s", exc)
        return None


def _query_holder_concentration(
    token_address: str,
    chain: str,
) -> dict[str, Any] | None:
    """Query holder concentration data.

    Extreme top-holder concentration is a strong bundling signal.

    Returns: {
        "top3_pct": float,
        "top10_pct": float,
        "holder_count": int,
    }
    """
    client = _get_ch_client()
    if client is None:
        return None

    try:
        rows = client.execute(
            """
            SELECT
                addr,
                sum(net) AS balance
            FROM (
                SELECT
                    to_address AS addr,
                    toFloat64OrZero(value) AS net
                FROM wallet_memory.evm_transfers
                WHERE token_address = %(token)s
                  AND chain = %(chain)s
                  AND to_address NOT IN ('', '0x0000000000000000000000000000000000000000')
                UNION ALL
                SELECT
                    from_address AS addr,
                    -toFloat64OrZero(value) AS net
                FROM wallet_memory.evm_transfers
                WHERE token_address = %(token)s
                  AND chain = %(chain)s
                  AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000')
            )
            WHERE addr != ''
            GROUP BY addr
            HAVING balance > 0
            ORDER BY balance DESC
            LIMIT 50
            """,
            {"token": token_address, "chain": chain},
        )

        if not rows:
            return None

        total_balance = sum(float(r[1] or 0) for r in rows)
        if total_balance <= 0:
            return None

        balances = [float(r[1] or 0) for r in rows]
        top3_pct = (
            sum(balances[:3]) / total_balance * 100 if len(balances) >= 3 else sum(balances) / total_balance * 100
        )
        top10_pct = (
            sum(balances[:10]) / total_balance * 100 if len(balances) >= 10 else sum(balances) / total_balance * 100
        )

        return {
            "top3_pct": round(top3_pct, 2),
            "top10_pct": round(top10_pct, 2),
            "holder_count": len(rows),
        }

    except (
        ConnectionError,
        OSError,
        TimeoutError,
        RuntimeError,
        ValueError,
        KeyError,
        TypeError,
        CHDriverError,
    ) as exc:
        logger.debug("holder_concentration_query_failed: %s", exc)
        return None


def _query_launch_cluster(
    token_address: str,
    chain: str,
    window_blocks: int = _LAUNCH_WINDOW_BLOCKS,
) -> dict[str, Any] | None:
    """Query launch-window buy cluster data for a token.

    Finds the token's deploy block (from ``deployer_events``, falling back to
    its first transfer block) then pulls every buy in the first ``window_blocks``
    blocks, annotated with:

      - ``is_first_tx`` — the buyer's first-ever on-chain tx is this token
        (no prior token history — fresh sniper farm signal),
      - ``deployer_funded`` — the buyer was funded by the deployer wallet
        (same-funder signal).

    Returns: {
        "deploy_block": int | None,
        "launch_buyers": [
            {"buyer": str, "block_number": int, "buy_count": int,
             "amount": float, "is_first_tx": bool, "deployer_funded": bool}
        ],
        "cluster_blocks": [{"block_number": int, "buyer_count": int}],
    }
    """
    client = _get_ch_client()
    if client is None:
        return None

    try:
        # ── 1. Deploy block (deployer_events first appearance) ──
        deploy_block: int | None = None
        deployer: str | None = None
        deploy_rows = client.execute(
            """
            SELECT block_number, deployer
            FROM wallet_memory.deployer_events
            WHERE token_address = %(token)s
              AND chain = %(chain)s
              AND block_number > 0
            ORDER BY block_number ASC
            LIMIT 1
            """,
            {"token": token_address, "chain": chain},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        if deploy_rows and deploy_rows[0]:
            deploy_block = int(deploy_rows[0][0] or 0)
            deployer = str(deploy_rows[0][1] or "").lower() or None

        # ── 1b. Fallback: first transfer block ──
        if not deploy_block:
            min_rows = client.execute(
                """
                SELECT min(block_number)
                FROM wallet_memory.evm_transfers
                WHERE token_address = %(token)s
                  AND chain = %(chain)s
                """,
                {"token": token_address, "chain": chain},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            )
            if min_rows and min_rows[0] and min_rows[0][0]:
                deploy_block = int(min_rows[0][0])

        if not deploy_block:
            return None

        start_block = deploy_block
        end_block = deploy_block + max(0, window_blocks - 1)

        # ── 2. Launch-window buys grouped by wallet ──
        buy_rows = client.execute(
            """
            SELECT
                from_address AS buyer,
                block_number,
                count() AS buy_count,
                sum(toFloat64OrZero(value)) AS amount
            FROM wallet_memory.evm_transfers
            WHERE token_address = %(token)s
              AND chain = %(chain)s
              AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                        '0x000000000000000000000000000000000000dead')
              AND to_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                        '0x000000000000000000000000000000000000dead')
              AND block_number >= %(start)s
              AND block_number <= %(end)s
            GROUP BY from_address, block_number
            ORDER BY block_number ASC
            LIMIT 300
            """,
            {"token": token_address, "chain": chain, "start": start_block, "end": end_block},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )

        if not buy_rows:
            return {"deploy_block": deploy_block, "launch_buyers": [], "cluster_blocks": []}

        buyers = list(dict.fromkeys(str(r[0] or "").lower() for r in buy_rows if r and r[0]))

        # ── 3. Per-buyer first-ever tx (any token, in/out) — prior-history check ──
        first_tx_map: dict[str, str] = {}
        if buyers:
            first_rows = client.execute(
                """
                SELECT
                    addr,
                    argMin(token_address, block_timestamp) AS first_token
                FROM (
                    SELECT to_address AS addr, token_address, block_timestamp
                    FROM wallet_memory.evm_transfers
                    WHERE chain = %(chain)s
                      AND to_address IN %(buyers)s
                      AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                                '0x000000000000000000000000000000000000dead')
                    UNION ALL
                    SELECT from_address AS addr, token_address, block_timestamp
                    FROM wallet_memory.evm_transfers
                    WHERE chain = %(chain)s
                      AND from_address IN %(buyers)s
                      AND to_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                                '0x000000000000000000000000000000000000dead')
                )
                GROUP BY addr
                """,
                {"buyers": tuple(buyers), "chain": chain},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            )
            for row in first_rows or []:
                if row and row[0]:
                    first_tx_map[str(row[0]).lower()] = str(row[1] or "").lower()

        # ── 4. Deployer-funded launch buyers (same-funder signal) ──
        deployer_funded: set[str] = set()
        if deployer and buyers:
            funded_rows = client.execute(
                """
                SELECT DISTINCT to_address
                FROM wallet_memory.evm_transfers
                WHERE chain = %(chain)s
                  AND from_address = %(deployer)s
                  AND to_address IN %(buyers)s
                  AND block_timestamp >= now() - INTERVAL 14 DAY
                """,
                {"deployer": deployer, "buyers": tuple(buyers), "chain": chain},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            )
            for row in funded_rows or []:
                if row and row[0]:
                    deployer_funded.add(str(row[0]).lower())

        # ── 5. Assemble result ──
        launch_buyers: list[dict[str, Any]] = []
        for r in buy_rows:
            buyer = str(r[0] or "").lower()
            if not buyer:
                continue
            launch_buyers.append(
                {
                    "buyer": buyer,
                    "block_number": int(r[1] or 0),
                    "buy_count": int(r[2] or 0),
                    "amount": round(float(r[3] or 0), 6),
                    "is_first_tx": first_tx_map.get(buyer) == token_address,
                    "deployer_funded": buyer in deployer_funded,
                }
            )

        block_counts: dict[int, int] = {}
        for b in launch_buyers:
            blk = int(b["block_number"])
            block_counts[blk] = block_counts.get(blk, 0) + 1
        cluster_blocks = [{"block_number": blk, "buyer_count": cnt} for blk, cnt in sorted(block_counts.items())]

        return {
            "deploy_block": deploy_block,
            "launch_buyers": launch_buyers,
            "cluster_blocks": cluster_blocks,
        }

    except (
        ConnectionError,
        OSError,
        TimeoutError,
        RuntimeError,
        ValueError,
        KeyError,
        TypeError,
        CHDriverError,
    ) as exc:
        logger.debug("launch_cluster_query_failed: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# SCORING
# ═══════════════════════════════════════════════════════════════════════════


def _score_atomic_blocks(
    result: dict[str, Any],
    blocks: list[dict[str, Any]],
) -> None:
    """Score based on atomic block buys."""
    if not blocks:
        return

    result["bundle_blocks"] = blocks

    # Find the highest buyer count in a single block
    max_buyers = max(b.get("buyer_count", 0) for b in blocks)

    # Score: 0-100 based on max buyers in one block
    if max_buyers >= 20:
        result["atomic_block_score"] = 100
    elif max_buyers >= 10:
        result["atomic_block_score"] = 80
    elif max_buyers >= 7:
        result["atomic_block_score"] = 60
    elif max_buyers >= 5:
        result["atomic_block_score"] = 40
    elif max_buyers >= 3:
        result["atomic_block_score"] = 20

    # Bonus for multiple high-buyer blocks
    high_buyer_blocks = sum(1 for b in blocks if b.get("buyer_count", 0) >= 5)
    if high_buyer_blocks >= 3:
        result["atomic_block_score"] = min(100, result["atomic_block_score"] + 15)


def _score_common_funders(
    result: dict[str, Any],
    funders: list[dict[str, Any]],
) -> None:
    """Score based on common funding sources."""
    if not funders:
        return

    result["common_funders"] = funders

    # Max funded count
    max_funded = max(f.get("funded_count", 0) for f in funders)

    # Score based on how many buyers share a funder
    if max_funded >= 10:
        result["common_funder_score"] = 100
    elif max_funded >= 5:
        result["common_funder_score"] = 70
    elif max_funded >= 3:
        result["common_funder_score"] = 40
    elif max_funded >= 2:
        result["common_funder_score"] = 20

    # Bonus for multiple funders each funding multiple wallets
    multi_funders = sum(1 for f in funders if f.get("funded_count", 0) >= 3)
    if multi_funders >= 3:
        result["common_funder_score"] = min(100, result["common_funder_score"] + 10)


def _score_holder_concentration(
    result: dict[str, Any],
    holder_data: dict[str, Any],
) -> None:
    """Score based on holder concentration."""
    result["top3_holder_pct"] = holder_data.get("top3_pct", 0)
    result["top10_holder_pct"] = holder_data.get("top10_pct", 0)
    result["holder_count"] = holder_data.get("holder_count", 0)

    top3 = result["top3_holder_pct"]
    top10 = result["top10_holder_pct"]

    if top3 > 90:
        result["distribution_anomaly_score"] = 100
        result["concentration_score"] = 100
    elif top3 > 80:
        result["distribution_anomaly_score"] = 80
        result["concentration_score"] = 80
    elif top3 > 60:
        result["distribution_anomaly_score"] = 60
        result["concentration_score"] = 60
    elif top10 > 90:
        result["distribution_anomaly_score"] = 50
        result["concentration_score"] = 50
    elif top3 > 40:
        result["distribution_anomaly_score"] = 30
        result["concentration_score"] = 30
    elif top10 > 70:
        result["distribution_anomaly_score"] = 20
        result["concentration_score"] = 20


def _score_launch_cluster(
    result: dict[str, Any],
    cluster: dict[str, Any],
) -> None:
    """Score a launch-window buy cluster for coordinated bundling.

    Pure logic over ``_query_launch_cluster`` output. Signals (each 0-100):

      - ``launch_cluster_score`` — distinct wallets buying in the same launch
        block (density, 60%) + share of first-time buyers (40%).
      - ``similar_amount_score`` — low coefficient of variation among launch
        buy amounts (bot-sized buys cluster at similar amounts).
      - ``deployer_funded_pct`` — share of launch buyers funded directly by
        the deployer (same-funder signal).
    """
    launch_buyers = cluster.get("launch_buyers") or []
    cluster_blocks = cluster.get("cluster_blocks") or []
    deploy_block = cluster.get("deploy_block")

    result["deploy_block"] = deploy_block if deploy_block else None
    result["cluster_blocks"] = cluster_blocks

    if not launch_buyers:
        return

    result["launch_buyers"] = len(launch_buyers)

    # (a) + (d) — first-time buyers with no prior token history
    first_tx_count = sum(1 for b in launch_buyers if b.get("is_first_tx"))
    first_tx_pct = first_tx_count / len(launch_buyers) * 100
    result["first_time_buyer_pct"] = round(first_tx_pct, 1)

    # Cluster density: max distinct buyers in a single launch block.
    max_block_buyers = max((int(b.get("buyer_count", 0)) for b in cluster_blocks), default=0)
    density_score = min(100.0, max_block_buyers * 10.0)

    first_tx_score = min(100.0, first_tx_pct)
    result["launch_cluster_score"] = round(0.6 * density_score + 0.4 * first_tx_score, 1)

    # (c) — similar purchase amounts (low CV among positive amounts)
    amounts = [float(b.get("amount", 0) or 0) for b in launch_buyers if float(b.get("amount", 0) or 0) > 0]
    if len(amounts) >= 2:
        mean = sum(amounts) / len(amounts)
        if mean > 0:
            variance = sum((a - mean) ** 2 for a in amounts) / len(amounts)
            std = variance**0.5
            cv = std / mean
            similar = 0.0
            for max_cv, score in _SIMILAR_AMOUNT_CV_BANDS:
                if cv <= max_cv:
                    similar = score
                    break
            result["similar_amount_score"] = similar

    # (b) — same-funder signal: share funded directly by the deployer
    deployer_count = sum(1 for b in launch_buyers if b.get("deployer_funded"))
    result["deployer_funded_pct"] = round(deployer_count / len(launch_buyers) * 100, 1)


def _compute_launch_bundle_verdict(result: dict[str, Any]) -> None:
    """Aggregate launch-window signals into ``is_bundled`` / ``confidence``.

    Used by ``detect_bundled_launch``. Weights:
      - launch_cluster_score (density + first-time buyers): 0.40
      - similar_amount_score (bot-sized buys): 0.20
      - common_funder_score (shared funder among buyers): 0.25
      - deployer_funded_pct (deployer-funded wallets): 0.15
    """
    launch = float(result.get("launch_cluster_score", 0) or 0)
    similar = float(result.get("similar_amount_score", 0) or 0)
    funder = float(result.get("common_funder_score", 0) or 0)
    deployer = float(result.get("deployer_funded_pct", 0) or 0)

    aggregated = launch * 0.40 + similar * 0.20 + funder * 0.25 + min(deployer, 100.0) * 0.15
    result["confidence"] = round(aggregated, 1)

    if aggregated >= 60:
        result["is_bundled"] = True
        result["risk_label"] = "critical"
    elif aggregated >= 40:
        result["is_bundled"] = True
        result["risk_label"] = "high"
    elif aggregated >= 25:
        result["is_bundled"] = True
        result["risk_label"] = "medium"
    else:
        result["is_bundled"] = False
        result["risk_label"] = "low"


def _compute_bundle_score(result: dict[str, Any]) -> None:
    """Compute aggregate bundle score from sub-scores.

    The launch-cluster signal (when present) contributes a capped bonus of
    ``launch_cluster_score * 0.10`` — the base weights are unchanged so
    existing behavior is preserved when no launch data is available.
    """
    atomic = result.get("atomic_block_score", 0) or 0
    funder = result.get("common_funder_score", 0) or 0
    distribution = result.get("distribution_anomaly_score", 0) or 0
    concentration = result.get("concentration_score", 0) or 0
    launch = result.get("launch_cluster_score", 0) or 0

    # Weighted combination
    scores = [
        (atomic, 0.35),  # Atomic block buys: strongest signal
        (funder, 0.25),  # Common funders: strong signal
        (distribution, 0.25),  # Distribution anomaly: moderate
        (concentration, 0.15),  # Concentration: supporting
    ]

    aggregated = sum(s * w for s, w in scores)
    # Launch-cluster bonus (0-10): a clear launch bundle (multiple first-time
    # buyers in the first blocks) pushes a borderline aggregate over the line.
    aggregated = aggregated + (launch * 0.10)
    result["confidence"] = round(aggregated, 1)

    # Determine bundled status
    if aggregated >= 70:
        result["is_bundled"] = True
        result["risk_label"] = "critical"
    elif aggregated >= 50:
        result["is_bundled"] = True
        result["risk_label"] = "high"
    elif aggregated >= 30:
        result["is_bundled"] = True
        result["risk_label"] = "medium"
    else:
        result["risk_label"] = "low"


# ═══════════════════════════════════════════════════════════════════════════
# CACHING
# ═══════════════════════════════════════════════════════════════════════════


def _cache_bundle(key: str, data: dict[str, Any]) -> None:
    data["_cached_at"] = time.time()
    _BUNDLE_CACHE[key] = data
    if len(_BUNDLE_CACHE) > _BUNDLE_CACHE_MAX:
        _BUNDLE_CACHE.pop(next(iter(_BUNDLE_CACHE)), None)


def get_cache_stats() -> dict[str, Any]:
    return _common_get_cache_stats(
        _BUNDLE_CACHE,
        _BUNDLE_CACHE_MAX,
        _BUNDLE_CACHE_TTL,
        extra={"clickhouse_connected": _ch_client is not None},
    )


def clear_cache() -> None:
    _common_clear_cache(_BUNDLE_CACHE)
