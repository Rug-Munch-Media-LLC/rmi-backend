"""Domain health-check registry for the RMI backend.

This module owns the *domain* health registry — the per-domain
(``rag``, ``catalog``, ``alerts``, ``postiz``, ``analytics``, ``x402``,
``token``) check callbacks that domains register at import time via
``register_health_check`` and that ``app.mcp.server`` aggregates.

The HTTP liveness/readiness probes NO LONGER live here. They were
consolidated (2026-08-29) into ``app.core.health_route`` to eliminate a
disagreeing duplicate: this module previously mounted ``/healthz`` +
``/readyz`` with its own sync-Redis / per-call SQLAlchemy probes, while
``health_route`` mounted ``/live`` + ``/ready`` with the shared async
pool + asyncpg probes. The two reported different redis health for the
same backend. The canonical endpoints are now:

  - ``/live``  (liveness)  — ``app.core.health_route.liveness``
  - ``/ready`` (readiness) — ``app.core.health_route.readiness``
  - ``/health`` (deep diagnostic) — ``app.core.health_route.health``

``health_route`` is registered early in ``app.factory`` (before
``mount_all``) so orchestrator probes survive a router-mount failure.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

log = logging.getLogger(__name__)


@dataclass
class DomainHealth:
    """Health status for one domain (catalog, rag, alerts, etc.).

    Restored 2026-08-21: the /healthz centralization commit removed this
    dataclass but seven domain modules still construct it in their
    ``register_health_checks`` callbacks.
    """

    name: str
    healthy: bool
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    latency_ms: int | None = None


# ── Domain health-check registry ───────────────────────────────────
# Seven domains (rag, catalog, alerts, postiz, analytics, x402, token)
# register callbacks into this registry via register_health_check();
# app.mcp.server aggregates them through run_health_checks().
_health_checks: dict[str, Callable[[], Any]] = {}


def register_health_check(name: str, func: Callable[[], Any]) -> None:
    """Register a health check function for a domain."""
    if name in _health_checks:
        log.warning(f"health_check_already_registered name={name}")
    _health_checks[name] = func


async def run_health_checks() -> dict[str, DomainHealth]:
    """Run all registered health checks and return results."""
    results: dict[str, DomainHealth] = {}
    for name, func in _health_checks.items():
        try:
            start = asyncio.get_event_loop().time()
            result = func()
            if asyncio.iscoroutine(result):
                result = await result
            took_ms = int((asyncio.get_event_loop().time() - start) * 1000)
            results[name] = DomainHealth(
                name=name,
                healthy=result.healthy if hasattr(result, "healthy") else bool(result),
                details=getattr(result, "details", {}),
                error=getattr(result, "error", None),
                latency_ms=took_ms,
            )
        except Exception as e:
            log.exception(f"health_check_failed name={name}")
            results[name] = DomainHealth(name=name, healthy=False, error=str(e))
    return results


def get_health_status() -> dict[str, Any]:
    """Sync wrapper: run all registered domain checks (non-async callers)."""
    import asyncio as _aio

    results = _aio.run(run_health_checks())
    return {
        "status": (
            "healthy"
            if all(d.healthy for d in results.values())
            else "degraded"
            if any(d.healthy for d in results.values())
            else "unhealthy"
        ),
        "domains": {
            n: {"healthy": d.healthy, "error": d.error, "latency_ms": d.latency_ms} for n, d in results.items()
        },
    }
