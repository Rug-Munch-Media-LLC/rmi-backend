"""
RMI x402 Payment Middleware — Backward-compat re-export.
Routes have been split into app/routers/x402_middleware/ package.
"""
from __future__ import annotations

from app.routers.x402_middleware import router

__all__ = ["router"]
