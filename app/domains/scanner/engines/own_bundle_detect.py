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

Degodding split (2026-08-13): ClickHouse queries moved to
``engines/_own_bundle_queries.py``, pure scoring to
``engines/_own_bundle_scoring.py``, and shared plumbing (lazy client,
chain normalisation, cache, constants) to ``engines/_own_bundle_common.py``.
This module is the public API only; every name is re-exported so
``bd._query_*`` / ``bd._score_*`` references in tests and callers keep
working (test_own_bundle_detect / test_scan_enhancements use them).
"""

from __future__ import annotations

import asyncio
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
from app.domains.scanner.engines._own_bundle_common import (
    _BUNDLE_CACHE,
    _BUNDLE_CACHE_MAX,
    _BUNDLE_CACHE_TTL,
    _LAUNCH_WINDOW_BLOCKS,  # noqa: F401  (re-exported for tests/callers)
    _QUERY_TIMEOUT_SECONDS,  # noqa: F401  (re-exported)
    _SIMILAR_AMOUNT_CV_BANDS,  # noqa: F401  (re-exported)
    _ch_client,
    _normalize_chain,
)
from app.domains.scanner.engines._own_bundle_queries import (
    # Re-exported so ``bd._query_*`` references keep resolving. Tests
    # (test_scan_enhancements) patch these on the ``bd`` namespace and
    # dispatch on function identity — re-export preserves identity.
    _query_atomic_blocks,
    _query_common_funders,
    _query_holder_concentration,
    _query_launch_cluster,
)
from app.domains.scanner.engines._own_bundle_scoring import (
    # Re-exported for tests that call them as bd.<name>.
    _compute_bundle_score,
    _compute_launch_bundle_verdict,
    _score_atomic_blocks,
    _score_common_funders,
    _score_holder_concentration,
    _score_launch_cluster,
)

logger = get_logger(__name__)


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
