"""facilitators — DEPRECATED shim. Use app.domains.billing.facilitators.

Phase 4.1 of AUDIT-2026-Q3.md moved app/facilitators/ to
app/domains/billing/facilitators/. This shim re-exports the public surface
and aliases submodules so legacy imports like
`from app.facilitators.base import Facilitator` keep working.

MIGRATION: replace `from app.facilitators.base import ...` with
`from app.domains.billing.facilitators.base import ...`.
"""

from __future__ import annotations

import importlib as _importlib
import sys as _sys

# Re-export public surface
from app.domains.billing.facilitators import (
    Facilitator,
    FacilitatorRegistry,
    FacilitatorRouter,
    SettlementResult,
    VerificationResult,
    get_facilitator_router,
)

# Alias submodules
for _name in (
    "base",
    "config",
    "router",
    "asterpay",
    "bitcoin_selfverify",
    "cloudflare_x402",
    "coinbase_cdp",
    "eip7702",
    "merx_tron",
    "payai",
    "pieverse",
    "primev",
    "satoshi",
    "startup",
    "tron_selfverify",
    "x402_rs",
):
    _sys.modules[f"app.facilitators.{_name}"] = _importlib.import_module(
        f"app.domains.billing.facilitators.{_name}"
    )
