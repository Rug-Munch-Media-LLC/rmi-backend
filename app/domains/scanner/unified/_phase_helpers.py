"""Phase helpers — background phase sub-runners for ``UnifiedTokenScanner``.

Extracted from ``unified/scanner.py`` (degodding wave 7, 2026-08-09) so the
two largest phase blocks (``_run_wallet_analysis`` C-20 and its freshness
aggregator ``_gather_freshness`` C-17) live beside the other phase helpers
instead of inflating the scanner orchestration class.

Both are best-effort: each engine degrades gracefully when own ClickHouse
data is unavailable, so a failed sub-engine never fails the scan. Errors are
captured (and logged) inside ``run_wallet_analysis``; ``gather_freshness``
returns an ``unavailable`` summary when no wallet finished a scan.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.domains.scanner.unified.model import ScanResult

logger = get_logger(__name__)


async def gather_freshness(tasks: list[Any]) -> dict[str, Any]:
    """Aggregate per-wallet freshness results into a token-level summary.

    Runs the given freshness tasks concurrently and returns a summary dict
    with the fresh-wallet concentration signal (``fresh_wallet_pct`` is the
    share of analyzed top holders classified FRESH_SUSPICIOUS).
    """
    results = await asyncio.gather(*tasks, return_exceptions=True)
    analyzed: list[dict[str, Any]] = []
    for res in results:
        if not isinstance(res, BaseException) and isinstance(res, dict) and res.get("result") == "scanned":
            analyzed.append(res)

    if not analyzed:
        return {"result": "unavailable", "reason": "no freshness data", "analyzed": 0}

    classifications = [r.get("classification", "ESTABLISHED") for r in analyzed]
    suspicious = sum(1 for c in classifications if c == "FRESH_SUSPICIOUS")
    clean = sum(1 for c in classifications if c == "FRESH_CLEAN")
    established = sum(1 for c in classifications if c == "ESTABLISHED")
    avg_score = sum(float(r.get("freshness_score", 0) or 0) for r in analyzed) / len(analyzed)

    return {
        "result": "scanned",
        "analyzed": len(analyzed),
        "fresh_suspicious": suspicious,
        "fresh_clean": clean,
        "established": established,
        "fresh_wallet_pct": round(suspicious / len(analyzed) * 100, 1) if analyzed else 0.0,
        "avg_freshness_score": round(avg_score, 1),
        "wallets": [
            {
                "address": r.get("address"),
                "freshness_score": r.get("freshness_score"),
                "classification": r.get("classification"),
                "age_days": r.get("age_days"),
                "total_txs": r.get("total_txs"),
                "funded_by_cex": r.get("funded_by_cex"),
            }
            for r in analyzed
        ],
    }


async def run_wallet_analysis(r: ScanResult, address: str, chain: str) -> None:
    """Run exchange-funding, freshness, and scam-overlap analysis.

    Best-effort: each engine degrades gracefully when own ClickHouse data
    is unavailable, so a failed sub-engine never fails the scan.
    """
    try:
        from app.domains.scanner.engines.funding_analysis import (
            analyze_funding_sources,
            analyze_scam_overlap,
            compute_wallet_freshness,
        )

        holders: list[str] = []
        holder_data = r.holders if isinstance(r.holders, dict) else None
        if holder_data:
            top = holder_data.get("top_holders") or []
            if isinstance(top, list):
                holders = [str(h.get("address", "")) for h in top if isinstance(h, dict) and h.get("address")]
            elif isinstance(top, dict):
                # Some providers return {address: pct}
                holders = [str(a) for a in top if a]

        # ── Metric 1: Exchange-funded wallet % ──
        funding_task = analyze_funding_sources(address, chain, holders=holders or None)

        # ── Metric 2: Freshness of the top few holders (bounded) ──
        freshness_holders = holders[:10]
        freshness_tasks = [compute_wallet_freshness(h, chain) for h in freshness_holders]
        freshness_task = gather_freshness(freshness_tasks)

        # ── Metric 3: Holder overlap vs known scam tokens ──
        overlap_task = analyze_scam_overlap(address, chain)

        funding: Any
        freshness: Any
        overlap: Any
        funding, freshness, overlap = await asyncio.gather(
            funding_task,
            freshness_task,
            overlap_task,
            return_exceptions=True,
        )

        if not isinstance(funding, BaseException) and isinstance(funding, dict):
            r.funding_analysis = funding
        if not isinstance(freshness, BaseException) and isinstance(freshness, dict):
            r.wallet_freshness = freshness
        if not isinstance(overlap, BaseException) and isinstance(overlap, dict):
            r.holder_overlap = overlap

        r.modules_run.extend(["funding_analysis", "wallet_freshness", "holder_overlap"])
        r.enrichment_sources.append("wallet_analysis:own")
    except Exception as exc:
        logger.debug("wallet_analysis_failed", error=str(exc)[:120])
