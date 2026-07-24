"""Social feed and news network — /news/feed, /news/social, etc."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.core.logging import get_logger

logger = get_logger("rmi_mcp_v3")
router = APIRouter(tags=["mcp_social"])

@router.get("/news/feed")
async def news_feed(
    category: str | None = None,
    sentiment: str | None = None,
    tier: int | None = None,
    sort: str = "latest",
    limit: int = 50,
    offset: int = 0,
    impact: str | None = None,
    source: str | None = None,
) -> Any:
    """Main news feed with all filters."""
    from app.caching_shield.news_network import fetch_all, get_feed
    await fetch_all(max_per_source=5)
    return get_feed(
        category=category or "", sentiment=sentiment or "",
        tier=tier or 0, sort=sort, limit=limit, offset=offset,
        impact=impact or "", source=source or "",
    )

@router.get("/news/categories")
async def news_categories() -> dict:
    from app.caching_shield.news_network import fetch_all, get_categories
    await fetch_all(max_per_source=3)
    return {"categories": get_categories()}

@router.post("/news/vote")
async def news_vote(data: dict) -> Any:
    from app.caching_shield.news_network import vote_article
    return vote_article(data.get("article_id", ""), data.get("direction", "up"))

@router.post("/news/comment")
async def news_comment(data: dict) -> Any:
    from app.caching_shield.news_network import add_comment
    return add_comment(data.get("article_id", ""), data.get("user", "anon"), data.get("text", ""))

@router.get("/news/comments/{article_id}")
async def news_comments(article_id: str) -> dict:
    from app.caching_shield.news_network import get_comments
    return {"comments": get_comments(article_id)}

@router.post("/news/bookmark")
async def news_bookmark(data: dict) -> Any:
    from app.caching_shield.news_network import bookmark
    return bookmark(data.get("article_id", ""))

@router.get("/news/search")
async def news_search(q: str = "", limit: int = 20) -> dict:
    from app.caching_shield.news_network import search_articles
    return {"results": search_articles(q, limit)}

@router.get("/news/daily-data")
async def daily_data() -> Any:
    from app.caching_shield.daily_data import get_daily_rundown_data
    return await get_daily_rundown_data()

@router.get("/news/social")
async def social_feed(limit_twitter: int = 30, limit_reddit: int = 20) -> Any:
    """Combined X/Twitter + Reddit crypto feed."""
    from app.caching_shield.social_feed import get_social_feed
    return await get_social_feed(limit_twitter, limit_reddit)

@router.get("/news/social/twitter")
async def twitter_feed(limit: int = 30) -> Any:
    """Top 50 crypto X/Twitter accounts."""
    from app.caching_shield.social_feed import get_twitter_feed
    return await get_twitter_feed(limit)

@router.get("/news/social/reddit")
async def reddit_feed(limit: int = 20) -> Any:
    """Top crypto subreddits."""
    from app.caching_shield.social_feed import get_reddit_feed
    return await get_reddit_feed(limit)

@router.get("/news/social/accounts")
async def social_accounts() -> dict:
    """List of monitored X accounts with profile pics."""
    from app.caching_shield.social_feed import get_top_accounts
    return {"accounts": get_top_accounts()}
