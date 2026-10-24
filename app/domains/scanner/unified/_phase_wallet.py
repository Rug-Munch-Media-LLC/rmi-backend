"""Wallet-analysis phase — exchange funding, freshness, overlap, metrics.

Split out of ``_phase_helpers.py`` (debt wave, 2026-10-25).

Both halves are best-effort: each engine degrades gracefully when own
ClickHouse data is unavailable, so a failed sub-engine never fails the scan.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from app.domains.scanner.engines._logging import get_logger
from app.domains.scanner.unified._phase_common import (
    _record_module_error,
    _record_module_unavailable,
    _safe,
)
from app.domains.scanner.unified._phase_freshness import gather_freshness
from app.domains.scanner.unified._phase_health import (
    ScannerTimeoutError,
    health_tracker,
    timeout_decorator,
)
from app.domains.scanner.unified._phase_wallet_inputs import (
    FRESHNESS_HOLDER_SAMPLE,
    probe_recent_transfers,
    resolve_holders,
)

if TYPE_CHECKING:
    from app.domains.scanner.unified.model import ScanResult

logger = get_logger(__name__)


@timeout_decorator(120, operation="wallet_analysis")
async def _run_wallet_analysis_impl(r: ScanResult, address: str, chain: str) -> dict[str, Any]:
    """Wallet-analysis body (timeout-capped by ``timeout_decorator``).

    Returns a status dict; a hung phase raises ``ScannerTimeoutError`` so
    ``run_wallet_analysis`` can record the failure and open the circuit.
    """
    try:
        from app.domains.scanner.engines.funding_analysis import (
            analyze_funding_sources,
            analyze_scam_overlap,
            compute_wallet_freshness,
            compute_wallet_metrics,
        )

        early = await probe_recent_transfers(r, address, chain)
        if early is not None:
            return early
        holders = await resolve_holders(r, address, chain)

        # ── Metric 1: Exchange-funded wallet % ──
        funding_task = analyze_funding_sources(address, chain, holders=holders or None)

        # ── Metric 2: Freshness of the top few holders (bounded) ──
        # Top-5 (was 10): each wallet = 2 heavy ClickHouse queries; at ~35s
        # per wallet under load, 10 wallets blew the scan budget. Top-5 is
        # still a representative freshness sample (2 batches @ concurrency 3).
        freshness_holders = holders[:FRESHNESS_HOLDER_SAMPLE]
        freshness_tasks = [compute_wallet_freshness(h, chain) for h in freshness_holders]
        freshness_task = gather_freshness(freshness_tasks)

        # ── Metric 3: Holder overlap vs known scam tokens ──
        overlap_task = analyze_scam_overlap(address, chain)

        # ── Metric 4: Token-level wallet freshness metrics (any token) ──
        metrics_task = compute_wallet_metrics(address, chain, holders=holders or None)

        funding: Any
        freshness: Any
        overlap: Any
        metrics: Any

        # ── Sequential execution (2026-09-02 wallet_analysis fix) ──
        # The old single asyncio.gather ran 4 metric engines + 10 freshness
        # sub-tasks at once — ~25 concurrent heavy ClickHouse queries on
        # large tokens (USDT: 26.6M transfer rows). Combined with the shared
        # client singleton this produced "Simultaneous queries on single
        # connection" errors (fixed per-query) AND server-side contention
        # that pushed every engine past its budget → uniform "unavailable".
        # Sequential engines + bounded freshness concurrency keep each query
        # fast; total wall time is comparable because queries no longer
        # thrash each other.
        funding = await _safe(funding_task)
        overlap = await _safe(overlap_task)
        metrics = await _safe(metrics_task)
        freshness = await _safe(freshness_task)

        attached = [
            attach_wallet_metric(r, field, module, res, chain)
            for field, module, res in (
                ("funding_analysis", "funding_analysis", funding),
                ("wallet_freshness", "wallet_freshness", freshness),
                ("holder_overlap", "holder_overlap", overlap),
                ("wallet_metrics", "wallet_metrics", metrics),
            )
        ]

        r.modules_run.extend(["funding_analysis", "wallet_freshness", "holder_overlap", "wallet_metrics"])
        # Honest data_sources counting (2026-09-02): only credit own data when
        # at least ONE of the 4 wallet metrics actually returned "scanned" —
        # a ClickHouse outage leaves every metric "unavailable" and the module
        # served nothing.
        scanned = any(attached)
        if scanned:
            r.enrichment_sources.append("wallet_analysis:own")
        return {"result": "scanned" if scanned else "unavailable"}
    except Exception as exc:
        logger.warning("wallet_analysis_failed", error=str(exc)[:200], chain=chain, token=address[:16])
        _record_module_error(r, "wallet_analysis", chain, str(exc))
        return {"result": "error", "error": str(exc)}


def attach_wallet_metric(r: ScanResult, field: str, module: str, res: Any, chain: str) -> bool:
    """Attach one wallet-engine result, recording honest unavailable/error notes.

    Unlike the other phases the field is stored even when the engine degraded
    (the payload keeps its ``reason``); the return value reports whether the
    engine actually produced data.
    """
    if isinstance(res, BaseException):
        _record_module_error(r, module, chain, str(res)[:200])
        return False
    if not isinstance(res, dict):
        _record_module_error(r, module, chain, "invalid result")
        return False
    setattr(r, field, res)
    if res.get("result") != "scanned":
        _record_module_unavailable(r, module, chain, str(res.get("reason", "")))
        return False
    return True


async def run_wallet_analysis(r: ScanResult, address: str, chain: str) -> None:
    """Run exchange-funding, freshness, and scam-overlap analysis.

    Resilience wiring (rugmunch-scanner layer):
      * ``@timeout_decorator(120)`` on the impl — a hung phase raises
        ``ScannerTimeoutError`` instead of blocking the scan forever.
      * ``health_tracker`` — after 5 consecutive failures the phase is
        skipped entirely (circuit breaker) until the half-open probe
        succeeds again.

    Best-effort: each engine degrades gracefully when own ClickHouse data
    is unavailable, so a failed sub-engine never fails the scan.
    """
    if not health_tracker.can_proceed():
        logger.warning("wallet_analysis_skipped_degraded", chain=chain, token=address[:16])
        with contextlib.suppress(AttributeError):  # defensive for legacy results
            r.errors.append({"module": "wallet_analysis", "error": "degraded — skipped after 5 consecutive failures"})
        return

    try:
        outcome = await _run_wallet_analysis_impl(r, address, chain)
    except ScannerTimeoutError as exc:
        # The timeout decorator raises — record it as a phase failure so the
        # circuit sees it and the API consumer knows why the wallet modules
        # are missing.
        note = f"query timed out after {exc.timeout_seconds:g}s"
        logger.warning("wallet_analysis_timeout", chain=chain, token=address[:16], note=note)
        _record_module_error(r, "wallet_analysis", chain, note)
        health_tracker.record_failure()
        return

    if isinstance(outcome, dict) and outcome.get("result") == "scanned":
        health_tracker.record_success(0.0)
    else:
        health_tracker.record_failure()
        logger.warning(
            "wallet_analysis_degraded",
            chain=chain,
            token=address[:16],
            result=str((outcome or {}).get("result", "unknown"))[:64],
        )
