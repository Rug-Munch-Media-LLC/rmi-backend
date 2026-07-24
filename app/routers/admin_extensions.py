"""
Darkroom Admin Extensions Router — Backward-compat re-export.
Routes have been split into app/routers/admin/ package.
"""
from __future__ import annotations

from .admin import router, verify_admin
from .admin.telegram import telegram_router
from .admin.ghost import ghost_router
from .admin.cron import cron_router
from .admin.dify import dify_router
from .admin.server import server_router
from .admin.mail import mail_router

__all__ = ["router", "verify_admin"]
