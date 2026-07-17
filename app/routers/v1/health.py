"""V1 health endpoints — /api/v1/health, /api/v1/live, /api/v1/ready.

Delegates to the canonical health handler from app.core.health_route.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter(prefix="/health", tags=["health-v1"])


@router.get("/live")
async def live_v1():
    """Liveness check (redirect to root /live)."""
    return RedirectResponse(url="/live", status_code=307)


@router.get("/ready")
async def ready_v1():
    """Readiness check (redirect to root /ready)."""
    return RedirectResponse(url="/ready", status_code=307)


@router.get("")
async def health_v1():
    """Full health check (redirect to root /health)."""
    return RedirectResponse(url="/health", status_code=307)
