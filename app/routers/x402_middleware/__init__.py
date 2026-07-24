"""
RMI x402 Payment Middleware — Modular Split
=============================================
Components: config, verifier, routes
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/x402", tags=["x402-payment-middleware"])

# ── x402 SDK Availability ──────────────────────────────────────
try:
    from x402 import X402_VERSION  # noqa: F401
    X402_SDK_AVAILABLE = True
except ImportError:
    X402_SDK_AVAILABLE = False

# Import sub-modules (they register routes on `router`)
from . import routes  # noqa: F401, E402
