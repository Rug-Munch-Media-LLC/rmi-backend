"""Phanes-parity token-metric phase (own ClickHouse, best-effort).

Split out of ``_phase_helpers.py`` (debt wave, 2026-10-25).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from app.domains.scanner.unified._phase_common import (
    _record_module_error,
    _record_module_unavailable,
)

if TYPE_CHECKING:
    from app.domains.scanner.unified.model import ScanResult


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
