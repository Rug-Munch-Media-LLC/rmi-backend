"""Bundled-sniper detection for the RugMunchBot scan (fail-open, injectable).

A *bundled sniper* signal is a strong rug flag: a token launch whose early
supply is concentrated in coordinated wallets (Jito-bundled launches, GMGN
sniper-holder flags, or same-block / first-block early-buy clusters).

Public API
----------
``detect_bundle(address, chain, *, bundle_lookup=None, holder_analysis=None,
early_buys=None) -> dict``
    Best-effort multi-source detection. Never raises: any error degrades to
    ``{"bundled": False, "bundle_pct": None, "sniper_count": None,
    "evidence": None}``. Fetchers are injectable so callers/tests can stub
    them without network or DB access.

``bundle_signal(result) -> str | None``
    Pure renderer: ``"bundled: 62% snipers"`` when ``bundle_pct`` is present,
    ``"sniper cluster"`` when only ``sniper_count`` is present, ``None`` when
    not bundled.

Detection sources, evaluated in priority order (first positive verdict wins):

(a) Direct bundle lookup — ``wallet_memory.solana_bundles`` (indexer
    ClickHouse, written by lite-chain-indexer's Jito-bundle strategy: same-slot
    token-create + fresh-wallet buys + tip transfer to a Jito tip account).
    The table stores ``buyer_count`` (fresh-wallet buyers, a lower bound on
    bundled snipers) but not a supply percentage, so ``bundle_pct`` stays
    ``None`` for this source. Default fetcher: ``_bundle_lookup_clickhouse``.

(b) GMGN holder-analysis sniper/bot flags — ``get_holder_analysis`` returns
    ``sniper_count`` (holders with <= 2 buy txs and realized profit) and
    ``insider_count``. ``sniper_count >= SNIPER_CLUSTER_MIN`` → sniper
    cluster. Default fetcher: ``_holder_analysis_gmgn``.

(c) Heuristic fallback — when neither source fires, early-buy records (maker +
    block) are clustered by first-buy block; if the most crowded block holds
    >= ``BUNDLE_PCT_THRESHOLD`` % of distinct holders (with at least
    ``MIN_BUYER_SAMPLE`` holders observed) the launch is flagged. Default
    fetcher: ``_early_buys_dexscreener`` (free, no key). This source yields
    both ``bundle_pct`` and ``sniper_count``.

Sub-threshold observations (e.g. GMGN ``sniper_count`` of 1, or a heuristic
cluster below the threshold) are still returned as metrics with
``bundled=False`` and no ``evidence``, so the lead can surface partial info.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Tunables ────────────────────────────────────────────────────────────
# Min GMGN sniper holders to flag a "sniper cluster" (bundled=True).
SNIPER_CLUSTER_MIN = 2
# Min % of distinct early holders sharing the most crowded first-buy block.
BUNDLE_PCT_THRESHOLD = 30.0
# Min distinct early holders for the heuristic to mean anything.
MIN_BUYER_SAMPLE = 5

# ── Fetcher type aliases (injectable for tests) ─────────────────────────
BundleLookupFetcher = Callable[[str, str], Awaitable[dict[str, Any] | None]]
HolderAnalysisFetcher = Callable[[str, str], Awaitable[dict[str, Any] | None]]
EarlyBuysFetcher = Callable[[str, str], Awaitable[list[dict[str, Any]] | None]]

# The normal-form result shape returned by detect_bundle.
_RESULT_KEYS = ("bundled", "bundle_pct", "sniper_count", "evidence")


def _clean_result(bundled: bool = False, **fields: Any) -> dict[str, Any]:
    """Build a normal-form result dict, dropping None fillers for unset fields."""
    result: dict[str, Any] = {
        "bundled": bundled,
        "bundle_pct": fields.get("bundle_pct"),
        "sniper_count": fields.get("sniper_count"),
        "evidence": fields.get("evidence"),
    }
    return result


# ── Source (a): direct solana_bundles lookup (indexer ClickHouse) ───────


async def _bundle_lookup_clickhouse(address: str, chain: str) -> dict[str, Any] | None:
    """Look up ``wallet_memory.solana_bundles`` for the mint == *address*.

    The table only exists for Solana (lite-chain-indexer Jito-bundle
    strategy); any other chain returns None. Best-effort: ClickHouse
    unavailability or a missing row degrades to None, never an exception.

    Columns: chain, slot, block_timestamp, create_tx_signature,
    tip_tx_signature, tip_account, mint, buyer_count, buyer_wallets.
    """
    if chain.lower() != "solana":
        return None
    try:
        from app.domains.scanner.engines._own_bundle_common import _get_ch_client

        client = _get_ch_client()
        if client is None:
            return None
        rows = client.execute(
            "SELECT chain, slot, block_timestamp, create_tx_signature, "
            "tip_tx_signature, tip_account, mint, buyer_count, buyer_wallets "
            "FROM wallet_memory.solana_bundles "
            "WHERE mint = %(mint)s "
            "ORDER BY block_timestamp DESC "
            "LIMIT 1",
            {"mint": address},
            with_names=True,
        )
    except Exception as exc:
        logger.debug("bundle_lookup_clickhouse_failed", error=str(exc)[:120])
        return None

    if not rows:
        return None
    row = rows[0]
    # clickhouse_driver returns dicts with with_names=True; tolerate tuples.
    if isinstance(row, dict):
        return row
    keys = (
        "chain",
        "slot",
        "block_timestamp",
        "create_tx_signature",
        "tip_tx_signature",
        "tip_account",
        "mint",
        "buyer_count",
        "buyer_wallets",
    )
    return dict(zip(keys, row, strict=False))


def _bundle_lookup_signal(row: dict[str, Any]) -> dict[str, Any]:
    """Verdict for source (a): a solana_bundles row proves a Jito bundle."""
    try:
        buyer_count = int(row.get("buyer_count") or 0)
    except (TypeError, ValueError):
        buyer_count = 0
    slot = row.get("slot") or 0
    return _clean_result(
        bundled=True,
        bundle_pct=None,  # table stores buyer count, not supply %
        sniper_count=buyer_count or None,
        evidence=(f"solana_bundles: Jito bundle at slot {slot} ({buyer_count} fresh-wallet buyers)"),
    )


# ── Source (b): GMGN holder-analysis sniper/bot flags ────────────────────


async def _holder_analysis_gmgn(address: str, chain: str) -> dict[str, Any] | None:
    """Fetch holder analysis via the GMGN provider (sniper/bot flags)."""
    try:
        from app.databus.gmgn_provider import get_gmgn_provider

        provider = get_gmgn_provider()
        result = await provider.get_holder_analysis(address, chain)
        return result or None
    except Exception as exc:
        logger.debug("holder_analysis_gmgn_failed", error=str(exc)[:120])
        return None


def _gmgn_signal(holders: dict[str, Any]) -> dict[str, Any]:
    """Verdict for source (b): GMGN sniper/bot holder flags.

    ``sniper_count`` is GMGN's own definition (holders with <= 2 buy txs and
    realized profit). At or above ``SNIPER_CLUSTER_MIN`` → sniper cluster.
    Below threshold the observed count is still reported (bundled=False).
    """
    try:
        sniper_count = int(holders.get("sniper_count") or 0)
    except (TypeError, ValueError):
        sniper_count = 0
    total = holders.get("total_holders")
    if sniper_count >= SNIPER_CLUSTER_MIN:
        total_txt = f" of {total}" if total else ""
        return _clean_result(
            bundled=True,
            bundle_pct=None,
            sniper_count=sniper_count,
            evidence=f"gmgn holder analysis: {sniper_count} sniper holders{total_txt}",
        )
    return _clean_result(bundled=False, sniper_count=sniper_count or None)


# ── Source (c): heuristic — same-block / first-block early-buy cluster ───


async def _early_buys_dexscreener(address: str, chain: str) -> list[dict[str, Any]] | None:
    """Fetch recent trades from DexScreener (free, no key) with block info.

    Each returned record: ``{"maker": wallet, "block": int|None,
    "timestamp": ...}``. Fail-open: network errors / non-200 → None.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"https://api.dexscreener.com/tokens/v1/{chain}/{address}/trades")
            if resp.status_code != 200:
                return None
            trades = (resp.json() or {}).get("trades") or []
    except Exception as exc:
        logger.debug("early_buys_dexscreener_failed", error=str(exc)[:120])
        return None

    records: list[dict[str, Any]] = []
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        records.append(
            {
                "maker": trade.get("maker") or "",
                "block": trade.get("blockNumber"),
                "timestamp": trade.get("timestamp"),
            }
        )
    return records


def _heuristic_bundle(buys: list[dict[str, Any]]) -> dict[str, Any]:
    """Verdict for source (c): same-block / first-block early-buy cluster.

    Pure function — no I/O. Attributes each distinct holder their FIRST buy
    block, then finds the most crowded block. When that block holds
    >= ``BUNDLE_PCT_THRESHOLD`` % of distinct holders (with at least
    ``MIN_BUYER_SAMPLE`` holders observed) the launch is flagged as bundled.
    A cluster below the threshold is still reported as a metric.
    """
    first_block: dict[str, int] = {}
    for buy in buys:
        maker = str(buy.get("maker") or "").strip()
        raw_block = buy.get("block") or buy.get("blockNumber")
        if not maker or raw_block is None:
            continue
        try:
            block = int(raw_block)
        except (TypeError, ValueError):
            continue
        if block <= 0:
            continue
        first_block.setdefault(maker, block)

    if len(first_block) < MIN_BUYER_SAMPLE:
        return _clean_result()

    block_counts = Counter(first_block.values())
    _top_block, top_count = block_counts.most_common(1)[0]
    if top_count < 2:
        return _clean_result()

    cluster_pct = top_count / len(first_block) * 100.0
    bundled = cluster_pct >= BUNDLE_PCT_THRESHOLD
    return _clean_result(
        bundled=bundled,
        bundle_pct=round(cluster_pct, 1) if top_count >= 2 else None,
        sniper_count=top_count,
        evidence=(f"heuristic: {top_count}/{len(first_block)} holders bought in the same block" if bundled else None),
    )


# ── Orchestrator ─────────────────────────────────────────────────────────


async def detect_bundle(
    address: str,
    chain: str,
    *,
    bundle_lookup: BundleLookupFetcher | None = None,
    holder_analysis: HolderAnalysisFetcher | None = None,
    early_buys: EarlyBuysFetcher | None = None,
) -> dict[str, Any]:
    """Detect bundled snipers for *address* on *chain* (fail-open).

    Sources, in priority order (first positive verdict wins):

    (a) direct ``solana_bundles`` lookup (indexer ClickHouse)
    (b) GMGN holder-analysis sniper/bot flags
    (c) same-block / first-block early-buy cluster heuristic

    Fetchers are injectable keyword-only params — pass async callables to
    stub any source in unit tests (no network/DB). When omitted, the default
    fetchers above are used. Any error (from a fetcher or the orchestration
    itself) degrades to ``{"bundled": False, ...}`` — this never raises.
    """
    bundle_lookup = bundle_lookup or _bundle_lookup_clickhouse
    holder_analysis = holder_analysis or _holder_analysis_gmgn
    early_buys = early_buys or _early_buys_dexscreener

    try:
        # (a) direct bundle lookup — highest-confidence signal
        try:
            row = await bundle_lookup(address, chain)
        except Exception as exc:
            logger.debug("bundle_lookup_failed", error=str(exc)[:120])
            row = None
        if row:
            return _bundle_lookup_signal(row)

        # (b) GMGN holder-analysis sniper/bot flags
        try:
            holders = await holder_analysis(address, chain)
        except Exception as exc:
            logger.debug("holder_analysis_failed", error=str(exc)[:120])
            holders = None
        gmgn_verdict: dict[str, Any] | None = None
        if holders:
            gmgn_verdict = _gmgn_signal(holders)
            if gmgn_verdict["bundled"]:
                return gmgn_verdict

        # (c) heuristic fallback — same-block / first-block cluster
        try:
            buys = await early_buys(address, chain)
        except Exception as exc:
            logger.debug("early_buys_failed", error=str(exc)[:120])
            buys = None
        heuristic_verdict: dict[str, Any] | None = None
        if buys:
            heuristic_verdict = _heuristic_bundle(buys)
            if heuristic_verdict["bundled"]:
                return heuristic_verdict

        # No strong signal: carry observed (sub-threshold) metrics.
        result = _clean_result()
        if gmgn_verdict:
            result["sniper_count"] = gmgn_verdict.get("sniper_count")
        if heuristic_verdict:
            if heuristic_verdict.get("bundle_pct") is not None:
                result["bundle_pct"] = heuristic_verdict.get("bundle_pct")
            if heuristic_verdict.get("sniper_count"):
                result["sniper_count"] = heuristic_verdict.get("sniper_count")
        return result
    except Exception as exc:
        logger.debug("bundle_analysis_failed", error=str(exc)[:120])
        return _clean_result()


# ── Renderer ─────────────────────────────────────────────────────────────


def bundle_signal(result: dict[str, Any]) -> str | None:
    """Render a compact one-line bundle signal for the scan report.

    Returns:
        ``"bundled: 62% snipers"`` — bundled with a ``bundle_pct``
        ``"sniper cluster"``     — bundled with only ``sniper_count``
        ``"bundled launch"``     — bundled with no numeric metrics
        ``None``                 — not bundled (or empty result)
    """
    if not result or not result.get("bundled"):
        return None

    bundle_pct = result.get("bundle_pct")
    if bundle_pct is not None:
        try:
            return f"bundled: {round(float(bundle_pct))}% snipers"
        except (TypeError, ValueError):
            pass

    if result.get("sniper_count"):
        return "sniper cluster"
    return "bundled launch"


__all__ = [
    "BUNDLE_PCT_THRESHOLD",
    "MIN_BUYER_SAMPLE",
    "SNIPER_CLUSTER_MIN",
    "_bundle_lookup_clickhouse",
    "_bundle_lookup_signal",
    "_early_buys_dexscreener",
    "_gmgn_signal",
    "_heuristic_bundle",
    "_holder_analysis_gmgn",
    "bundle_signal",
    "detect_bundle",
]
