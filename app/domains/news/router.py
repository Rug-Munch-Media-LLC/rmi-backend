"""T28 News Intelligence Product — HTTP router (aggregator).

Per v4.0 §T28. Splits the former single-file router (957 lines) by concern:

  app/domains/news/routes/
    schemas.py    — models, time-decay scoring, legacy row adapter, sanitizers
    articles.py   — list, trending, story-sources, single, analyze
    engagement.py — comments + reactions

This module is the aggregator: it mounts both sub-routers and re-exports the
public names so ``app.domains.news`` and external importers
(e.g. ``app.domains.reports.generator`` → ``_adapt_legacy_row``) keep working
unchanged.

Data sources:
  - crypto_news (legacy table, 1750+ items from RSS feeds)
  - news_items (new catalog table, populated by RSS ingest)
  - Qdrant embeddings for semantic search (rag_embedding_id)

Routes (unchanged):
  POST /api/v1/news              list, paginated, filterable
  GET  /api/v1/news/trending     time-decay weighted
  GET  /api/v1/news/stories/{cluster_id}/sources
  GET  /api/v1/articles/{news_id}    single item
  POST /api/v1/articles/{news_id}/analyze  LLM analysis (LiteLLM)
  POST /api/v1/news/{news_id}/react
  GET  /api/v1/news/{news_id}/reactions
  GET  /api/v1/news/{news_id}/comments
  POST /api/v1/news/{news_id}/comments
  DELETE /api/v1/news/comment/{comment_id}
  POST /api/v1/news/comment/{comment_id}/react
"""

from __future__ import annotations

from fastapi import APIRouter

from app.domains.news.routes.articles import (
    analyze_news,
    get_news,
    get_story_sources,
    list_news,
    router as articles_router,
    trending_news,
)
from app.domains.news.routes.engagement import (
    create_comment,
    delete_comment,
    get_news_reactions,
    list_comments,
    react_to_comment,
    react_to_news,
    router as engagement_router,
)
from app.domains.news.routes.schemas import (
    SOURCE_AUTHORITY,
    CommentCreate,
    CommentListResponse,
    CommentOut,
    CommentReactionResponse,
    NewsAnalysisResponse,
    NewsItemOut,
    NewsListResponse,
    ReactionRequest,
    ReactionsSummary,
    StorySourcesResponse,
    _adapt_legacy_row,
    _coerce_reaction,
    _sanitize_comment_body,
    recency_decay,
    source_authority,
    trend_score,
)

# Prefix is /api/v1 (not /api/v1/news) per Phase 2 Wave 2 (AUDIT-2026-Q3.md):
# the catch-all endpoints moved to /api/v1/articles/{news_id} so that
# app.routers.news can mount /api/v1/news/{feed,headlines,sources,...}
# without being shadowed by a {news_id} catch-all.
router = APIRouter(prefix="/api/v1", tags=["news"])

router.include_router(articles_router)
router.include_router(engagement_router)

__all__ = [
    "SOURCE_AUTHORITY",
    "CommentCreate",
    "CommentListResponse",
    "CommentOut",
    "CommentReactionResponse",
    "NewsAnalysisResponse",
    "NewsItemOut",
    "NewsListResponse",
    "ReactionRequest",
    "ReactionsSummary",
    "StorySourcesResponse",
    "_adapt_legacy_row",
    "_coerce_reaction",
    "_sanitize_comment_body",
    "analyze_news",
    "create_comment",
    "delete_comment",
    "get_news",
    "get_news_reactions",
    "get_story_sources",
    "list_comments",
    "list_news",
    "react_to_comment",
    "react_to_news",
    "recency_decay",
    "router",
    "source_authority",
    "trend_score",
    "trending_news",
]
