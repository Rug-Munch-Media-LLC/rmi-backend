"""Homepage routes - /, /version.

Minimal landing endpoints for the backend. Returns deployment metadata.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.factory import DESCRIPTION, TITLE, VERSION

router = APIRouter(tags=["homepage"])


class VersionResponse(BaseModel):
    """Backend version info."""

    version: str
    title: str
    description: str
    status: str = "operational"


class HomeResponse(BaseModel):
    """Landing page response."""

    service: str
    version: str
    docs: str = "/docs"
    openapi: str = "/openapi.json"
    health: str = "/health"
    metrics: str = "/metrics"


@router.get("/", response_model=HomeResponse)
async def home() -> HomeResponse:
    """Landing page - returns service metadata."""
    return HomeResponse(service=TITLE, version=VERSION)


@router.get("/version", response_model=VersionResponse)
async def version() -> VersionResponse:
    """Detailed version info for ops."""
    return VersionResponse(version=VERSION, title=TITLE, description=DESCRIPTION)
