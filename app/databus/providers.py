"""Backward-compat shim — moved to app.domains.databus.providers in P4.

Phase 4 of AUDIT-2026-Q3.md moved app/databus/providers/ to
app/domains/databus/providers/. This shim re-exports the public
surface for legacy callers.
"""

from app.domains.databus.providers import (  # noqa: F401
    Provider,
    ProviderChain,
    ProviderTier,
    _CircuitBreaker,
    _RateLimiter,
    build_provider_chains,
)
