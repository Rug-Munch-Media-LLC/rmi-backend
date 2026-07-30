"""
Intelligence Router Package
Aggregates all sub-routers into a single APIRouter with prefix /api/v1/intelligence.
"""

from fastapi import APIRouter

from app.routers.intelligence_router.cards_marketing import router as cards_marketing_router
from app.routers.intelligence_router.dashboard import router as dashboard_router
from app.routers.intelligence_router.queries import router as queries_router
from app.routers.intelligence_router.receivers import router as receivers_router

router = APIRouter(prefix="/api/v1/intelligence", tags=["intelligence"])
router.include_router(receivers_router)
router.include_router(queries_router)
router.include_router(dashboard_router)
router.include_router(cards_marketing_router)

__all__ = ["router"]
