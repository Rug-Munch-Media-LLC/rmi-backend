"""
RMI x402 DataBus Tools — Pay-per-call APIs wired through DataBus
=================================================================
Split from x402_databus_tools.py into focused submodules.

Backward-compatible re-export layer. All existing imports continue to work:
    from app.routers.x402_databus_tools import X402_TOOL_PRICING
    from app.routers.x402_databus_tools import check_trial
    from app.routers.x402_databus_tools import router

Architecture:
    pricing.py       — X402_TOOL_PRICING dictionary (811 lines)
    models.py        — Pydantic request/response models
    rate_limit.py    — Redis trial tracking
    auth.py          — Payment verification, response cleaning
    router.py        — APIRouter definition
    market_tools.py  — Catalog + market data endpoints
    analysis_tools.py — Risk/security/analysis endpoints + universal /fetch
    wallet_tools.py  — Wallet/entity/elite endpoints

Every endpoint routes through app.databus.fetch() with full access control.
No raw HTTP calls. No backdoors. Consumer type is always x402_paid.

Pricing Tiers:
  BASIC   ($0.01-0.02) - Quick lookups, public-ish data
  PREMIUM ($0.05-0.10) - Advanced intelligence, multi-source
  ELITE   ($0.15-0.40) - Institutional forensics, admin-grade data

Security:
  - Every call goes through DataBus access_control (x402_paid consumer)
  - Zero backdoors: no direct DB/API access, everything through databus.fetch()
"""

# ── Re-export all public symbols for backward compatibility ─────

from __future__ import annotations

# Import submodules to register routes on the router
from app.routers.x402_databus_tools import (
    analysis_tools,  # noqa: F401
    market_tools,  # noqa: F401
    wallet_tools,  # noqa: F401
)
from app.routers.x402_databus_tools.auth import _clean_response, verify_x402_payment
from app.routers.x402_databus_tools.models import (
    AddressDepthRequest,
    AddressRequest,
    GenericDataRequest,
    MintRequest,
    QueryRequest,
)
from app.routers.x402_databus_tools.pricing import X402_TOOL_PRICING
from app.routers.x402_databus_tools.rate_limit import (
    TRIAL_REDIS_PREFIX,
    TRIAL_WINDOW_SECONDS,
    check_trial,
    consume_trial,
)

# Re-export the combined router for app.mount()
from app.routers.x402_databus_tools.router import router

__all__ = [
    "TRIAL_REDIS_PREFIX",
    "TRIAL_WINDOW_SECONDS",
    "X402_TOOL_PRICING",
    "AddressDepthRequest",
    "AddressRequest",
    "GenericDataRequest",
    "MintRequest",
    "QueryRequest",
    "_clean_response",
    "check_trial",
    "consume_trial",
    "router",
    "verify_x402_payment",
]
