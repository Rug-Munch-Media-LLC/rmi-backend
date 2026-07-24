"""V1 entity resolution endpoints — /api/v1/entities/*.

Cross-wallet entity identification and clustering.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.errors import ServiceUnavailableError

router = APIRouter(prefix="/entities", tags=["entities-v1"])


@router.post("/resolve")
async def resolve_entity(addresses: list[str], chain: str = "ethereum") -> None:
    """Resolve wallet addresses to entities (clusters of related wallets).

    Uses entity clustering from app.entity_clustering.
    """
    raise ServiceUnavailableError(
        "Entity resolution — planned: POST /api/v1/entities/resolve — see roadmap",
    )


@router.get("/{entity_id}")
async def get_entity(entity_id: str) -> None:
    """Get entity details and member wallets."""
    raise ServiceUnavailableError(
        "Entity details endpoint — planned: GET /api/v1/entities/{entity_id} — see roadmap",
    )


@router.get("/search")
async def search_entities(
    query: str = Query(..., description="Search term for entity name or address"),
) -> None:
    """Search for entities by name or associated address."""
    raise ServiceUnavailableError(
        "Entity search — planned: GET /api/v1/entities/search — see roadmap",
    )
