"""Bundle-launch detection phase (own ClickHouse, best-effort).

Split out of ``_phase_helpers.py`` (debt wave, 2026-10-25).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from app.domains.scanner.engines._logging import get_logger
from app.domains.scanner.unified._phase_common import (
    _record_module_error,
    _record_module_unavailable,
)

if TYPE_CHECKING:
    from app.domains.scanner.unified.model import ScanResult

logger = get_logger(__name__)


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
        from app.domains.scanner.engines._own_bundle_common import bounded_pair
        from app.domains.scanner.engines.own_bundle_detect import (
            detect_bundled_launch,
            detect_same_slot_bundler,
        )

        bundle, same_slot = await bounded_pair(  # budgeted: see _own_bundle_common
            detect_bundled_launch(address, chain), detect_same_slot_bundler(address, chain)
        )

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
        if isinstance(r.bundle_analysis, dict):
            await _attach_bundle_graph(r, address, chain)
    except Exception as exc:
        logger.warning("bundle_analysis_failed", error=str(exc)[:200], chain=chain, token=address[:16])
        _record_module_error(r, "bundle_analysis", chain, str(exc))


#: Wall-clock budget for the bundle graph (own ClickHouse reads only).
BUNDLE_GRAPH_BUDGET_S = 8.0


async def _attach_bundle_graph(r: ScanResult, address: str, chain: str) -> None:
    """Attach the multi-signal bundle graph to ``bundle_analysis``.

    Best-effort and inside the phase's existing budget: a graph failure leaves
    the legacy bundle verdict untouched and records why.
    """
    try:
        from app.domains.scanner.engines.bundle_graph import build_bundle_graph

        graph = await asyncio.wait_for(build_bundle_graph(address, chain), timeout=BUNDLE_GRAPH_BUDGET_S)
        if isinstance(graph, dict) and isinstance(r.bundle_analysis, dict):
            r.bundle_analysis["bundle_graph"] = graph
    except Exception as exc:  # never fail a scan for the graph
        logger.debug("bundle_graph_attach_failed error=%s", str(exc)[:160])
