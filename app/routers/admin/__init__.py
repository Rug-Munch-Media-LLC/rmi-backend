"""
Darkroom Admin Extensions Router
=================================
Backend endpoints for the darkroom admin panel, split into domain modules.
All endpoints require admin auth via X-Admin-Key header.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/v1/admin", tags=["admin-extensions"])


# ── Auth helper ──
async def verify_admin(request: Request) -> bool:
    """Verify admin access with API key."""
    admin_key = os.getenv("ADMIN_API_KEY")
    if not admin_key:
        raise RuntimeError("ADMIN_API_KEY environment variable is required")
    key = request.headers.get("X-Admin-Key", "")
    if key != admin_key:
        raise HTTPException(status_code=401, detail="Invalid admin key")
    return True


# Import sub-routers and include them
# (using lazy import pattern to avoid circular deps with router reference)


def _include_sub_routers():
    from .bot_admin import bot_admin_router
    from .cron import cron_router
    from .dify import dify_router
    from .ghost import ghost_router
    from .mail import mail_router
    from .server import server_router
    from .telegram import telegram_router

    router.include_router(telegram_router)
    router.include_router(bot_admin_router)
    router.include_router(ghost_router)
    router.include_router(cron_router)
    router.include_router(dify_router)
    router.include_router(server_router)
    router.include_router(mail_router)


_include_sub_routers()
