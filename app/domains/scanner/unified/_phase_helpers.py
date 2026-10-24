"""Phase helpers — public facade for the ``UnifiedTokenScanner`` phase runners.

Split into cohesive modules (debt wave, 2026-10-25) to keep every file under
the ≤200-line policy; this module preserves the historical import surface:

* ``_phase_common``        — error/unavailable recording, exception-safe await
* ``_phase_health``        — resilience wiring (circuit breaker + timeouts)
* ``_phase_freshness``     — per-wallet freshness aggregation
* ``_phase_wallet_inputs`` — transfer probe, holder resolution
* ``_phase_wallet``        — wallet-analysis phase
* ``_phase_bundle``        — bundle-launch detection
* ``_phase_buyers``        — first-buyers analysis
* ``_phase_token_metrics`` — Phanes-parity token metrics
"""

from __future__ import annotations

from app.domains.scanner.unified._phase_bundle import BUNDLE_GRAPH_BUDGET_S, run_bundle_analysis
from app.domains.scanner.unified._phase_buyers import (
    FIRST_BUYERS_LIMIT_DEEP,
    FIRST_BUYERS_LIMIT_QUICK,
    run_first_buyers,
)
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
from app.domains.scanner.unified._phase_token_metrics import run_token_metrics
from app.domains.scanner.unified._phase_wallet import run_wallet_analysis
from app.domains.scanner.unified._phase_wallet_inputs import (
    FRESHNESS_HOLDER_SAMPLE,
    HOLDER_SAMPLE,
    TRANSFER_PROBE_DAYS,
    holders_from_payload,
    probe_recent_transfers,
    resolve_holders,
)

__all__ = [
    "BUNDLE_GRAPH_BUDGET_S",
    "FIRST_BUYERS_LIMIT_DEEP",
    "FIRST_BUYERS_LIMIT_QUICK",
    "FRESHNESS_HOLDER_SAMPLE",
    "HOLDER_SAMPLE",
    "TRANSFER_PROBE_DAYS",
    "ScannerTimeoutError",
    "_record_module_error",
    "_record_module_unavailable",
    "_safe",
    "gather_freshness",
    "health_tracker",
    "holders_from_payload",
    "probe_recent_transfers",
    "resolve_holders",
    "run_bundle_analysis",
    "run_first_buyers",
    "run_token_metrics",
    "run_wallet_analysis",
    "timeout_decorator",
]
