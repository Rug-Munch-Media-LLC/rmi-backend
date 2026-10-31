"""Ports that let ``app.core`` report state owned by a higher layer.

``app.core`` is the lowest layer (see ``.importlinter`` contract 4: *Core must
not import from higher layers*). The deep ``/health`` payload still has to
report which scoring calibration answered, and that fact is owned by
``app.domains.scanner.risk_policy``. So the dependency is inverted:

* this module owns the port and a safe default — a visible health fact, never a
  crash (the pre-inversion probe degraded the same way);
* ``app.factory`` — the composition root — binds the adapter at startup.

The adapter is registered as a *callable*, not as its result, so policy
resolution keeps happening on the first ``/health`` request exactly as before
(it stays cache-backed inside ``risk_policy.resolve_policy``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

#: What an adapter must return: ``{"name", "source", "illustrative"}``.
RiskPolicyReport = Callable[[], dict[str, Any]]

_provider: RiskPolicyReport | None = None


def register_risk_policy_report(provider: RiskPolicyReport) -> None:
    """Bind the adapter. Composition-root duty; idempotent (last bind wins)."""
    global _provider
    _provider = provider


def risk_policy_report() -> dict[str, Any]:
    """Resolved scoring policy, degrading to a visible error (never silent)."""
    if _provider is None:
        return {
            "name": None,
            "source": "error",
            "illustrative": True,
            "error": "risk policy report port not registered",
        }
    try:
        return _provider()
    except Exception as exc:  # a broken provider is a health fact, not a crash
        return {"name": None, "source": "error", "illustrative": True, "error": str(exc)[:200]}
