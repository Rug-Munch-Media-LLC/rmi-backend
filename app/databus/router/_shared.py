"""Shared utilities for DataBus router sub-modules."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from app.core.logging import get_logger

if TYPE_CHECKING:
    from fastapi import Request

logger = get_logger("databus.router")

def verify_admin(request: Request) -> bool:
    """Verify admin API key from request headers.

    Args:
        request: FastAPI request with X-Admin-Key header

    Returns:
        True if admin key is valid, False otherwise
    """
    admin_key = os.getenv("ADMIN_API_KEY", "")
    if not admin_key:
        logger.critical("ADMIN_API_KEY not configured — admin endpoints disabled")
        return False
    provided = request.headers.get("X-Admin-Key", "")
    if provided != admin_key:
        logger.warning("admin_auth_failed: invalid X-Admin-Key")
        return False
    return True
