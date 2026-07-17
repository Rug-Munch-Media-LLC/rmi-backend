"""V1 version endpoint — /api/v1/version.

Delegates to the root /version handler.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter(tags=["version-v1"])


@router.get("/version")
async def version_v1():
    """Version info (redirect to root /version)."""
    return RedirectResponse(url="/version", status_code=307)
