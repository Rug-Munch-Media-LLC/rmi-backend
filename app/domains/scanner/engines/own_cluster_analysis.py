"""
Own Cluster Analysis — Funding-Source Clusters + Coordinated-Buy Detection
===========================================================================
Upgrades CLUSTER detection beyond simple bundle wallet counts. Two signals
that are stronger than a raw count:

  * FUNDING-SOURCE CLUSTER — first (launch-window) buyers are grouped by
    their FIRST FUNDER (the wallet that funded them before they bought).
    One funder \u2192 N wallets is a stronger coordinated signal than a simple
    bundle count, because it shows the wallets were armed by one operator.
  * COORDINATED-BUY detection — first-buyers who buy in the SAME BLOCK and
    share a funder (or a funding chain, where the funder is itself a launch
    buyer) get a ``coordinated`` verdict; otherwise ``organic``.

Data sources (reused — no new services):
  - wallet_memory.evm_transfers + deployer_events via
    ``_own_bundle_queries._query_launch_cluster`` (first-buyers + block),
  - first-funder lookup via ``_funding_queries._query_first_funders``
    (argMin incoming transfer across ALL tokens — the wallet that funded
    each buyer before they bought).

Fail-open by design: returns an ``unavailable`` payload with
``cluster_line: "\u2014"`` when ClickHouse has no launch-buyer / funding data.
Never fabricates clusters for missing data.

Usage:
    from app.domains.scanner.engines.own_cluster_analysis import get_own_cluster_analysis

    result = await get_own_cluster_analysis("0x...", "ethereum")
    # result = {
    #     "check": "cluster_analysis",
    #     "result": "scanned" | "unavailable",
    #     "source": "own",
    #     "cluster_line": "🕸️ 3 funders → 14 wallets · largest cluster 9 · coordinated",
    #     "distinct_funders": int,
    #     "funded_wallets": int,
    #     "largest_cluster_size": int,
    #     "clusters": [{"funder": str, "wallet_count": int, "wallets": [str]}],
    #     "coordinated": bool,
    #     "coordinated_buyers": int,
    #     "funding_chain_buyers": int,
    #     "verdict": "coordinated" | "organic" | "unknown",
    # }
"""

from __future__ import annotations

import time
from typing import Any

from app.core.logging import get_logger
from app.domains.scanner.engines._common import (
    clear_cache as _common_clear_cache,
    get_cache_stats as _common_get_cache_stats,
    run_sync,
)
from app.domains.scanner.engines._funding_queries import _query_first_funders
from app.domains.scanner.engines._own_bundle_common import _normalize_chain
from app.domains.scanner.engines._own_bundle_queries import _query_launch_cluster

logger = get_logger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────
_CLUSTER_CACHE: dict[str, dict[str, Any]] = {}
_CLUSTER_CACHE_TTL = 600  # 10 minutes — matches the bundle cache
_CLUSTER_CACHE_MAX = 100

#: Max funder clusters surfaced in the result (top-N by wallet count).
_MAX_CLUSTERS = 5


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def get_own_cluster_analysis(
    token_address: str,
    chain: str,
    deployer: str | None = None,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Funding-source clusters + coordinated-buy verdict for a token.

    Groups first (launch-window) buyers by their first funder and decides
    whether the early buying was coordinated (same block + shared funder)
    or organic. Best-effort: returns an ``unavailable`` payload with a
    ``cluster_line`` of ``"—"`` when ClickHouse has no launch-buyer or
    funding data. Never raises.

    Args:
        token_address: Token address (lowercased internally).
        chain: Chain name (ethereum / bsc / polygon / ...; solana has no
            evm_transfers funding rows, so it degrades to ``unavailable``).
        deployer: Optional deployer address — treated as a known non-funder
            (cluster results are unchanged; kept for signature symmetry).
        force_fresh: Bypass the in-process cache.

    Returns:
        Dict with ``cluster_line`` (compact 🕸️ summary) plus cluster detail
        and the coordinated / organic verdict. Fail-open to ``"—"`` when
        launch-buyer / funding data is not reachable.
    """
    token_address = (token_address or "").lower().strip()
    chain_norm = _normalize_chain(chain)
    _start = time.monotonic()

    cache_key = f"{token_address}:{chain_norm}"
    if not force_fresh and cache_key in _CLUSTER_CACHE:
        cached = _CLUSTER_CACHE[cache_key]
        if time.time() - cached.get("_cached_at", 0) < _CLUSTER_CACHE_TTL:
            return {k: v for k, v in cached.items() if not k.startswith("_")}

    result: dict[str, Any] = {
        "check": "cluster_analysis",
        "result": "unavailable",
        "source": "own",
        "cluster_line": "\u2014",  # —
        "distinct_funders": 0,
        "funded_wallets": 0,
        "largest_cluster_size": 0,
        "clusters": [],
        "coordinated": False,
        "coordinated_buyers": 0,
        "funding_chain_buyers": 0,
        "verdict": "unknown",
    }

    try:
        # ── 1. Launch-window first buyers (with their block numbers) ──
        launch = await run_sync(_query_launch_cluster, token_address, chain_norm)
        launch_buyers = []
        if isinstance(launch, dict):
            launch_buyers = [b for b in (launch.get("launch_buyers") or []) if isinstance(b, dict)]

        if not launch_buyers:
            result["note"] = (
                "[REVIEW] funding-source clustering needs launch-buyer rows "
                "(no ClickHouse evm_transfers coverage for this token/chain)"
            )
            _cache_cluster(cache_key, result)
            return result

        # ── 2. First funder per first-buyer (the wallet that funded them) ──
        buyer_addrs = list(dict.fromkeys(str(b.get("buyer", "") or "").lower() for b in launch_buyers))
        funder_rows = await run_sync(_query_first_funders, buyer_addrs, chain_norm)
        funder_map: dict[str, str] = {}
        for row in funder_rows or []:
            if len(row) >= 2 and row[0] and row[1]:
                funder_map[str(row[0]).lower()] = str(row[1]).lower()

        if not funder_map:
            result["note"] = (
                "[REVIEW] funding-source clustering needs first-funder rows "
                "(no ClickHouse evm_transfers funding coverage for this chain)"
            )
            _cache_cluster(cache_key, result)
            return result

        # ── 3. Build funding-source clusters + coordinated verdict ──
        clusters = _build_funding_clusters(launch_buyers, funder_map)
        coordinated, coordinated_buyers, chain_buyers = _detect_coordinated_buys(launch_buyers, funder_map)

        distinct_funders = len(clusters)
        funded_wallets = sum(c["wallet_count"] for c in clusters)
        largest = clusters[0]["wallet_count"] if clusters else 0
        verdict = "coordinated" if coordinated else "organic"

        result.update(
            {
                "result": "scanned",
                "cluster_line": _build_cluster_line(distinct_funders, funded_wallets, largest, verdict),
                "distinct_funders": distinct_funders,
                "funded_wallets": funded_wallets,
                "largest_cluster_size": largest,
                "clusters": clusters[:_MAX_CLUSTERS],
                "coordinated": coordinated,
                "coordinated_buyers": coordinated_buyers,
                "funding_chain_buyers": chain_buyers,
                "verdict": verdict,
            }
        )
    except Exception as exc:  # pragma: no cover - defensive; never break a scan
        logger.debug("cluster_analysis_failed for %s: %s", token_address[:16], exc)
        result["note"] = "[REVIEW] cluster analysis data source unreachable"

    _cache_cluster(cache_key, result)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "own_cluster_analysis_latency",
        ms=round(_elapsed_ms, 1),
        token=token_address[:16],
        chain=chain_norm,
        scanned=result["result"] == "scanned",
        verdict=result["verdict"],
        largest=result["largest_cluster_size"],
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# PURE CLUSTERING LOGIC (unit-testable without ClickHouse)
# ═══════════════════════════════════════════════════════════════════════════


def _build_funding_clusters(
    launch_buyers: list[dict[str, Any]],
    funder_map: dict[str, str],
) -> list[dict[str, Any]]:
    """Group first-buyers by their FIRST FUNDER.

    Returns clusters sorted by wallet count descending:
    ``[{"funder": str, "wallet_count": int, "wallets": [str]}, ...]``.
    Buyers with no known funder are omitted (they contribute nothing to a
    funding-source cluster).
    """
    groups: dict[str, list[str]] = {}
    for b in launch_buyers:
        buyer = str(b.get("buyer", "") or "").lower()
        funder = (funder_map.get(buyer) or "").lower()
        if not buyer or not funder:
            continue
        groups.setdefault(funder, []).append(buyer)

    clusters = [
        {"funder": funder, "wallet_count": len(wallets), "wallets": wallets} for funder, wallets in groups.items()
    ]
    clusters.sort(key=lambda c: (c["wallet_count"], c["funder"]), reverse=True)
    return clusters


def _detect_coordinated_buys(
    launch_buyers: list[dict[str, Any]],
    funder_map: dict[str, str],
) -> tuple[bool, int, int]:
    """Detect coordinated buys: same block + shared first funder.

    A coordinated pair is two first-buyers that BOTH:
      (a) bought in the same block, and
      (b) share the same first funder (the wallet that funded them).

    Also flags a weaker two-hop signal — a "funding chain" — where a
    buyer's first funder is itself a first-buyer of the token (the funder
    bought first, then armed other launch wallets).

    Returns ``(coordinated, coordinated_wallet_count, funding_chain_wallet_count)``.
    """
    by_block: dict[int, list[str]] = {}
    for b in launch_buyers:
        blk = int(b.get("block_number", 0) or 0)
        buyer = str(b.get("buyer", "") or "").lower()
        if blk and buyer:
            by_block.setdefault(blk, []).append(buyer)

    launch_buyer_set = {str(b.get("buyer", "") or "").lower() for b in launch_buyers}

    # ── Same-block + shared-funder wallets ──
    coordinated_wallets: set[str] = set()
    for _blk, buyers in by_block.items():
        if len(buyers) < 2:
            continue
        funder_groups: dict[str, list[str]] = {}
        for buyer in buyers:
            funder = (funder_map.get(buyer) or "").lower()
            if funder:
                funder_groups.setdefault(funder, []).append(buyer)
        for group in funder_groups.values():
            if len(group) >= 2:
                coordinated_wallets.update(group)

    coordinated = len(coordinated_wallets) >= 2

    # ── Funding-chain wallets: their first funder is itself a launch buyer ──
    funding_chain: set[str] = set()
    for buyer, funder in funder_map.items():
        f = (funder or "").lower()
        if f and f in launch_buyer_set and f != buyer:
            funding_chain.add(buyer)

    return coordinated, len(coordinated_wallets), len(funding_chain)


def _build_cluster_line(
    distinct_funders: int,
    funded_wallets: int,
    largest_cluster_size: int,
    verdict: str,
) -> str:
    """Compact 🕸️ summary, e.g. ``🕸️ 3 funders → 14 wallets · largest cluster 9 · coordinated``."""
    return (
        f"\U0001f578\ufe0f {distinct_funders} funders \u2192 {funded_wallets} wallets"
        f" \u00b7 largest cluster {largest_cluster_size} \u00b7 {verdict}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# CACHING
# ═══════════════════════════════════════════════════════════════════════════


def _cache_cluster(key: str, data: dict[str, Any]) -> None:
    data["_cached_at"] = time.time()
    _CLUSTER_CACHE[key] = data
    if len(_CLUSTER_CACHE) > _CLUSTER_CACHE_MAX:
        _CLUSTER_CACHE.pop(next(iter(_CLUSTER_CACHE)), None)


def get_cache_stats() -> dict[str, Any]:
    """Return cache statistics for monitoring."""
    return _common_get_cache_stats(
        _CLUSTER_CACHE,
        _CLUSTER_CACHE_MAX,
        _CLUSTER_CACHE_TTL,
        extra={"max_clusters": _MAX_CLUSTERS},
    )


def clear_cache() -> None:
    """Clear the in-process cache (for testing)."""
    _common_clear_cache(_CLUSTER_CACHE)
