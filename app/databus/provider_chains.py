"""Backward-compat shim - moved to app.domains.databus._generated.provider_chains in P4.

Phase 4 of AUDIT-2026-Q3.md moved app/databus/_generated/ to
app/domains/databus/_generated/. This shim re-exports for legacy callers.

This module is auto-generated. Do not edit manually.
Regenerate via: scripts/generate_provider_chains.py
"""

from app.domains.databus._generated.provider_chains import (  # noqa: F401
    build_provider_chains,
)
