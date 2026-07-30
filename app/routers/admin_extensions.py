"""
Darkroom Admin Extensions Router — Backward-compat re-export.
Routes have been split into app/routers/admin/ package.
"""

from __future__ import annotations

from .admin import router, verify_admin

__all__ = ["router", "verify_admin"]
