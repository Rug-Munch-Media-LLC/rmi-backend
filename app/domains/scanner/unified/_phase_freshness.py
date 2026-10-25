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
    analyzed = _collect_analyzed(results)
    if not analyzed:
        return {"result": "unavailable", "reason": "no freshness data", "analyzed": 0}
    return _summarize_freshness(analyzed)


def _collect_analyzed(results: list[Any]) -> list[dict[str, Any]]:
    """Keep scanned wallets; a failed task is logged, never silently dropped."""
    analyzed: list[dict[str, Any]] = []
    for res in results:
        if isinstance(res, BaseException):
            # Never silent: a failed per-wallet freshness task must be logged
            # so "unavailable" summaries are diagnosable (2026-09-02).
            logger.warning("freshness_task_failed", error=str(res)[:200])
            continue
        if isinstance(res, dict) and res.get("result") == "scanned":
            analyzed.append(res)
    return analyzed


def _count_class(classifications: list[str], label: str) -> int:
    return sum(1 for c in classifications if c == label)


def _summarize_freshness(analyzed: list[dict[str, Any]]) -> dict[str, Any]:
    """Token-level summary of the per-wallet freshness results."""
    classifications = [r.get("classification", "ESTABLISHED") for r in analyzed]
    suspicious = _count_class(classifications, "FRESH_SUSPICIOUS")
    total = len(analyzed)
    avg_score = sum(float(r.get("freshness_score", 0) or 0) for r in analyzed) / total
    return {
        "result": "scanned",
        "analyzed": total,
        "fresh_suspicious": suspicious,
        "fresh_clean": _count_class(classifications, "FRESH_CLEAN"),
        "established": _count_class(classifications, "ESTABLISHED"),
        "fresh_wallet_pct": round(suspicious / total * 100, 1),
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
