"""News admin sub-router: comments, scanner-alert injection, bot ingest.

POST /comment, GET /comments/{article_id}, POST /internal/scanner-alert,
POST /articles (bot batch push), POST /ingest (raw article).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends

from app.auth import require_public_profile
from app.core.auth import require_internal_key
from app.core.errors import ValidationError
from app.routers.news import _helpers as _h
from app.routers.news._helpers import (
    _COMMENTS,
    BotArticle,
    BotArticlesRequest,
    BotIngestRequest,
    CommentRequest,
    CommentResponse,
    _make_comment_id,
    _news_url_exists,
    _qdrant_available,
    _store_bot_article,
    _url_hash,
    logger,
    router as router,
)

# ─── Comments / Social ────────────────────────────────────────────


@router.post("/comment", response_model=CommentResponse)
async def post_comment(req: CommentRequest, user: dict[str, Any] = Depends(require_public_profile)) -> Any:
    """Post a comment on any news article. Requires public profile."""
    if not req.article_id or not req.content.strip():
        raise ValidationError("article_id and content required")

    comment = {
        "id": _make_comment_id(req.article_id, req.content),
        "article_id": req.article_id,
        "author": user.get("display_name") or user.get("email", "anon")[:50],
        "content": req.content[:2000],
        "created_at": datetime.now(UTC).isoformat(),
        "parent_id": req.parent_id,
        "likes": 0,
    }

    if req.article_id not in _COMMENTS:
        _COMMENTS[req.article_id] = []
    _COMMENTS[req.article_id].append(comment)

    return CommentResponse(**comment)


@router.get("/comments/{article_id}", response_model=list[CommentResponse])
async def get_comments(article_id: str) -> list[Any]:
    """Get comments for a specific article."""
    comments = _COMMENTS.get(article_id, [])
    return [CommentResponse(**c) for c in sorted(comments, key=lambda x: x["created_at"])]


# ─── Internal: for cron jobs to post scanner findings as news ─────


@router.post("/internal/scanner-alert")
async def post_scanner_alert(
    token_name: str, chain: str, risk_score: float, address: str, flags: str = ""
) -> dict[str, str]:
    """Internal endpoint for cron jobs to inject scanner findings into news feed."""
    content_hash = hashlib.sha256(f"internal:{address}:{chain}".encode()).hexdigest()
    article: dict[str, Any] = {
        "id": f"rmi-{content_hash[:12]}",
        "title": f"RMI Scanner: {token_name} ({chain.upper()}) - Risk {risk_score}/100",
        "url": f"https://rugmunch.io/scanner?address={address}&chain={chain}",
        "description": f"Scanner detected {token_name} on {chain}. Risk: {risk_score}/100. Flags: {flags}. Address: {address}",
        "source": "RMI Scanner",
        "published_at": datetime.now(UTC).isoformat(),
        "category": "security",
        "sentiment": "bearish" if risk_score > 50 else "neutral",
        "kind": "internal",
        "tier": "rmi",
        "risk_score": risk_score,
        "token_address": address,
        "chain": chain,
    }
    # Prepend to cache
    if _h._NEWS_CACHE:
        _h._NEWS_CACHE["articles"] = [article, *_h._NEWS_CACHE.get("articles", [])]
        _h._NEWS_CACHE["total"] = len(_h._NEWS_CACHE["articles"])
    return {"status": "injected", "id": article["id"]}


@router.post("/articles")
async def post_news_articles(
    req: BotArticlesRequest,
    _auth: dict[str, Any] = Depends(require_internal_key),
) -> dict[str, Any]:
    """Push a batch of articles from the bot.

    For each article: dedup against Qdrant news_v2 by URL hash; if new,
    rate + store; if duplicate, skip. Fail-open when Qdrant is down.
    """
    received = len(req.articles)
    if not req.articles:
        return {"received": 0, "stored": 0, "duplicates": 0}
    if not await _qdrant_available():
        logger.warning("news_push_qdrant_unavailable received=%d", received)
        return {"received": received, "stored": 0, "error": "qdrant unavailable"}

    stored = 0
    duplicates = 0
    for article in req.articles:
        if not article.url:
            continue
        try:
            if await _news_url_exists(_url_hash(article.url)):
                duplicates += 1
                continue
            if await _store_bot_article(article):
                stored += 1
            else:
                duplicates += 1  # store failed → not stored
        except Exception as e:
            logger.warning("news_article_push_failed url=%s err=%s", article.url, e)
            duplicates += 1
    return {"received": received, "stored": stored, "duplicates": duplicates}


@router.post("/ingest")
async def post_news_ingest(
    req: BotIngestRequest,
    _auth: dict[str, Any] = Depends(require_internal_key),
) -> dict[str, Any]:
    """Alternative bulk-ingest: raw article text + URL through the RAG pipeline.

    Same dedup + rating + store logic as POST /articles, but accepts a single
    raw article (title + content + URL) instead of a structured batch.
    """
    received = 1
    if not req.url:
        return {"received": received, "stored": 0, "duplicates": 0}
    if not await _qdrant_available():
        logger.warning("news_ingest_qdrant_unavailable url=%s", req.url)
        return {"received": received, "stored": 0, "error": "qdrant unavailable"}

    try:
        if await _news_url_exists(_url_hash(req.url)):
            return {"received": received, "stored": 0, "duplicates": 1}
        article = BotArticle(
            title=req.title,
            url=req.url,
            source=req.source,
            description=req.content or req.title,
            published_at=req.published_at,
            image_url=req.image_url,
            tokens_mentioned=req.tokens_mentioned,
        )
        if await _store_bot_article(article):
            return {"received": received, "stored": 1, "duplicates": 0}
        return {"received": received, "stored": 0, "duplicates": 0}
    except Exception as e:
        logger.warning("news_ingest_failed url=%s err=%s", req.url, e)
        return {"received": received, "stored": 0, "error": "qdrant unavailable"}
