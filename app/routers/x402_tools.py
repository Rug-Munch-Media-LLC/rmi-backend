"""x402_tools.py — DEPRECATED shim. Re-exports from app.billing.x402.

from __future__ import annotations

Phase 3A of AUDIT-2026-Q3.md split this 5,780-LOC god-file into focused modules
under app/billing/x402/. This file is kept as a thin re-export shim so any
caller that does `from app.routers.x402_tools import router` still works.

MIGRATION: replace `from app.routers.x402_tools import router` with
`from app.billing.x402.router import router`. The legacy import path
will be removed in a future release.

Re-exported public surface (preserves every symbol other modules import):
  router                       — FastAPI APIRouter with all 77 endpoints
  fetch_with_fallback          — HTTP multi-URL fallback helper (used by x402_forensic_tools)
  rpc_call                     — JSON-RPC fallback (used by wash_trading_detector, whale_accumulation)
  _audit_solana, _audit_evm    — used by app/fallback_engine.py
  BUNDLES                      — bundle pricing dict (used by app/billing/x402/enforcement.py)
  TOOL_ALIASES                 — alias map (used by app/routers/mcp_server.py)
  HUMAN_PAYMENT_TOKENS, HUMAN_PAY_TO, _resolve_pay_to
  record_x402_payment          — P3A stub (replaces pre-existing # noqa: F821 NameError-bait)

Pre-existing issues surfaced (not introduced) by this refactor:
  - `record_x402_payment` was referenced 77+ times via `# noqa: F821` but never
    defined. The legacy module silently NameError'd at every endpoint call.
    P3A now exposes a logging stub here so the missing-import fix has a clear
    home. Track: fix(f821).
"""

from app.billing.x402.router import router
from app.billing.x402.shared import (
    BUNDLES,
    HUMAN_PAY_TO,
    HUMAN_PAYMENT_TOKENS,
    TOOL_ALIASES,
    _audit_evm,
    _audit_solana,
    _resolve_pay_to,
    fetch_with_fallback,
    record_x402_payment,
    rpc_call,
)

__all__ = [
    "BUNDLES",
    "HUMAN_PAYMENT_TOKENS",
    "HUMAN_PAY_TO",
    "TOOL_ALIASES",
    "_audit_evm",
    "_audit_solana",
    "_resolve_pay_to",
    "fetch_with_fallback",
    "record_x402_payment",
    "router",
    "rpc_call",
]
