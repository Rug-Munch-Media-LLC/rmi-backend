"""
Market Intelligence Routes Package
Aggregates all sub-routers into a single APIRouter with prefix /api/v1/market.
"""

from fastapi import APIRouter

from app.routers.market_intel.routes.hyperliquid import router as hyperliquid_router
from app.routers.market_intel.routes.market_data import router as market_data_router
from app.routers.market_intel.routes.overview import router as overview_router
from app.routers.market_intel.routes.premium import router as premium_router
from app.routers.market_intel.routes.scams import router as scams_router

router = APIRouter(prefix="/api/v1/market", tags=["market-intel"])
router.include_router(overview_router)
router.include_router(market_data_router)
router.include_router(scams_router)
router.include_router(premium_router)
router.include_router(hyperliquid_router)
