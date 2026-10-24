"""Article engagement router — one mount point for reactions, comments, sentiment.

Included by ``app.routers.v1`` so every content surface gets the same
``/api/v1/articles/{article_id}/...`` contract.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.domains.engagement.comments_router import router as comments_router
from app.domains.engagement.reactions_router import router as reactions_router

router = APIRouter(prefix="/api/v1/articles", tags=["engagement"])
router.include_router(reactions_router)
router.include_router(comments_router)

__all__ = ["router"]
