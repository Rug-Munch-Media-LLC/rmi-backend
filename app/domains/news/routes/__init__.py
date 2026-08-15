"""News domain HTTP route modules.

Split by concern (2026-08-14):
  - schemas.py    — shared Pydantic models, scoring helpers, row adapter
  - articles.py   — article browsing: list, trending, single, analyze, story sources
  - engagement.py — comments + reactions
The aggregator lives in ``app.domains.news.router``.
"""

from app.domains.news.routes.articles import router as articles_router
from app.domains.news.routes.engagement import router as engagement_router

__all__ = ["articles_router", "engagement_router"]
