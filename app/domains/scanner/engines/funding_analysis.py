"""
Clever Wallet Analysis — Exchange Funding, Freshness, Holder Overlap
=====================================================================
RugMunchScan metrics other security firms don't bother with.

Three metrics, all computed from our own ClickHouse data:

  1. ``analyze_funding_sources`` — Exchange-Funded Wallet Percentage.
     What % of a token's early holders were funded by CEX wallets?
     High % = potential insider/coordinated launch. Also classifies
     fresh-wallet-funded and mixer-funded holders.

  2. ``compute_wallet_freshness`` — Wallet Freshness Score.
     How "fresh" is a wallet (age, tx count, first funder)? New wallets
     buying a token = potential farm.

  3. ``compute_holder_overlap`` — Holder Overlap Analysis.
     How many holders of token A also hold token B? High overlap (>30%)
     with a known scam token = coordinated farm group red flag.

  4. ``compute_wallet_metrics`` — Token-Level Wallet Freshness Metrics.
     For ANY token: % of holders <30 days old, % funded by CEX wallets,
     % whose first-ever on-chain tx is this token (completely fresh).

Data Sources:
  Primary: wallet_memory.evm_transfers (funding trace, holder sets)
  Primary: wallet_memory.solana_spl_balance_changes (Solana holder sets)
  Primary: wallet_memory.wallet_labels (39.4M entity labels)
  Primary: wallet_memory.deployer_history (known scam tokens, is_scam_related)
  Static:  known_entities.EXCHANGE_WALLETS / EVM_KNOWN_CONTRACTS (CEX/mixer)

Algorithm (Metric 1):
  1. Resolve top N holders (passed in, or from evm_transfers /
     solana_spl_balance_changes net balances).
  2. For each holder, find their FIRST incoming transfer on the chain
     (argMin(from_address, block_timestamp) across all tokens) — the
     wallet's original funding source.
  3. Classify the funder via wallet_labels + known_entities:
     CEX → exchange_funded; mixer → mixer_funded; <=10 txs → fresh_wallet.
  4. Cross-reference funders — funders that funded many holders surface in
     ``top_funders``.
  5. Percentages + risk assessment (exchange_funded_pct > 50 → HIGH).

Every ClickHouse query is bounded by ``max_execution_time`` (time-boxed —
see Cross-Cutting Principles). All caches are graceful on failure.

Usage:
    from app.domains.scanner.engines.funding_analysis import (
        analyze_funding_sources,
        compute_holder_overlap,
        compute_wallet_freshness,
        compute_wallet_metrics,
    )

    funding = await analyze_funding_sources("0x...", "ethereum", holders=[...])
    fresh   = await compute_wallet_freshness("0x...", "ethereum")
    overlap = await compute_holder_overlap("0xA...", "0xB...", "ethereum")
    metrics = await compute_wallet_metrics("0x...", "ethereum")

NOTE (degodding 2026-08-07): 1,358 lines. Split is deferred (all three metrics share
ClickHouse query plumbing and the per-token result model). If splitting, extract the
three metric analyzers (funding sources / freshness / holder overlap) into
``engines/funding_analysis/_analyzers.py`` keyed by a shared result-merge helper.

NOTE (degodding 2026-08-08): the pure-logic section (funder classification,
freshness scoring, risk assessment, top-funder / scam-overlap aggregation)
was extracted into ``engines/_funding_helpers.py`` (wave 5). This module is
now the public API + ClickHouse computation layer only.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, cast

try:
    from clickhouse_driver.errors import Error as CHDriverError
except ImportError:  # pragma: no cover
    CHDriverError = Exception

from app.core.logging import get_logger
from app.domains.databus.providers.chains.chain_indexer import (
    _get_ch_client,
    _normalize_chain,
)
from app.domains.scanner.engines._common import (
    _query_batch_labels,
    clear_cache as _common_clear_cache,
    get_cache_stats as _common_get_cache_stats,
    redis_get,
    redis_set,
    run_sync,
    to_epoch,
    to_iso,
)
from app.domains.scanner.engines._funding_helpers import (
    # Re-exported for tests that reference them as fa.<name> (see
    # tests/unit/domain/scanner/test_funding_analysis.py).
    _CEX_FUNDED_PTS,  # noqa: F401
    _FRESH_CLEAN_MIN,  # noqa: F401
    _FRESH_SUSPICIOUS_MIN,  # noqa: F401
    _OVERLAP_SCAM_PCT,
    _RISK_DESCRIPTIONS,
    _aggregate_scam_overlap,
    _assess_funding_risk,
    _build_top_funders,
    _classify_freshness,
    _classify_funder,
    _compute_freshness_score,
)

# Legacy module-local names kept for consistency with the other engines.
_redis_get = redis_get
_redis_set = redis_set

logger = get_logger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────
REDIS_CACHE_PREFIX = "funding_analysis"
REDIS_TTL_SECONDS = 600  # 10 minutes — holder funding data changes slowly
_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_TTL = 300  # 5 minutes in-process mirror
_CACHE_MAX = 200

# ── Query / analysis limits (time-boxing) ─────────────────────────────────
_QUERY_TIMEOUT_SECONDS = 10
_MAX_HOLDERS = 50  # top-N holders analyzed for funding sources
_MAX_SCAM_TOKENS = 5  # known-scam tokens cross-referenced for overlap

# ── Classification thresholds ─────────────────────────────────────────────
# Pure-logic thresholds (funder classification, freshness scoring, risk
# bands, scam-overlap) now live in ``engines/_funding_helpers.py``.
_ZERO_ADDR = "0x0000000000000000000000000000000000000000"
_DEAD_ADDR = "0x000000000000000000000000000000000000dead"

# ── Token-level wallet freshness metrics (compute_wallet_metrics) ────────
_FRESH_WALLET_AGE_DAYS = 30  # holder wallet < 30 days old => "fresh"


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API — Metric 1: Exchange-Funded Wallet Percentage
# ═══════════════════════════════════════════════════════════════════════════


async def analyze_funding_sources(
    token_address: str,
    chain: str,
    holders: list[str] | None = None,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Trace the first funding source of a token's top holders.

    Args:
        token_address: Token contract address.
        chain: Chain name (ethereum, bsc, arbitrum, solana, ...).
        holders: Top-N holder addresses. When omitted, resolved from
            ``evm_transfers`` (EVM) or ``solana_spl_balance_changes``
            (Solana) net balances.
        force_fresh: Bypass all caches.

    Returns:
        {
            "check": "funding_sources",
            "result": "scanned" | "unavailable" | "error",
            "source": "own",
            "token_address": "0x...",
            "chain": "ethereum",
            "holders_analyzed": 12,
            "exchange_funded_pct": 45.2,
            "fresh_wallet_pct": 22.1,
            "mixer_funded_pct": 1.3,
            "unknown_pct": 31.4,
            "top_funders": [
                {"address": "0x...", "label": "Binance Hot Wallet", "funded_holders": 12}
            ],
            "holder_details": [
                {"holder": "0x...", "funder": "0x...", "funding_type": "exchange_funded"}
            ],
            "risk_assessment": "HIGH",
            "note": "...",
        }
    """
    token_address = token_address.lower().strip()
    chain_norm = _normalize_chain(chain)
    _start = time.monotonic()

    if not token_address or not chain_norm:
        return _error_result(token_address, chain_norm, "token_address and chain are required")

    # ── 1. Redis fast cache ──
    cache_key = f"{REDIS_CACHE_PREFIX}:funding:{chain_norm}:{token_address}"
    if not force_fresh:
        cached = await _redis_get(cache_key)
        if cached is not None:
            logger.debug("funding_analysis_redis_hit", key=cache_key)
            return cast("dict[str, Any]", cached)

    # ── 2. In-process mirror cache ──
    if not force_fresh and cache_key in _CACHE:
        entry = _CACHE[cache_key]
        if time.time() - entry.get("_cached_at", 0) < _CACHE_TTL:
            return {k: v for k, v in entry.items() if not k.startswith("_")}

    # ── 3. Full ClickHouse computation ──
    try:
        result = await _compute_funding(token_address, chain_norm, holders)
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("funding_analysis_query_failed: %s", exc)
        result = _unavailable_result(token_address, chain_norm, reason=str(exc)[:120])

    if result.get("result") == "scanned":
        await _redis_set(cache_key, result, REDIS_TTL_SECONDS)
        result["_cached_at"] = time.time()
        _CACHE[cache_key] = result
        if len(_CACHE) > _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)), None)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "funding_analysis_latency",
        ms=round(_elapsed_ms, 1),
        token=token_address[:16],
        chain=chain_norm,
        holders=result.get("holders_analyzed", 0),
        exchange_pct=result.get("exchange_funded_pct", 0.0),
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API — Metric 2: Wallet Freshness Score
# ═══════════════════════════════════════════════════════════════════════════


async def compute_wallet_freshness(
    address: str,
    chain: str,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Score how "fresh" (new / farm-like) a wallet is.

    Args:
        address: Wallet address to analyze.
        chain: Chain name (ethereum, bsc, arbitrum, solana, ...).
        force_fresh: Bypass all caches.

    Returns:
        {
            "check": "wallet_freshness",
            "result": "scanned" | "unavailable" | "error",
            "source": "own",
            "address": "0x...",
            "chain": "ethereum",
            "age_days": 3,
            "total_txs": 12,
            "funded_by_cex": True,
            "first_funder": "0x...",
            "freshness_score": 85,
            "classification": "FRESH_SUSPICIOUS",
            "note": "...",
        }

    Freshness bands (score 0-100, higher = fresher/more suspicious):
      - >= 70  → FRESH_SUSPICIOUS (new wallet with CEX funding / few txs)
      - >= 40  → FRESH_CLEAN (new-ish but no strong red flags)
      - <  40  → ESTABLISHED (old, active wallet)
    """
    address = address.lower().strip()
    chain_norm = _normalize_chain(chain)
    _start = time.monotonic()

    if not address or not chain_norm:
        return {
            "check": "wallet_freshness",
            "result": "error",
            "error": "address and chain are required",
            "address": address,
            "chain": chain_norm,
            "age_days": 0,
            "total_txs": 0,
            "funded_by_cex": False,
            "first_funder": None,
            "freshness_score": 0,
            "classification": "ESTABLISHED",
        }

    cache_key = f"{REDIS_CACHE_PREFIX}:fresh:{chain_norm}:{address}"
    if not force_fresh:
        cached = await _redis_get(cache_key)
        if cached is not None:
            logger.debug("wallet_freshness_redis_hit", key=cache_key)
            return cast("dict[str, Any]", cached)
    if not force_fresh and cache_key in _CACHE:
        entry = _CACHE[cache_key]
        if time.time() - entry.get("_cached_at", 0) < _CACHE_TTL:
            return {k: v for k, v in entry.items() if not k.startswith("_")}

    try:
        result = await _compute_freshness(address, chain_norm)
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("wallet_freshness_query_failed: %s", exc)
        result = _freshness_unavailable(address, chain_norm, reason=str(exc)[:120])

    if result.get("result") == "scanned":
        await _redis_set(cache_key, result, REDIS_TTL_SECONDS)
        result["_cached_at"] = time.time()
        _CACHE[cache_key] = result
        if len(_CACHE) > _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)), None)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "wallet_freshness_latency",
        ms=round(_elapsed_ms, 1),
        wallet=address[:10],
        chain=chain_norm,
        score=result.get("freshness_score", 0),
        classification=result.get("classification", "ESTABLISHED"),
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API — Metric 3: Holder Overlap Analysis
# ═══════════════════════════════════════════════════════════════════════════


async def compute_holder_overlap(
    token_a: str,
    token_b: str,
    chain: str,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Cross-reference the holder sets of two tokens.

    Args:
        token_a: First token (the scanned token).
        token_b: Second token (often a known scam token).
        chain: Chain name.
        force_fresh: Bypass all caches.

    Returns:
        {
            "check": "holder_overlap",
            "result": "scanned" | "unavailable" | "error",
            "source": "own",
            "token_a": "0xA...",
            "token_b": "0xB...",
            "chain": "ethereum",
            "holders_a": 120,
            "holders_b": 80,
            "common_holders": 45,
            "overlap_pct": 37.5,       # |A ∩ B| / |A| * 100
            "token_b_is_scam": True,
            "risk_assessment": "HIGH",  # >30% overlap with known scam
            "note": "...",
        }

    High overlap (>30%) with a known scam token = red flag
    (coordinated farm group).
    """
    token_a = token_a.lower().strip()
    token_b = token_b.lower().strip()
    chain_norm = _normalize_chain(chain)
    _start = time.monotonic()

    if not token_a or not token_b or not chain_norm:
        return _overlap_error(token_a, token_b, chain_norm, "token_a, token_b and chain are required")

    cache_key = f"{REDIS_CACHE_PREFIX}:overlap:{chain_norm}:{token_a}:{token_b}"
    if not force_fresh:
        cached = await _redis_get(cache_key)
        if cached is not None:
            logger.debug("holder_overlap_redis_hit", key=cache_key)
            return cast("dict[str, Any]", cached)
    if not force_fresh and cache_key in _CACHE:
        entry = _CACHE[cache_key]
        if time.time() - entry.get("_cached_at", 0) < _CACHE_TTL:
            return {k: v for k, v in entry.items() if not k.startswith("_")}

    try:
        result = await _compute_overlap(token_a, token_b, chain_norm)
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("holder_overlap_query_failed: %s", exc)
        result = _overlap_unavailable(token_a, token_b, chain_norm, reason=str(exc)[:120])

    if result.get("result") == "scanned":
        await _redis_set(cache_key, result, REDIS_TTL_SECONDS)
        result["_cached_at"] = time.time()
        _CACHE[cache_key] = result
        if len(_CACHE) > _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)), None)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "holder_overlap_latency",
        ms=round(_elapsed_ms, 1),
        token_a=token_a[:12],
        token_b=token_b[:12],
        chain=chain_norm,
        overlap_pct=result.get("overlap_pct", 0.0),
    )
    return result


async def analyze_scam_overlap(
    token_address: str,
    chain: str,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Cross-reference a token's holders against known scam tokens.

    Scanner-level helper: resolves up to ``_MAX_SCAM_TOKENS`` known scam
    tokens on the chain (from ``deployer_history`` where
    ``is_scam_related = 1``) and reports the worst holder overlap.

    Returns:
        {
            "check": "scam_overlap",
            "result": "scanned" | "unavailable" | "error",
            "source": "own",
            "token_address": "0x...",
            "chain": "ethereum",
            "scam_tokens_checked": 3,
            "max_overlap_pct": 37.5,
            "worst_token": "0xB...",
            "worst_token_is_scam": True,
            "risk_assessment": "HIGH",
            "comparisons": [ ... per-token overlap result ... ],
        }
    """
    token_address = token_address.lower().strip()
    chain_norm = _normalize_chain(chain)
    _start = time.monotonic()

    if not token_address or not chain_norm:
        return _scam_overlap_error(token_address, chain_norm, "token_address and chain are required")

    cache_key = f"{REDIS_CACHE_PREFIX}:scam_overlap:{chain_norm}:{token_address}"
    if not force_fresh:
        cached = await _redis_get(cache_key)
        if cached is not None:
            logger.debug("scam_overlap_redis_hit", key=cache_key)
            return cast("dict[str, Any]", cached)
    if not force_fresh and cache_key in _CACHE:
        entry = _CACHE[cache_key]
        if time.time() - entry.get("_cached_at", 0) < _CACHE_TTL:
            return {k: v for k, v in entry.items() if not k.startswith("_")}

    try:
        scam_tokens = await run_sync(_query_known_scam_tokens, chain_norm, _MAX_SCAM_TOKENS)
        scam_tokens = [t for t in scam_tokens if t and t.lower() != token_address]
        if not scam_tokens:
            result = {
                "check": "scam_overlap",
                "result": "unavailable",
                "source": "own",
                "token_address": token_address,
                "chain": chain_norm,
                "scam_tokens_checked": 0,
                "max_overlap_pct": 0.0,
                "worst_token": None,
                "worst_token_is_scam": False,
                "risk_assessment": "LOW",
                "comparisons": [],
                "note": "no known scam tokens on chain for cross-reference",
            }
        else:
            comparisons = []
            for scam_token in scam_tokens:
                overlap = await _compute_overlap(token_address, scam_token, chain_norm)
                if overlap.get("result") == "scanned":
                    comparisons.append(overlap)
            result = _aggregate_scam_overlap(token_address, chain_norm, comparisons)
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("scam_overlap_query_failed: %s", exc)
        result = _scam_overlap_error(token_address, chain_norm, str(exc)[:120])

    if result.get("result") == "scanned":
        await _redis_set(cache_key, result, REDIS_TTL_SECONDS)
        result["_cached_at"] = time.time()
        _CACHE[cache_key] = result
        if len(_CACHE) > _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)), None)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "scam_overlap_latency",
        ms=round(_elapsed_ms, 1),
        token=token_address[:16],
        chain=chain_norm,
        checked=result.get("scam_tokens_checked", 0),
        max_overlap=result.get("max_overlap_pct", 0.0),
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API — Metric 4: Token-Level Wallet Freshness Metrics
# ═══════════════════════════════════════════════════════════════════════════


async def compute_wallet_metrics(
    token_address: str,
    chain: str,
    holders: list[str] | None = None,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Compute token-level wallet freshness metrics for ANY token.

    Three metrics over the token's top holders (from ``evm_transfers`` net
    balances):

      - ``fresh_wallet_pct`` — % of holders whose wallet is <30 days old
        (first-seen on the chain is recent — farm-like concentration).
      - ``cex_funded_pct`` — % of holders whose FIRST funder is a CEX wallet
        (Binance, Coinbase, Kraken, Bybit, OKX, ... — institutional backing).
      - ``first_tx_pct`` — % of holders whose first-ever on-chain tx is this
        token (completely fresh wallets — no prior token history).

    Uses ``wallet_memory.evm_transfers`` for wallet age + funder lookups
    (the same tables as the other funding metrics). Best-effort: returns an
    ``unavailable`` result when ClickHouse has no holder/age data.

    Args:
        token_address: Token contract address.
        chain: Chain name (ethereum, bsc, arbitrum, ...).
        holders: Top-N holder addresses. When omitted, resolved from
            ``evm_transfers`` net balances.
        force_fresh: Bypass all caches.

    Returns:
        {
            "check": "wallet_metrics",
            "result": "scanned" | "unavailable" | "error",
            "source": "own",
            "token_address": "0x...",
            "chain": "ethereum",
            "holders_analyzed": 25,
            "fresh_wallet_pct": 12.3,
            "cex_funded_pct": 45.6,
            "first_tx_pct": 8.0,
            "fresh_wallet_count": 3,
            "cex_funded_count": 11,
            "first_tx_count": 2,
            "holder_details": [...],
            "note": "...",
        }
    """
    token_address = token_address.lower().strip()
    chain_norm = _normalize_chain(chain)
    _start = time.monotonic()

    if not token_address or not chain_norm:
        return _wallet_metrics_error(token_address, chain_norm, "token_address and chain are required")

    cache_key = f"{REDIS_CACHE_PREFIX}:metrics:{chain_norm}:{token_address}"
    if not force_fresh:
        cached = await _redis_get(cache_key)
        if cached is not None:
            logger.debug("wallet_metrics_redis_hit", key=cache_key)
            return cast("dict[str, Any]", cached)
    if not force_fresh and cache_key in _CACHE:
        entry = _CACHE[cache_key]
        if time.time() - entry.get("_cached_at", 0) < _CACHE_TTL:
            return {k: v for k, v in entry.items() if not k.startswith("_")}

    try:
        result = await _compute_wallet_metrics(token_address, chain_norm, holders)
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("wallet_metrics_query_failed: %s", exc)
        result = _wallet_metrics_unavailable(token_address, chain_norm, reason=str(exc)[:120])

    if result.get("result") == "scanned":
        await _redis_set(cache_key, result, REDIS_TTL_SECONDS)
        result["_cached_at"] = time.time()
        _CACHE[cache_key] = result
        if len(_CACHE) > _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)), None)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "wallet_metrics_latency",
        ms=round(_elapsed_ms, 1),
        token=token_address[:16],
        chain=chain_norm,
        holders=result.get("holders_analyzed", 0),
        fresh_pct=result.get("fresh_wallet_pct", 0.0),
        cex_pct=result.get("cex_funded_pct", 0.0),
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# COMPUTATION — Metric 1
# ═══════════════════════════════════════════════════════════════════════════


async def _compute_funding(token_address: str, chain: str, holders: list[str] | None) -> dict[str, Any]:
    """Resolve holders, trace funders, classify, and aggregate percentages."""
    # ── 1. Resolve top holders ──
    holder_addrs: list[str] = []
    if holders:
        holder_addrs = [h.lower().strip() for h in holders if h and h.lower().strip() not in (_ZERO_ADDR, _DEAD_ADDR)]
        holder_addrs = list(dict.fromkeys(holder_addrs))[:_MAX_HOLDERS]

    if not holder_addrs:
        holder_rows = await run_sync(_query_top_holders, token_address, chain, _MAX_HOLDERS)
        if holder_rows:
            holder_addrs = [str(r[0] or "").lower() for r in holder_rows if r and r[0]]
            holder_addrs = [h for h in holder_addrs if h not in (_ZERO_ADDR, _DEAD_ADDR)][:_MAX_HOLDERS]

    if not holder_addrs:
        return _unavailable_result(token_address, chain, reason="no holder data")

    # ── 2. First incoming transfer per holder (funding source) ──
    first_in_rows = await run_sync(_query_first_funders, holder_addrs, chain)
    funder_by_holder: dict[str, dict[str, Any]] = {}
    for row in first_in_rows or []:
        h_addr = str(row[0] or "").lower()
        if not h_addr:
            continue
        funder_by_holder[h_addr] = {
            "funder": str(row[1] or "").lower() or None,
            "first_tx": str(row[2] or ""),
            "first_ts": row[3],
        }

    funders = sorted({info["funder"] for info in funder_by_holder.values() if info.get("funder")})

    # ── 3. Funder labels (wallet_labels + static known_entities) ──
    labels_map: dict[str, list[dict[str, Any]]] = {}
    if funders:
        label_rows = await run_sync(_query_batch_labels, funders)
        for row in label_rows or []:
            addr = str(row[0] or "").lower()
            if not addr:
                continue
            labels_map.setdefault(addr, []).append(
                {
                    "label_name": row[1],
                    "label_category": row[2],
                    "label_subtype": row[3],
                    "source": row[4],
                    "is_sanctioned": bool(row[5]) if len(row) > 5 else False,
                }
            )

    # ── 4. Funder total tx counts (<=10 txs => fresh wallet) ──
    tx_count_by_funder: dict[str, int] = {}
    if funders:
        tx_rows = await run_sync(_query_funder_tx_counts, funders, chain)
        for row in tx_rows or []:
            addr = str(row[0] or "").lower()
            if addr:
                tx_count_by_funder[addr] = int(row[1] or 0)

    # ── 5. Classify each holder ──
    holder_details: list[dict[str, Any]] = []
    funding_type_counts: dict[str, int] = {"exchange_funded": 0, "fresh_wallet": 0, "mixer_funded": 0, "other": 0}
    for h_addr in holder_addrs:
        info = funder_by_holder.get(h_addr, {})
        funder = info.get("funder")
        if not funder:
            funding_type = "other"
            funder_label: str | None = None
        else:
            funding_type, funder_label = _classify_funder(
                funder,
                labels_map.get(funder, []),
                tx_count_by_funder.get(funder, 0),
                chain,
            )
        funding_type_counts[funding_type] += 1
        holder_details.append(
            {
                "holder": h_addr,
                "funder": funder,
                "funder_label": funder_label,
                "funding_type": funding_type,
                "first_funding_tx": info.get("first_tx"),
                "first_funding_ts": to_iso(info.get("first_ts")),
            }
        )

    analyzed = len(holder_addrs)
    exchange_pct = round(funding_type_counts["exchange_funded"] / analyzed * 100, 1) if analyzed else 0.0
    fresh_pct = round(funding_type_counts["fresh_wallet"] / analyzed * 100, 1) if analyzed else 0.0
    mixer_pct = round(funding_type_counts["mixer_funded"] / analyzed * 100, 1) if analyzed else 0.0
    other_pct = round(100.0 - exchange_pct - fresh_pct - mixer_pct, 1)

    risk = _assess_funding_risk(exchange_pct, fresh_pct, mixer_pct)
    top_funders = _build_top_funders(holder_details, labels_map, chain)

    return {
        "check": "funding_sources",
        "result": "scanned",
        "source": "own",
        "token_address": token_address,
        "chain": chain,
        "holders_analyzed": analyzed,
        "exchange_funded_pct": exchange_pct,
        "fresh_wallet_pct": fresh_pct,
        "mixer_funded_pct": mixer_pct,
        "unknown_pct": other_pct,
        "top_funders": top_funders,
        "holder_details": holder_details,
        "risk_assessment": risk,
        "risk_description": _RISK_DESCRIPTIONS[risk],
        "note": "first incoming transfer per holder classified by funder label/tx-count",
    }


# ═══════════════════════════════════════════════════════════════════════════
# COMPUTATION — Metric 4 (token-level wallet freshness metrics)
# ═══════════════════════════════════════════════════════════════════════════


async def _compute_wallet_metrics(
    token_address: str,
    chain: str,
    holders: list[str] | None,
) -> dict[str, Any]:
    """Resolve holders, fetch wallet age + first funder, aggregate metrics."""
    # ── 1. Resolve top holders ──
    holder_addrs: list[str] = []
    if holders:
        holder_addrs = [h.lower().strip() for h in holders if h and h.lower().strip() not in (_ZERO_ADDR, _DEAD_ADDR)]
        holder_addrs = list(dict.fromkeys(holder_addrs))[:_MAX_HOLDERS]

    if not holder_addrs:
        holder_rows = await run_sync(_query_top_holders, token_address, chain, _MAX_HOLDERS)
        if holder_rows:
            holder_addrs = [str(r[0] or "").lower() for r in holder_rows if r and r[0]]
            holder_addrs = [h for h in holder_addrs if h not in (_ZERO_ADDR, _DEAD_ADDR)][:_MAX_HOLDERS]

    if not holder_addrs:
        return _wallet_metrics_unavailable(token_address, chain, reason="no holder data")

    # ── 2. First-ever on-chain tx per holder (wallet age + first-tx token) ──
    first_tx_rows = await run_sync(_query_holder_first_tx, holder_addrs, chain)
    first_tx_by_holder: dict[str, dict[str, Any]] = {}
    for row in first_tx_rows or []:
        addr = str(row[0] or "").lower()
        if not addr:
            continue
        first_tx_by_holder[addr] = {
            "first_ts": row[1],
            "first_token": str(row[2] or "").lower() or None,
            "first_tx_hash": str(row[3] or "") or None,
        }

    # Without wallet-age data we cannot compute freshness — degrade gracefully.
    if not first_tx_by_holder:
        return _wallet_metrics_unavailable(token_address, chain, reason="no wallet age data")

    # ── 3. First funder per holder (CEX funding source) ──
    first_in_rows = await run_sync(_query_first_funders, holder_addrs, chain)
    funder_by_holder: dict[str, dict[str, Any]] = {}
    for row in first_in_rows or []:
        addr = str(row[0] or "").lower()
        if not addr:
            continue
        funder_by_holder[addr] = {
            "funder": str(row[1] or "").lower() or None,
            "first_tx": str(row[2] or ""),
            "first_ts": row[3],
        }

    # ── 4. Funder labels + tx counts (CEX / mixer / fresh classification) ──
    funders = sorted({info["funder"] for info in funder_by_holder.values() if info.get("funder")})
    labels_map: dict[str, list[dict[str, Any]]] = {}
    if funders:
        label_rows = await run_sync(_query_batch_labels, funders)
        for row in label_rows or []:
            addr = str(row[0] or "").lower()
            if not addr:
                continue
            labels_map.setdefault(addr, []).append(
                {
                    "label_name": row[1],
                    "label_category": row[2],
                    "label_subtype": row[3],
                    "source": row[4],
                    "is_sanctioned": bool(row[5]) if len(row) > 5 else False,
                }
            )

    tx_count_by_funder: dict[str, int] = {}
    if funders:
        tx_rows = await run_sync(_query_funder_tx_counts, funders, chain)
        for row in tx_rows or []:
            addr = str(row[0] or "").lower()
            if addr:
                tx_count_by_funder[addr] = int(row[1] or 0)

    # ── 5. Per-holder detail + aggregation ──
    holder_details: list[dict[str, Any]] = []
    now_ts = time.time()
    for h_addr in holder_addrs:
        fh = first_tx_by_holder.get(h_addr, {})
        fi = funder_by_holder.get(h_addr, {})
        funder = fi.get("funder")
        if funder:
            funding_type, funder_label = _classify_funder(
                funder,
                labels_map.get(funder, []),
                tx_count_by_funder.get(funder, 0),
                chain,
            )
        else:
            funding_type, funder_label = "other", None

        first_ts = to_epoch(fh.get("first_ts"))
        age_days = max(0.0, (now_ts - first_ts) / 86400.0) if first_ts is not None else 0.0

        holder_details.append(
            {
                "holder": h_addr,
                "age_days": round(age_days, 1),
                "fresh": age_days < _FRESH_WALLET_AGE_DAYS,
                "first_seen_at": to_iso(first_ts) if first_ts is not None else None,
                "first_tx_token": fh.get("first_token"),
                "first_tx_is_token": bool(fh.get("first_token") and fh["first_token"] == token_address),
                "funder": funder,
                "funder_label": funder_label,
                "funding_type": funding_type,
            }
        )

    return _aggregate_wallet_metrics(holder_details, token_address, chain)


def _aggregate_wallet_metrics(
    holder_details: list[dict[str, Any]],
    token_address: str,
    chain: str,
) -> dict[str, Any]:
    """Aggregate per-holder freshness details into token-level percentages.

    Pure logic (unit-testable without ClickHouse):
      - ``fresh_wallet_pct`` — holders with wallet age < 30 days
      - ``cex_funded_pct`` — holders whose first funder is a CEX wallet
      - ``first_tx_pct`` — holders whose first-ever on-chain tx is this token
    """
    analyzed = len(holder_details)
    if not analyzed:
        return _wallet_metrics_unavailable(token_address, chain, reason="no holder data")

    fresh_count = sum(1 for d in holder_details if d.get("fresh"))
    cex_count = sum(1 for d in holder_details if d.get("funding_type") == "exchange_funded")
    first_tx_count = sum(1 for d in holder_details if d.get("first_tx_is_token"))

    return {
        "check": "wallet_metrics",
        "result": "scanned",
        "source": "own",
        "token_address": token_address,
        "chain": chain,
        "holders_analyzed": analyzed,
        "fresh_wallet_pct": round(fresh_count / analyzed * 100, 1),
        "cex_funded_pct": round(cex_count / analyzed * 100, 1),
        "first_tx_pct": round(first_tx_count / analyzed * 100, 1),
        "fresh_wallet_count": fresh_count,
        "cex_funded_count": cex_count,
        "first_tx_count": first_tx_count,
        "holder_details": holder_details,
        "note": (
            "fresh_wallet_pct = holders with wallet <30 days old; "
            "cex_funded_pct = holders whose first funder is a CEX wallet; "
            "first_tx_pct = holders whose first-ever on-chain tx is this token"
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# COMPUTATION — Metric 2
# ═══════════════════════════════════════════════════════════════════════════


async def _compute_freshness(address: str, chain: str) -> dict[str, Any]:
    """Query wallet stats + first funder, then score freshness."""
    stats_rows = await run_sync(_query_wallet_stats, address, chain)
    funder_row = await run_sync(_query_wallet_first_funder, address, chain)

    first_seen_ts: float | None = None
    total_txs = 0
    if stats_rows:
        row = stats_rows[0]
        first_seen_ts = to_epoch(row[0]) if len(row) > 0 else None
        total_txs = int(row[1] or 0) if len(row) > 1 else 0

    if first_seen_ts is None and total_txs == 0:
        return _freshness_unavailable(address, chain, reason="no on-chain activity found")

    funder: str | None = None
    first_funder_label: str | None = None
    if funder_row:
        funder = str(funder_row[0] or "").lower() or None
        if funder:
            label_rows = await run_sync(_query_batch_labels, [funder])
            labels: list[dict[str, Any]] = []
            for row in label_rows or []:
                addr = str(row[0] or "").lower()
                if addr == funder:
                    labels.append(
                        {
                            "label_name": row[1],
                            "label_category": row[2],
                            "label_subtype": row[3],
                            "source": row[4],
                            "is_sanctioned": bool(row[5]) if len(row) > 5 else False,
                        }
                    )
            funder_type, first_funder_label = _classify_funder(funder, labels, total_txs, chain)
            funded_by_cex = funder_type == "exchange_funded"
        else:
            funded_by_cex = False
    else:
        funded_by_cex = False

    age_days = 0.0
    if first_seen_ts is not None:
        age_days = max(0.0, (time.time() - first_seen_ts) / 86400.0)

    score = _compute_freshness_score(age_days, total_txs, funded_by_cex)
    classification = _classify_freshness(score)

    return {
        "check": "wallet_freshness",
        "result": "scanned",
        "source": "own",
        "address": address,
        "chain": chain,
        "age_days": round(age_days, 1),
        "total_txs": total_txs,
        "funded_by_cex": funded_by_cex,
        "first_funder": funder,
        "first_funder_label": first_funder_label,
        "first_seen_at": to_iso(first_seen_ts) if first_seen_ts is not None else None,
        "freshness_score": score,
        "classification": classification,
        "note": "age, tx count, and CEX funding weighted into a 0-100 freshness score",
    }


# ═══════════════════════════════════════════════════════════════════════════
# COMPUTATION — Metric 3
# ═══════════════════════════════════════════════════════════════════════════


async def _compute_overlap(token_a: str, token_b: str, chain: str) -> dict[str, Any]:
    """Compute holder-set overlap between two tokens."""
    holders_a_task = asyncio.create_task(run_sync(_query_holder_set, token_a, chain))
    holders_b_task = asyncio.create_task(run_sync(_query_holder_set, token_b, chain))
    scam_task = asyncio.create_task(run_sync(_query_token_scam_status, token_b, chain))

    holders_a_rows, holders_b_rows, token_b_scam = await asyncio.gather(
        holders_a_task,
        holders_b_task,
        scam_task,
    )

    holders_a = {str(r[0] or "").lower() for r in holders_a_rows or [] if r and r[0]}
    holders_b = {str(r[0] or "").lower() for r in holders_b_rows or [] if r and r[0]}

    if not holders_a:
        return _overlap_unavailable(token_a, token_b, chain, reason="no holder data for token_a")

    common = holders_a & holders_b
    overlap_pct = round(len(common) / len(holders_a) * 100, 1)

    risk = (
        "HIGH"
        if (token_b_scam and overlap_pct > _OVERLAP_SCAM_PCT)
        else ("MEDIUM" if overlap_pct > _OVERLAP_SCAM_PCT else "LOW")
    )

    return {
        "check": "holder_overlap",
        "result": "scanned",
        "source": "own",
        "token_a": token_a,
        "token_b": token_b,
        "chain": chain,
        "holders_a": len(holders_a),
        "holders_b": len(holders_b),
        "common_holders": len(common),
        "overlap_pct": overlap_pct,
        "token_b_is_scam": bool(token_b_scam),
        "risk_assessment": risk,
        "note": "overlap = |holders_A ∩ holders_B| / |holders_A|; >30% with known scam = red flag",
    }


# ═══════════════════════════════════════════════════════════════════════════
# CLICKHOUSE QUERIES (all sync, bounded by max_execution_time)
# ═══════════════════════════════════════════════════════════════════════════


def _query_top_holders(token_address: str, chain: str, limit: int) -> list[tuple[Any, ...]] | None:
    """Top-N holder addresses from evm_transfers (EVM) or solana balance changes."""
    client = _get_ch_client()
    if client is None:
        return None
    try:
        if chain == "solana":
            return cast(
                "list[tuple[Any, ...]] | None",
                client.execute(
                    """
                SELECT if(owner != '', owner, token_account) AS holder, sum(toFloat64OrZero(delta)) AS balance
                FROM wallet_memory.solana_spl_balance_changes
                WHERE mint = %(token)s
                GROUP BY holder
                HAVING balance > 0
                ORDER BY balance DESC
                LIMIT %(limit)s
                """,
                    {"token": token_address, "limit": limit},
                    settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
                ),
            )
        return cast(
            "list[tuple[Any, ...]] | None",
            client.execute(
                """
            SELECT addr, balance FROM (
                SELECT
                    addr,
                    sum(net) AS balance
                FROM (
                    SELECT to_address AS addr, toFloat64OrZero(value) AS net
                    FROM wallet_memory.evm_transfers
                    WHERE token_address = %(token)s
                      AND chain = %(chain)s
                      AND to_address NOT IN ('', '0x0000000000000000000000000000000000000000')
                    UNION ALL
                    SELECT from_address AS addr, -toFloat64OrZero(value) AS net
                    FROM wallet_memory.evm_transfers
                    WHERE token_address = %(token)s
                      AND chain = %(chain)s
                      AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000')
                )
                WHERE addr != ''
                GROUP BY addr
                HAVING sum(net) > 0
            )
            ORDER BY balance DESC
            LIMIT %(limit)s
            """,
                {"token": token_address, "chain": chain, "limit": limit},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            ),
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("funding_top_holders_query_failed: %s", exc)
        return None


def _query_first_funders(holders: list[str], chain: str) -> list[tuple[Any, ...]] | None:
    """First incoming transfer per holder across ALL tokens (the funding event)."""
    client = _get_ch_client()
    if client is None or not holders:
        return None
    try:
        return cast(
            "list[tuple[Any, ...]] | None",
            client.execute(
                """
            SELECT
                to_address AS holder,
                argMin(from_address, block_timestamp) AS funder,
                argMin(tx_hash, block_timestamp) AS first_tx,
                min(block_timestamp) AS first_ts
            FROM wallet_memory.evm_transfers
            WHERE chain = %(chain)s
              AND to_address IN %(holders)s
              AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                          '0x000000000000000000000000000000000000dead')
            GROUP BY to_address
            """,
                {"holders": tuple(holders), "chain": chain},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            ),
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("funding_first_funders_query_failed: %s", exc)
        return None


def _query_funder_tx_counts(funders: list[str], chain: str) -> list[tuple[Any, ...]] | None:
    """Total on-chain transfers per funder (in + out) — drives fresh-wallet detection."""
    client = _get_ch_client()
    if client is None or not funders:
        return None
    try:
        return cast(
            "list[tuple[Any, ...]] | None",
            client.execute(
                """
            SELECT addr, sum(cnt) AS total
            FROM (
                SELECT from_address AS addr, count() AS cnt
                FROM wallet_memory.evm_transfers
                WHERE chain = %(chain)s AND from_address IN %(funders)s
                GROUP BY from_address
                UNION ALL
                SELECT to_address AS addr, count() AS cnt
                FROM wallet_memory.evm_transfers
                WHERE chain = %(chain)s AND to_address IN %(funders)s
                GROUP BY to_address
            )
            GROUP BY addr
            """,
                {"funders": tuple(funders), "chain": chain},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            ),
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("funding_funder_tx_counts_failed: %s", exc)
        return None


def _query_wallet_stats(address: str, chain: str) -> list[tuple[Any, ...]] | None:
    """First-seen timestamp and total transfer count for a wallet."""
    client = _get_ch_client()
    if client is None:
        return None
    try:
        return cast(
            "list[tuple[Any, ...]] | None",
            client.execute(
                """
            SELECT min(block_timestamp) AS first_seen, count() AS total
            FROM wallet_memory.evm_transfers
            WHERE chain = %(chain)s
              AND (from_address = %(addr)s OR to_address = %(addr)s)
              AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000')
              AND to_address NOT IN ('', '0x0000000000000000000000000000000000000000')
            """,
                {"addr": address, "chain": chain},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            ),
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("wallet_stats_query_failed: %s", exc)
        return None


def _query_wallet_first_funder(address: str, chain: str) -> list[tuple[Any, ...]] | None:
    """The wallet's first-ever incoming funder (any token on chain)."""
    client = _get_ch_client()
    if client is None:
        return None
    try:
        return cast(
            "list[tuple[Any, ...]] | None",
            client.execute(
                """
            SELECT argMin(from_address, block_timestamp) AS funder
            FROM wallet_memory.evm_transfers
            WHERE chain = %(chain)s
              AND to_address = %(addr)s
              AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                          '0x000000000000000000000000000000000000dead')
            """,
                {"addr": address, "chain": chain},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            ),
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("wallet_first_funder_query_failed: %s", exc)
        return None


def _query_holder_first_tx(holders: list[str], chain: str) -> list[tuple[Any, ...]] | None:
    """First-ever on-chain transfer per holder (any token, in or out).

    Returns rows of ``(holder, first_ts, first_token, first_tx_hash)``. The
    ``first_token`` column drives ``first_tx_pct`` (holders whose first-ever
    tx is the scanned token) and ``first_ts`` drives the <30-day wallet-age
    metric.
    """
    client = _get_ch_client()
    if client is None or not holders:
        return None
    try:
        return cast(
            "list[tuple[Any, ...]] | None",
            client.execute(
                """
            SELECT
                addr,
                argMin(block_timestamp, block_timestamp) AS first_ts,
                argMin(token_address, block_timestamp) AS first_token,
                argMin(tx_hash, block_timestamp) AS first_tx_hash
            FROM (
                SELECT to_address AS addr, block_timestamp, token_address, tx_hash
                FROM wallet_memory.evm_transfers
                WHERE chain = %(chain)s
                  AND to_address IN %(holders)s
                  AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                              '0x000000000000000000000000000000000000dead')
                UNION ALL
                SELECT from_address AS addr, block_timestamp, token_address, tx_hash
                FROM wallet_memory.evm_transfers
                WHERE chain = %(chain)s
                  AND from_address IN %(holders)s
                  AND to_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                            '0x000000000000000000000000000000000000dead')
            )
            GROUP BY addr
            """,
                {"holders": tuple(holders), "chain": chain},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            ),
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("holder_first_tx_query_failed: %s", exc)
        return None


def _query_holder_set(token_address: str, chain: str, limit: int = 500) -> list[tuple[Any, ...]] | None:
    """Set of holder addresses for a token (net balance > 0), capped at ``limit``."""
    client = _get_ch_client()
    if client is None:
        return None
    try:
        if chain == "solana":
            return cast(
                "list[tuple[Any, ...]] | None",
                client.execute(
                    """
                SELECT DISTINCT if(owner != '', owner, token_account) AS holder
                FROM wallet_memory.solana_spl_balance_changes
                WHERE mint = %(token)s
                  AND delta > 0
                LIMIT %(limit)s
                """,
                    {"token": token_address, "limit": limit},
                    settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
                ),
            )
        return cast(
            "list[tuple[Any, ...]] | None",
            client.execute(
                """
            SELECT DISTINCT to_address AS holder
            FROM wallet_memory.evm_transfers
            WHERE token_address = %(token)s
              AND chain = %(chain)s
              AND to_address NOT IN ('', '0x0000000000000000000000000000000000000000')
            LIMIT %(limit)s
            """,
                {"token": token_address, "chain": chain, "limit": limit},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            ),
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("holder_set_query_failed: %s", exc)
        return None


def _query_known_scam_tokens(chain: str, limit: int) -> list[str]:
    """Recent tokens flagged scam-related from deployer_history."""
    client = _get_ch_client()
    if client is None:
        return []
    try:
        rows = client.execute(
            """
            SELECT token_address
            FROM wallet_memory.deployer_history
            WHERE chain_id = %(chain)s
              AND is_scam_related = 1
              AND token_address != ''
            ORDER BY deployed_at DESC
            LIMIT %(limit)s
            """,
            {"chain": chain, "limit": limit},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return [str(r[0] or "").lower() for r in rows if r and r[0]]
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("known_scam_tokens_query_failed: %s", exc)
        return []


def _query_token_scam_status(token_address: str, chain: str) -> bool:
    """True when a token is flagged in scam_addresses or deployer_history."""
    client = _get_ch_client()
    if client is None:
        return False
    try:
        rows = client.execute(
            """
            SELECT 1
            FROM wallet_memory.deployer_history
            WHERE token_address = %(token)s
              AND chain_id = %(chain)s
              AND is_scam_related = 1
            LIMIT 1
            """,
            {"token": token_address, "chain": chain},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        if rows:
            return True
        rows = client.execute(
            """
            SELECT 1
            FROM wallet_memory.scam_addresses
            WHERE address = %(token)s
              AND chain_id = %(chain)s
            LIMIT 1
            """,
            {"token": token_address, "chain": chain},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return bool(rows)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("token_scam_status_query_failed: %s", exc)
        return False


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════


def _unavailable_result(token_address: str, chain_norm: str, reason: str = "clickhouse unavailable") -> dict[str, Any]:
    """Graceful degradation result (Metric 1)."""
    return {
        "check": "funding_sources",
        "result": "unavailable",
        "source": "own",
        "token_address": token_address,
        "chain": chain_norm,
        "holders_analyzed": 0,
        "exchange_funded_pct": 0.0,
        "fresh_wallet_pct": 0.0,
        "mixer_funded_pct": 0.0,
        "unknown_pct": 0.0,
        "top_funders": [],
        "holder_details": [],
        "risk_assessment": "LOW",
        "note": reason,
    }


def _error_result(token_address: str, chain_norm: str, message: str) -> dict[str, Any]:
    """Invalid-input error result (Metric 1)."""
    return {
        "check": "funding_sources",
        "result": "error",
        "error": message,
        "token_address": token_address,
        "chain": chain_norm,
        "holders_analyzed": 0,
        "exchange_funded_pct": 0.0,
        "fresh_wallet_pct": 0.0,
        "mixer_funded_pct": 0.0,
        "unknown_pct": 0.0,
        "top_funders": [],
        "holder_details": [],
        "risk_assessment": "LOW",
    }


def _freshness_unavailable(address: str, chain_norm: str, reason: str = "clickhouse unavailable") -> dict[str, Any]:
    """Graceful degradation result (Metric 2)."""
    return {
        "check": "wallet_freshness",
        "result": "unavailable",
        "source": "own",
        "address": address,
        "chain": chain_norm,
        "age_days": 0.0,
        "total_txs": 0,
        "funded_by_cex": False,
        "first_funder": None,
        "first_funder_label": None,
        "first_seen_at": None,
        "freshness_score": 0,
        "classification": "ESTABLISHED",
        "note": reason,
    }


def _overlap_unavailable(
    token_a: str, token_b: str, chain_norm: str, reason: str = "clickhouse unavailable"
) -> dict[str, Any]:
    """Graceful degradation result (Metric 3)."""
    return {
        "check": "holder_overlap",
        "result": "unavailable",
        "source": "own",
        "token_a": token_a,
        "token_b": token_b,
        "chain": chain_norm,
        "holders_a": 0,
        "holders_b": 0,
        "common_holders": 0,
        "overlap_pct": 0.0,
        "token_b_is_scam": False,
        "risk_assessment": "LOW",
        "note": reason,
    }


def _overlap_error(token_a: str, token_b: str, chain_norm: str, message: str) -> dict[str, Any]:
    """Invalid-input error result (Metric 3)."""
    return {
        "check": "holder_overlap",
        "result": "error",
        "error": message,
        "token_a": token_a,
        "token_b": token_b,
        "chain": chain_norm,
        "holders_a": 0,
        "holders_b": 0,
        "common_holders": 0,
        "overlap_pct": 0.0,
        "token_b_is_scam": False,
        "risk_assessment": "LOW",
    }


def _scam_overlap_error(token_address: str, chain_norm: str, message: str) -> dict[str, Any]:
    """Invalid-input error result (scanner helper)."""
    return {
        "check": "scam_overlap",
        "result": "error",
        "error": message,
        "token_address": token_address,
        "chain": chain_norm,
        "scam_tokens_checked": 0,
        "max_overlap_pct": 0.0,
        "worst_token": None,
        "worst_token_is_scam": False,
        "risk_assessment": "LOW",
        "comparisons": [],
    }


def _wallet_metrics_unavailable(
    token_address: str,
    chain_norm: str,
    reason: str = "clickhouse unavailable",
) -> dict[str, Any]:
    """Graceful degradation result (Metric 4)."""
    return {
        "check": "wallet_metrics",
        "result": "unavailable",
        "source": "own",
        "token_address": token_address,
        "chain": chain_norm,
        "holders_analyzed": 0,
        "fresh_wallet_pct": 0.0,
        "cex_funded_pct": 0.0,
        "first_tx_pct": 0.0,
        "fresh_wallet_count": 0,
        "cex_funded_count": 0,
        "first_tx_count": 0,
        "holder_details": [],
        "note": reason,
    }


def _wallet_metrics_error(token_address: str, chain_norm: str, message: str) -> dict[str, Any]:
    """Invalid-input error result (Metric 4)."""
    return {
        "check": "wallet_metrics",
        "result": "error",
        "error": message,
        "token_address": token_address,
        "chain": chain_norm,
        "holders_analyzed": 0,
        "fresh_wallet_pct": 0.0,
        "cex_funded_pct": 0.0,
        "first_tx_pct": 0.0,
        "fresh_wallet_count": 0,
        "cex_funded_count": 0,
        "first_tx_count": 0,
        "holder_details": [],
    }


# ═══════════════════════════════════════════════════════════════════════════
# MONITORING
# ═══════════════════════════════════════════════════════════════════════════


def get_cache_stats() -> dict[str, Any]:
    """Return cache statistics for monitoring."""
    return _common_get_cache_stats(
        _CACHE,
        _CACHE_MAX,
        _CACHE_TTL,
        extra={
            "redis_ttl_seconds": REDIS_TTL_SECONDS,
            "clickhouse_connected": _get_ch_client() is not None,
        },
    )


def clear_cache() -> None:
    """Clear the in-process cache (for testing)."""
    _common_clear_cache(_CACHE)
