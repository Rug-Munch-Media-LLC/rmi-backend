"""News & market endpoints — /mcp/news, /mcp/news/summary, etc."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.core.logging import get_logger

logger = get_logger("rmi_mcp_v3")
router = APIRouter(tags=["mcp_news"])


@router.get("/mcp/news")
async def mcp_news_feed(
    category: str | None = None,
    sentiment: str | None = None,
    source: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Any:
    """Aggregated crypto news with sentiment analysis."""
    from app.caching_shield.market_rundown import get_articles

    return await get_articles(
        category=category or "",
        sentiment=sentiment or "",
        source=source or "",
        limit=limit,
        offset=offset,
    )


@router.get("/mcp/news/summary")
async def market_summary(force: bool = False) -> Any:
    """AI-generated daily market rundown (DeepSeek V4 Pro, cached 24h)."""
    from app.caching_shield.market_rundown import generate_market_summary

    return await generate_market_summary(force=force)


@router.get("/mcp/news/categories")
async def mcp_news_categories() -> dict[str, Any]:
    """Available news categories."""
    from app.caching_shield.market_rundown import get_categories

    return {"categories": get_categories()}


@router.get("/mcp/news/sources")
async def news_sources() -> dict[str, Any]:
    """Available news sources."""
    from app.caching_shield.market_rundown import get_sources

    return {"sources": get_sources()}


@router.post("/mcp/news/vote")
async def mcp_news_vote(data: dict[str, Any]) -> Any:
    """Vote up/down on an article."""
    from app.caching_shield.market_rundown import vote

    return await vote(data.get("article_id", ""), data.get("direction", "up"))


@router.post("/mcp/news/comment")
async def mcp_news_comment(data: dict[str, Any]) -> Any:
    """Add a comment to an article."""
    from app.caching_shield.market_rundown import comment

    return await comment(data.get("article_id", ""), data.get("user", "anon"), data.get("text", ""))


@router.get("/mcp/news/comments/{article_id}")
async def mcp_news_comments(article_id: str) -> Any:
    """Get comments for an article."""
    from app.caching_shield.market_rundown import get_comments

    return await get_comments(article_id)


@router.get("/mcp/daily-data")
async def daily_market_data() -> Any:
    """Enhanced daily data - price action, sentiment, security, whales, prediction markets."""
    from app.caching_shield.daily_data import get_daily_rundown_data

    return await get_daily_rundown_data()
