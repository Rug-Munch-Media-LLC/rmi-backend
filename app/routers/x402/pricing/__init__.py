"""
x402 DataBus Pricing — catalog and trial tracking.

Provides backward-compatible re-exports:
    from app.routers.x402.pricing import X402_TOOL_PRICING
    from app.routers.x402.pricing import check_trial, consume_trial
    from app.routers.x402.pricing import TRIAL_REDIS_PREFIX, TRIAL_WINDOW_SECONDS

Architecture:
    catalog_manual.py       — Hand-curated BASIC, PREMIUM, ELITE tiers
    catalog_autogen_1.py    — Auto-generated DataBus chains (A-K)
    catalog_autogen_2.py    — Auto-generated DataBus chains (L-Z)
    trials.py               — Redis-based trial tracking
"""

# Combine pricing catalog from submodules
from __future__ import annotations
from app.routers.x402.pricing.catalog_manual import X402_TOOL_PRICING_MANUAL
from app.routers.x402.pricing.catalog_autogen_1 import X402_TOOL_PRICING_AUTOGEN_1
from app.routers.x402.pricing.catalog_autogen_2 import X402_TOOL_PRICING_AUTOGEN_2
from app.routers.x402.pricing.trials import (
    TRIAL_REDIS_PREFIX,
    TRIAL_WINDOW_SECONDS,
    check_trial,
    consume_trial,
)

X402_TOOL_PRICING: dict = {}
X402_TOOL_PRICING.update(X402_TOOL_PRICING_MANUAL)
X402_TOOL_PRICING.update(X402_TOOL_PRICING_AUTOGEN_1)
X402_TOOL_PRICING.update(X402_TOOL_PRICING_AUTOGEN_2)

__all__ = [
    "X402_TOOL_PRICING",
    "check_trial",
    "consume_trial",
    "TRIAL_REDIS_PREFIX",
    "TRIAL_WINDOW_SECONDS",
]
