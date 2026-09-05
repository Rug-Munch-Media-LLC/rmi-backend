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
    # Fault isolation: each sub-router mounts independently. A missing optional
    # dependency in one module (e.g. bot_admin importing the rugmunchbot repo,
    # which is not installed in every container) must not take down the whole
    # darkroom admin panel — observed 2026-08-23: ModuleNotFoundError on
    # 'rugmunchbot' silently disabled ALL /api/v1/admin/* routes.
    import importlib
    import logging

    logger = logging.getLogger("rmi_admin_router")

    for name, attr in [
        ("backend", "backend_router"),
        ("telegram", "telegram_router"),
        ("bot_admin", "bot_admin_router"),
        ("ghost", "ghost_router"),
        ("cron", "cron_router"),
        ("dify", "dify_router"),
        ("server", "server_router"),
        ("mail", "mail_router"),
        ("pg_stats", "pg_stats_router"),
    ]:
        try:
            module = importlib.import_module(f".{name}", __package__)
            router.include_router(getattr(module, attr))
        except Exception as exc:
            logger.warning("admin sub-router %s failed to mount: %s", name, exc)


_include_sub_routers()
