"""Per-wallet freshness aggregation for the wallet-analysis phase.

Split out of ``_phase_helpers.py`` (debt wave, 2026-10-25).
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.domains.scanner.engines._logging import get_logger

logger = get_logger(__name__)


async def gather_freshness(tasks: list[Any], max_concurrency: int = 3) -> dict[str, Any]:
    """Aggregate per-wallet freshness results into a token-level summary.

    Runs the given freshness tasks with bounded concurrency (default 3 —
    each task issues 2 heavy ClickHouse queries; running all 10 at once
    starved the ClickHouse server and pushed the whole wallet phase past
    the scan budget on large tokens) and returns a summary dict with the
    fresh-wallet concentration signal (``fresh_wallet_pct`` is the share
    of analyzed top holders classified FRESH_SUSPICIOUS).
    """
    sem = asyncio.Semaphore(max(1, max_concurrency))

    async def _bounded(task: Any) -> Any:
        async with sem:
            return await task

    results = await asyncio.gather(*(_bounded(t) for t in tasks), return_exceptions=True)
    analyzed: list[dict[str, Any]] = []
    for res in results:
        if isinstance(res, BaseException):
            # Never silent: a failed per-wallet freshness task must be logged
            # so "unavailable" summaries are diagnosable (2026-09-02).
            logger.warning("freshness_task_failed", error=str(res)[:200])
            continue
        if isinstance(res, dict) and res.get("result") == "scanned":
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
