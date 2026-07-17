"""V1 entity resolution endpoints — /api/v1/entities/*.

Cross-wallet entity identification and clustering.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/entities", tags=["entities-v1"])


@router.post("/resolve")
async def resolve_entity(addresses: list[str], chain: str = "ethereum"):
    """Resolve wallet addresses to entities (clusters of related wallets).

    Uses entity clustering from app.entity_clustering.
    """
    raise HTTPException(
        status_code=501,
        detail="Entity resolution — uses app.entity_clustering + app.wallet_clustering. Planned for v5.1",
    )


@router.get("/{entity_id}")
async def get_entity(entity_id: str):
    """Get entity details and member wallets."""
    raise HTTPException(
        status_code=501,
        detail="Entity details endpoint — planned for v5.1",
    )


@router.get("/search")
async def search_entities(
    query: str = Query(..., description="Search term for entity name or address"),
):
    """Search for entities by name or associated address."""
    raise HTTPException(
        status_code=501,
        detail="Entity search — planned for v5.1",
    )
