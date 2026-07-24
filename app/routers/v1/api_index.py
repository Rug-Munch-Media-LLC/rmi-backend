"""API version index — /api (version listing).

Returns all available API versions for client discovery.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["api-index"])

@router.get("/api")
async def api_index() -> dict[str, list[str]]:
    """List all available API versions."""
    return {
        "versions": ["v1", "v2"],
        "latest": "v1",
        "deprecated": [],
        "legacy_root_level_routes": [
            "These routes are being migrated from root to /api/v1/",
            "See https://docs.rugmunch.io/api/versioning for migration guide",
        ],
    }
