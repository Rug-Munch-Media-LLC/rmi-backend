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
import contextlib
from typing import TYPE_CHECKING, Any

from app.domains.scanner.engines._logging import get_logger

# ── Resilience layer (rugmunch-scanner) ────────────────────────────────────
# Guarded import: the private ``rugmunch_scanner`` package may not be
# installed in dev environments. Fallbacks are no-ops so the scanner keeps
# working without the resilience layer (fail-open by contract).
try:
    from rugmunch_scanner.resilience import HealthTracker, with_timeout
except ImportError:  # pragma: no cover — dev env without the private package

    def with_timeout(seconds: float = 30.0, fallback: Any = None):  # type: ignore[misc]
        """No-op fallback for ``rugmunch_scanner.resilience.with_timeout``."""

        def decorator(func):
            return func

        return decorator

    class HealthTracker:  # type: ignore[no-redef]
        """No-op fallback for ``rugmunch_scanner.resilience.HealthTracker``."""

        def __init__(self, threshold: int = 5) -> None:
            self._threshold = threshold

        def record(self, module: str, success: bool, error: str = "") -> None:
            pass

        def is_healthy(self, module: str) -> bool:
            return True

        def get_stats(self, module: str) -> dict[str, Any]:
            return {
                "total": 0,
                "successes": 0,
                "failures": 0,
                "consecutive_failures": 0,
                "last_error": "",
                "healthy": True,
            }


#: Module-level health tracker for the wallet_analysis phase. A module is
#: considered degraded after 5 consecutive failures; the phase is then
#: skipped entirely until a success resets the counter.
health_tracker = HealthTracker(threshold=5)

if TYPE_CHECKING:
    from app.domains.scanner.unified.model import ScanResult

logger = get_logger(__name__)


def _record_module_error(r: ScanResult, module: str, chain: str, reason: str) -> None:
    """Carry a module failure into ``ScanResult.errors`` + log it visibly.

    Errors are NEVER silent: every unavailable/failed module gets a WARNING
    log with structured fields and an entry in the response ``errors`` list
    so the API consumer sees WHY own-data was missing (2026-09-02 hardening).
    """
    reason = (reason or "unknown")[:200]
    logger.warning("scanner_module_failed", module=module, chain=chain, error=reason)
    with contextlib.suppress(AttributeError):  # defensive for legacy results
        r.errors.append({"module": module, "chain": chain, "error": reason})


def _record_module_unavailable(r: ScanResult, module: str, chain: str, reason: str) -> None:
    """Log + record a module that degraded to ``unavailable`` (INFO level)."""
    reason = (reason or "no data")[:200]
    logger.info("scanner_module_unavailable", module=module, chain=chain, reason=reason)
    with contextlib.suppress(AttributeError):
        r.errors.append({"module": module, "chain": chain, "error": f"unavailable: {reason}"})


async def _safe(coro: Any) -> Any:
    """Await a coroutine, converting any exception into a returned value.

    Replacement for ``asyncio.gather(..., return_exceptions=True)`` in the
    sequential wallet-analysis path — same contract, no concurrency.
    """
    try:
        return await coro
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:  # mirrors gather(return_exceptions=True)
        return exc


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


@with_timeout(seconds=120)
async def _run_wallet_analysis_impl(r: ScanResult, address: str, chain: str) -> dict[str, Any]:
    """Wallet-analysis body (timeout-capped by ``with_timeout``).

    Returns a status dict so the timeout wrapper's structured fallback can
    be distinguished from a normal completion by ``run_wallet_analysis``.
    """
    try:
        from app.domains.scanner.engines.funding_analysis import (
            analyze_funding_sources,
            analyze_scam_overlap,
            compute_wallet_freshness,
            compute_wallet_metrics,
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
            if not holders and isinstance(holder_data.get("holders"), list):
                # Birdeye/GMGN-style payloads: [{"address": ...}, ...]
                holders = [
                    str(h.get("address", "")) if isinstance(h, dict) else str(h) for h in holder_data["holders"] if h
                ]

        # ── Holder fallback (2026-09-02 wallet_analysis fix): the DataBus
        # ``holder_data`` chain does not always carry ``top_holders`` (e.g.
        # the sentinelscan cache path returns a full scan payload without
        # one). Without holders the freshness phase had ZERO per-wallet
        # tasks and degraded to "no freshness data" on every orchestrated
        # scan while direct calls (which fetch their own holders) worked.
        # Fall back to the engine's own ClickHouse top-holders query —
        # same source ``compute_wallet_metrics`` already uses.
        if not holders:
            try:
                from app.domains.scanner.engines._common import run_sync
                from app.domains.scanner.engines._funding_queries import _query_top_holders

                rows = await run_sync(_query_top_holders, address, chain, 10)
                holders = [str(row[0]) for row in (rows or []) if row and row[0]]
                if holders:
                    logger.info("wallet_analysis_holders_fallback", chain=chain, count=len(holders))
            except Exception as exc:  # pragma: no cover — best-effort fallback
                logger.debug("wallet_analysis_holders_fallback_failed", error=str(exc)[:120])

        # ── Metric 1: Exchange-funded wallet % ──
        funding_task = analyze_funding_sources(address, chain, holders=holders or None)

        # ── Metric 2: Freshness of the top few holders (bounded) ──
        # Top-5 (was 10): each wallet = 2 heavy ClickHouse queries; at ~35s
        # per wallet under load, 10 wallets blew the scan budget. Top-5 is
        # still a representative freshness sample (2 batches @ concurrency 3).
        freshness_holders = holders[:5]
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

        if not isinstance(funding, BaseException) and isinstance(funding, dict):
            r.funding_analysis = funding
            if funding.get("result") != "scanned":
                _record_module_unavailable(r, "funding_analysis", chain, str(funding.get("reason", "")))
        else:
            _record_module_error(
                r,
                "funding_analysis",
                chain,
                str(funding)[:200] if isinstance(funding, BaseException) else "invalid result",
            )
        if not isinstance(freshness, BaseException) and isinstance(freshness, dict):
            r.wallet_freshness = freshness
            if freshness.get("result") != "scanned":
                _record_module_unavailable(r, "wallet_freshness", chain, str(freshness.get("reason", "")))
        else:
            _record_module_error(
                r,
                "wallet_freshness",
                chain,
                str(freshness)[:200] if isinstance(freshness, BaseException) else "invalid result",
            )
        if not isinstance(overlap, BaseException) and isinstance(overlap, dict):
            r.holder_overlap = overlap
            if overlap.get("result") != "scanned":
                _record_module_unavailable(r, "holder_overlap", chain, str(overlap.get("reason", "")))
        else:
            _record_module_error(
                r,
                "holder_overlap",
                chain,
                str(overlap)[:200] if isinstance(overlap, BaseException) else "invalid result",
            )
        if not isinstance(metrics, BaseException) and isinstance(metrics, dict):
            r.wallet_metrics = metrics
            if metrics.get("result") != "scanned":
                _record_module_unavailable(r, "wallet_metrics", chain, str(metrics.get("reason", "")))
        else:
            _record_module_error(
                r,
                "wallet_metrics",
                chain,
                str(metrics)[:200] if isinstance(metrics, BaseException) else "invalid result",
            )

        r.modules_run.extend(["funding_analysis", "wallet_freshness", "holder_overlap", "wallet_metrics"])
        # Honest data_sources counting (2026-09-02): only credit own data when
        # at least ONE of the 4 wallet metrics actually returned "scanned" —
        # a ClickHouse outage leaves every metric "unavailable" and the module
        # served nothing.
        scanned = any(
            isinstance(m, dict) and m.get("result") == "scanned" for m in (funding, freshness, overlap, metrics)
        )
        if scanned:
            r.enrichment_sources.append("wallet_analysis:own")
        return {"result": "scanned" if scanned else "unavailable"}
    except Exception as exc:
        logger.warning("wallet_analysis_failed", error=str(exc)[:200], chain=chain, token=address[:16])
        _record_module_error(r, "wallet_analysis", chain, str(exc))
        return {"result": "error", "error": str(exc)}


async def run_wallet_analysis(r: ScanResult, address: str, chain: str) -> None:
    """Run exchange-funding, freshness, and scam-overlap analysis.

    Resilience wiring (rugmunch-scanner layer):
      * ``@with_timeout(seconds=120)`` on the impl — a hung phase returns a
        structured timeout dict instead of blocking the scan forever.
      * ``health_tracker`` — after 5 consecutive failures the phase is
        skipped entirely (circuit breaker) until a success resets it.

    Best-effort: each engine degrades gracefully when own ClickHouse data
    is unavailable, so a failed sub-engine never fails the scan.
    """
    if not health_tracker.is_healthy("wallet_analysis"):
        logger.warning("wallet_analysis_skipped_degraded", chain=chain, token=address[:16])
        with contextlib.suppress(AttributeError):  # defensive for legacy results
            r.errors.append({"module": "wallet_analysis", "error": "degraded — skipped after 5 consecutive failures"})
        return

    outcome = await _run_wallet_analysis_impl(r, address, chain)
    if isinstance(outcome, dict) and outcome.get("result") == "timeout":
        # Structured fallback from with_timeout — record as a phase failure
        # so the health tracker sees it and the API consumer knows why the
        # wallet modules are missing.
        note = str(outcome.get("note", "timeout"))
        logger.warning("wallet_analysis_timeout", chain=chain, token=address[:16], note=note)
        _record_module_error(r, "wallet_analysis", chain, note)
        health_tracker.record("wallet_analysis", success=False, error=note)
        return

    success = isinstance(outcome, dict) and outcome.get("result") == "scanned"
    health_tracker.record("wallet_analysis", success=success, error=str(outcome.get("error", ""))[:200])


async def run_bundle_analysis(r: ScanResult, address: str, chain: str) -> None:
    """Run own ClickHouse bundle-launch detection (best-effort).

    Detects coordinated sniper/bot buys in the first blocks after deployment
    (see ``engines/own_bundle_detect.detect_bundled_launch``), plus same-slot
    buy concentration on Solana (``detect_same_slot_bundler``, Helius-backed;
    degrades to a ``[REVIEW]`` note when Helius is unreachable), and stores
    the result on ``ScanResult.bundle_analysis`` so scoring can emit
    ``BUNDLE_DETECTED``. Never raises — a missing engine or ClickHouse
    outage degrades to ``unavailable``.
    """
    try:
        from app.domains.scanner.engines.own_bundle_detect import (
            detect_bundled_launch,
            detect_same_slot_bundler,
        )

        bundle_task = asyncio.create_task(detect_bundled_launch(address, chain))
        same_slot_task = asyncio.create_task(detect_same_slot_bundler(address, chain))
        gathered = await asyncio.gather(bundle_task, same_slot_task, return_exceptions=True)
        bundle: Any = gathered[0]
        same_slot: Any = gathered[1]

        if isinstance(bundle, dict):
            r.bundle_analysis = bundle
            r.modules_run.append("bundle_analysis")
            if bundle.get("result") == "scanned":
                r.enrichment_sources.append("bundle_analysis:own")
            else:
                _record_module_unavailable(r, "bundle_analysis", chain, str(bundle.get("reason", "")))
        else:
            _record_module_error(
                r,
                "bundle_analysis",
                chain,
                str(bundle)[:200] if isinstance(bundle, BaseException) else "invalid result",
            )
        if isinstance(same_slot, dict) and isinstance(r.bundle_analysis, dict):
            r.bundle_analysis["same_slot"] = same_slot
    except Exception as exc:
        logger.warning("bundle_analysis_failed", error=str(exc)[:200], chain=chain, token=address[:16])
        _record_module_error(r, "bundle_analysis", chain, str(exc))


#: First-buyers sample size per scan tier (2026-08-29 depth push — synced
#: with the private scanner's FIRST_BUYERS_LIMIT_DEEP): quick/basic scans
#: surface the top 25 earliest buyers; deep/elite scans get the full
#: 250-wallet breakdown (raised from 200 — deep scans go deeper).
FIRST_BUYERS_LIMIT_QUICK = 25
FIRST_BUYERS_LIMIT_DEEP = 250


async def run_first_buyers(r: ScanResult, address: str, chain: str, tier: str) -> None:
    """Run first-buyers analysis from own indexer data (best-effort).

    Ranks the earliest distinct buyers and classifies each wallet (fresh /
    new / established age buckets, same-window bundling) — see
    ``engines/first_buyers.py``. Sample size scales with tier: 25 on
    quick scans, 250 on deep/elite. Never raises; a ClickHouse outage or
    unindexed token leaves ``r.first_buyers`` as None.
    """
    try:
        from app.domains.scanner.engines.first_buyers import get_first_buyers

        limit = FIRST_BUYERS_LIMIT_DEEP if (tier or "").lower() in ("elite", "admin") else FIRST_BUYERS_LIMIT_QUICK
        result = await get_first_buyers(address, chain, limit=limit)
        if isinstance(result, dict) and result.get("buyers"):
            r.first_buyers = result
            r.modules_run.append("first_buyers")
            r.enrichment_sources.append("first_buyers:own")
        else:
            _record_module_unavailable(
                r,
                "first_buyers",
                chain,
                str((result or {}).get("reason", "")) if isinstance(result, dict) else "no buyer data",
            )
    except Exception as exc:
        logger.warning("first_buyers_failed", error=str(exc)[:200], chain=chain, token=address[:16])
        _record_module_error(r, "first_buyers", chain, str(exc))


async def run_token_metrics(r: ScanResult, address: str, chain: str) -> None:
    """Run the Phanes-parity token-metric engines in parallel (best-effort).

    2026-09-02 Phanes-parity push: dev-sold status, DEX-paid status, ATH /
    current price / 1h change, and launch-sniper count — the four signals
    competitor scanners surface that we now compute from OWN ClickHouse
    data. All four engines run concurrently (each with its own per-query
    ClickHouse client via ``_new_ch_client()`` to avoid the shared-client
    simultaneous-queries bug) and each degrades independently.

    Deployer resolution mirrors ``risk_report.run_risk_report_phase``:
    security payload first, then the SENTINEL contract-authority module.
    Launch block comes from the bundle-analysis phase when it already ran.
    Never raises; a failed engine leaves the corresponding field None.
    """
    from app.domains.scanner.engines.dev_sold import check_dev_sold
    from app.domains.scanner.engines.dex_paid import check_dex_paid
    from app.domains.scanner.engines.price_metrics import get_price_metrics
    from app.domains.scanner.engines.sniper_detect import detect_snipers
    from app.domains.scanner.unified.sentinel_phase import extract_deployer_from_security

    # ── Resolve deployer (best-effort, several sources) ──
    deployer = extract_deployer_from_security(r.security)
    if deployer is None and isinstance(r.sentinel, dict):
        for bucket in ("free", "premium"):
            mods = r.sentinel.get(bucket)
            if not isinstance(mods, dict):
                continue
            ca = mods.get("contract_authority")
            if isinstance(ca, dict):
                deployer = extract_deployer_from_security(ca) or deployer

    # ── Resolve launch block (bundle phase already computed it when it ran) ──
    launch_block: int | None = None
    ba = r.bundle_analysis if isinstance(r.bundle_analysis, dict) else {}
    if isinstance(ba.get("deploy_block"), int) and ba["deploy_block"] > 0:
        launch_block = ba["deploy_block"]

    results = await asyncio.gather(
        check_dev_sold(address, chain, deployer),
        check_dex_paid(address, chain),
        get_price_metrics(address, chain),
        detect_snipers(address, chain, launch_block),
        return_exceptions=True,
    )
    dev_sold, dex_paid, price_metrics, snipers = results

    def _attach(field: str, res: Any, module: str) -> None:
        if isinstance(res, BaseException):
            _record_module_error(r, module, chain, str(res))
            return
        if isinstance(res, dict) and res.get("result") == "scanned":
            setattr(r, field, res)
            r.modules_run.append(module)
            r.enrichment_sources.append(f"{module}:own")
        else:
            note = str((res or {}).get("note") or (res or {}).get("error") or "no data")
            _record_module_unavailable(r, module, chain, note)

    _attach("dev_sold", dev_sold, "dev_sold")
    _attach("dex_paid", dex_paid, "dex_paid")
    _attach("price_metrics", price_metrics, "price_metrics")
    _attach("sniper_count", snipers, "sniper_detect")
