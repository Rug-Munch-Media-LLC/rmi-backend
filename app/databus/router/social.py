"""DataBus Router sub-module. Social endpoints."""

from fastapi import APIRouter, HTTPException, Query, Request
from typing import Any
from app.core.logging import get_logger
from app.databus.core import databus

logger = get_logger("databus.router.social")
router = APIRouter(tags=["databus"])

from app.databus.social import (
    X_DAILY_READ_BUDGET,
    X_FREE_MONTHLY_READ_LIMIT,
)

X_HANDLE = "CryptoRugMunch"


@router.get("/social/x/profile/{username}")
async def social_x_profile(username: str) -> dict[str, str]:
    """Get X/Twitter user profile - cached 24h."""
    from app.databus.social import SocialDataAggregator

    agg = SocialDataAggregator(databus.cache)
    result = await agg.x.get_user(username)
    if not result:
        raise HTTPException(404, f"User @{username} not found or API unavailable")
    return {"source": "x_twitter", "username": username, "data": result}

@router.get("/social/x/tweets/{username}")
async def social_x_tweets(username: str, count: int = Query(20, ge=1, le=100)) -> dict[str, str]:
    """Get recent tweets from user - cached 15min."""
    from app.databus.social import SocialDataAggregator

    agg = SocialDataAggregator(databus.cache)
    result = await agg.get_our_tweets(count=count) if username.lower() == "cryptorugmunch" else None
    if result is None:
        # Generic user lookup
        profile = await agg.x.get_user(username)
        if profile:
            result = await agg.x.get_user_tweets(profile["id"], max_results=count)
    if not result:
        raise HTTPException(404, f"Tweets for @{username} not available")
    return {"source": "x_twitter", "username": username, "count": len(result), "data": result}

@router.get("/social/x/mentions")
async def social_x_mentions(count: int = Query(20, ge=1, le=100)) -> dict[str, str]:
    """Get mentions of @CryptoRugMunch - cached 15min."""
    from app.databus.social import SocialDataAggregator

    agg = SocialDataAggregator(databus.cache)
    result = await agg.get_our_mentions(count=count)
    if not result:
        raise HTTPException(404, "Mentions not available (API budget exhausted)")
    return {"source": "x_twitter", "count": len(result), "data": result}

@router.get("/social/x/engagement")
async def social_x_engagement(tweet_ids: str = Query(..., description="Comma-separated tweet IDs")) -> dict[str, str]:
    """Get engagement metrics for tweets - cached 1h."""
    from app.databus.social import SocialDataAggregator

    agg = SocialDataAggregator(databus.cache)
    ids = [tid.strip() for tid in tweet_ids.split(",")[:100]]
    result = await agg.x.get_engagement_metrics(ids)
    return {"source": "x_twitter", "count": len(result), "data": result}

@router.get("/social/x/search")
async def social_x_search(
    q: str = Query(..., description="Search query"), count: int = Query(10, ge=1, le=100)
) -> dict[str, str]:
    """Search X/Twitter - VERY expensive, cached 24h. Use sparingly."""
    from app.databus.social import SocialDataAggregator

    agg = SocialDataAggregator(databus.cache)
    result = await agg.search_mentions(q, count=count)
    if not result:
        raise HTTPException(
            404, "Search results not available (API budget exhausted or no results)"
        )
    return {"source": "x_twitter", "query": q, "count": len(result), "data": result}

@router.get("/social/kol/{username}")
async def social_kol_reputation(username: str) -> Any:
    """KOL reputation score - cached 24h."""
    from app.databus.social import SocialDataAggregator

    agg = SocialDataAggregator(databus.cache)
    return await agg.get_kol_reputation(username)

@router.get("/social/sentiment")
async def social_sentiment(username: str = Query("CryptoRugMunch")) -> Any:
    """Brand sentiment analysis - cached 1h."""
    from app.databus.social import SocialDataAggregator

    agg = SocialDataAggregator(databus.cache)
    return await agg.get_sentiment(username)

@router.get("/social/x/budget")
async def social_x_budget() -> dict:
    """Current X API read budget status."""
    from app.databus.social import XTwitterProvider

    provider = XTwitterProvider(databus.cache)
    return {
        "daily_reads_used": provider._daily_reads,
        "daily_reads_remaining": X_DAILY_READ_BUDGET - provider._daily_reads,
        "monthly_free_limit": X_FREE_MONTHLY_READ_LIMIT,
        "daily_budget": X_DAILY_READ_BUDGET,
        "note": "X Free tier: 10k reads/month. Cache aggressively.",
    }

@router.get("/social/x/discover")
async def social_x_discover(handle: str = Query(X_HANDLE), limit: int = Query(50, ge=1, le=100)) -> dict[str, str]:
    """Discover tweets via web search - no API needed, works for free."""
    from app.databus.social_scraper import XWebScraper

    scraper = XWebScraper(databus.cache)
    tweets = await scraper.discover_tweets(handle=handle, limit=limit)
    return {"source": "web_search", "handle": handle, "count": len(tweets), "data": tweets}

@router.get("/social/x/engagement-report")
async def social_x_engagement_report(handle: str = Query(X_HANDLE)) -> Any:
    """Get engagement report for a handle - avg likes, best tweets, posting frequency."""
    from app.databus.social_scraper import XWebScraper

    scraper = XWebScraper(databus.cache)
    report = await scraper.get_engagement_report(handle=handle)
    return report

@router.get("/social/x/mentions-discover")
async def social_x_mentions_discover(
    handle: str = Query(X_HANDLE), limit: int = Query(20, ge=1, le=50)
) -> dict[str, str]:
    """Find tweets mentioning a handle - via web search."""
    from app.databus.social_scraper import XWebScraper

    scraper = XWebScraper(databus.cache)
    mentions = await scraper.get_mentions(handle=handle, limit=limit)
    return {"source": "web_search", "handle": handle, "count": len(mentions), "data": mentions}

@router.get("/social/x/trending")
async def social_x_trending() -> dict[str, str]:
    """Get current crypto trending topics from web search."""
    from app.databus.social_scraper import XWebScraper

    scraper = XWebScraper(databus.cache)
    topics = await scraper.get_trending_topics()
    return {"source": "web_search", "count": len(topics), "data": topics}

