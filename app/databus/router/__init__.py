"""DataBus Router — unified API endpoints."""

from fastapi import APIRouter

from app.databus.router.admin import router as admin_router
from app.databus.router.batch_cache import router as batch_cache_router
from app.databus.router.bitquery import router as bitquery_router
from app.databus.router.convenience import router as convenience_router
from app.databus.router.core import router as core_router
from app.databus.router.market_news import router as market_news_router
from app.databus.router.premium import router as premium_router
from app.databus.router.social import router as social_router
from app.databus.router.webhooks import router as webhooks_router

router = APIRouter(prefix="/api/v1/databus", tags=["databus"])
router.include_router(core_router)
router.include_router(admin_router)
router.include_router(convenience_router)
router.include_router(batch_cache_router)
router.include_router(social_router)
router.include_router(bitquery_router)
router.include_router(webhooks_router)
router.include_router(premium_router)
router.include_router(market_news_router)

__all__ = ["router"]
