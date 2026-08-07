"""billing — DEPRECATED shim. Use app.domains.billing.

Phase 4.1 of AUDIT-2026-Q3.md moved app/billing/ to app/domains/billing/.
This shim re-exports the canonical surface and aliases submodules so that
`from app.billing.x402.router import router` keeps working.

MIGRATION: replace `from app.billing.x402 import ...` with
`from app.domains.billing.x402 import ...`.
"""

from __future__ import annotations

import sys as _sys

from app.domains.billing import x402 as _x402

# Public re-exports
from app.domains.billing.x402 import (
    CHAIN_USDC,
    TOOL_PRICES,
    X402Enforcer,
    router as _router,
)

# Alias submodules so `from app.billing.x402.router import router` works
_sys.modules["app.billing.x402"] = _x402
_sys.modules["app.billing.x402.router"] = _router

__all__ = [
    "CHAIN_USDC",
    "TOOL_PRICES",
    "X402Enforcer",
]
