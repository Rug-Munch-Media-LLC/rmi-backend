"""News Router - THE Crypto News Aggregator Feed (package).

200+ sources. Real data only. No fake articles.

Split into sub-routers (feed / search / admin) with shared helpers in
``_helpers.py``. This package re-exports the full historical surface of
the former ``app/routers/news.py`` module — tests and internal callers
patch/read attributes on ``app.routers.news`` directly, so every name
below must remain importable from here.

Endpoints:
  GET  /api/v1/news/feed                - Full aggregated feed with filters
  GET  /api/v1/news/rag-feed            - RAG-served feed (rugbrain + rerank)
  GET  /api/v1/news/articles            - Ground News-style rated articles
  GET  /api/v1/news/articles/:id        - Single article with full rating card
  GET  /api/v1/news/sources             - Active sources with reputation tiers
  GET  /api/v1/news/sentiment           - Real-time sentiment overview
  GET  /api/v1/news/headlines           - Top headlines only
  GET  /api/v1/news/categories          - Categories with counts
  GET  /api/v1/news/stats               - Source/category statistics
  POST /api/v1/news/comment             - Comment on article (social)
  GET  /api/v1/news/comments/:id        - Get comments for article
  POST /api/v1/news/internal/scanner-alert - Cron scanner injection
  POST /api/v1/news/articles            - Bot batch push (internal key)
  POST /api/v1/news/ingest              - Bot raw-article ingest (internal key)
"""

from __future__ import annotations

from fastapi import APIRouter

from app.routers.news import admin, feed, search
from app.routers.news._helpers import (
    _CACHE_TS,
    _CACHE_TTL,
    _COMMENTS,
    _NEWS_CACHE,
    BotArticle,
    BotArticlesRequest,
    BotIngestRequest,
    CommentRequest,
    CommentResponse,
    NewsArticle,
    NewsFeedResponse,
    NewsResponse,
    _count_corroborating,
    _is_own_source,
    _make_comment_id,
    _news_url_exists,
    _normalize_sentiment_label,
    _own_source_first,
    _point_to_article,
    _qdrant_available,
    _qdrant_match_filter,
    _rate_article_dict,
    _refresh_cache,
    _scroll_news_v2,
    _sort_articles_by,
    _store_bot_article,
    _url_hash,
    logger,
)
from app.routers.news.admin import (
    get_comments,
    post_comment,
    post_news_articles,
    post_news_ingest,
    post_scanner_alert,
)
from app.routers.news.feed import (
    _BRANDED_PLACEHOLDER_IMAGE,
    _COMMENTARY_CACHE,
    _IMAGE_BACKFILL_CACHE,
    _IMAGE_BACKFILL_MAX,
    _attach_commentary,
    _backfill_images,
    _commentary_cache_key,
    _generate_commentary,
    _log_ingest_latency,
    get_categories,
    get_headlines,
    get_news_feed,
    get_news_rag_feed,
    get_stats,
)
from app.routers.news.search import (
    get_article,
    get_news_articles,
    get_news_sources,
    get_sentiment,
)

router = APIRouter(prefix="/api/v1/news", tags=["news"])
router.include_router(feed.router)
router.include_router(search.router)
router.include_router(admin.router)

__all__ = [
    "_BRANDED_PLACEHOLDER_IMAGE",
    "_CACHE_TS",
    "_CACHE_TTL",
    "_COMMENTARY_CACHE",
    "_COMMENTS",
    "_IMAGE_BACKFILL_CACHE",
    "_IMAGE_BACKFILL_MAX",
    "_NEWS_CACHE",
    "BotArticle",
    "BotArticlesRequest",
    "BotIngestRequest",
    "CommentRequest",
    "CommentResponse",
    # models
    "NewsArticle",
    "NewsFeedResponse",
    "NewsResponse",
    "RagFeedResponse",
    "_attach_commentary",
    "_backfill_images",
    "_commentary_cache_key",
    "_count_corroborating",
    "_generate_commentary",
    "_is_own_source",
    "_log_ingest_latency",
    "_make_comment_id",
    "_news_url_exists",
    "_normalize_sentiment_label",
    "_own_source_first",
    "_point_to_article",
    "_qdrant_available",
    "_qdrant_match_filter",
    "_rate_article_dict",
    "_refresh_cache",
    # helpers (patch targets / external consumers)
    "_scroll_news_v2",
    "_sort_articles_by",
    "_store_bot_article",
    "_url_hash",
    "admin",
    "feed",
    "get_article",
    "get_categories",
    "get_comments",
    "get_headlines",
    # search routes
    "get_news_articles",
    # feed routes
    "get_news_feed",
    "get_news_rag_feed",
    "get_news_sources",
    "get_sentiment",
    "get_stats",
    "logger",
    # admin routes
    "post_comment",
    "post_news_articles",
    "post_news_ingest",
    "post_scanner_alert",
    "router",
    "search",
]
