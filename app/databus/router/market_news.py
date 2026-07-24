"""DataBus Router sub-module. Market_News endpoints."""

from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query, Request
from typing import Any
from app.core.logging import get_logger
from app.databus.core import databus

logger = get_logger("databus.router.market_news")
router = APIRouter(tags=["databus"])

@router.get("/market/prices")
async def market_prices(coins: str = "bitcoin,ethereum,solana") -> Any:
    """Live prices from CoinGecko. Free, no key needed."""
    return await databus.fetch("live_prices", coins=coins)

@router.get("/market/fear-greed")
async def fear_greed() -> Any:
    """Crypto Fear & Greed Index. Free, no key needed."""
    return await databus.fetch("fear_greed")

@router.get("/market/trending")
async def market_trending() -> Any:
    """Trending coins from CoinGecko."""
    return await databus.fetch("trending_coins")

@router.get("/market/brief")
async def market_brief() -> Any:
    """One-call market overview: prices + fear/greed + trending + predictions."""
    return await databus.fetch("market_brief")

@router.get("/news/full")
async def full_news(limit: int = 15) -> Any:
    """Complete news feed: headlines + market data + fear/greed + polymarket."""
    return await databus.fetch("full_news", limit=limit)

@router.get("/news/intel")
async def news_intel(limit: int = 30) -> Any:
    """Complete news intelligence - 10+ sources, quality-scored, deduped, sentiment-tagged."""
    return await databus.fetch("news_intel", limit=limit)

@router.get("/news/weekly-best")
async def weekly_best(limit: int = 20) -> Any:
    """Curated weekly best - highest quality crypto journalism."""
    return await databus.fetch("weekly_best", limit=limit)

@router.get("/news/academic")
async def academic_papers(limit: int = 10) -> Any:
    """Academic crypto/blockchain research papers from arXiv."""
    return await databus.fetch("academic_papers", limit=limit)

@router.get("/news/social")
async def social_feed(limit: int = 30) -> Any:
    """Crypto social feed - X/Twitter + CryptoPanic sentiment."""
    return await databus.fetch("social_feed", limit=limit)

@router.post("/news/react/{content_hash}")
async def react_article(content_hash: str, request: Request) -> Any:
    """React to article: 🔥🐂🐻💎🧠🤡🚀💀"""
    body = await request.json()
    return await databus.fetch(
        "article_reactions",
        content_hash=content_hash,
        reaction=body.get("reaction", "🔥"),
        user=body.get("user", "anon"),
    )

@router.get("/news/reactions/{content_hash}")
async def get_article_reactions(content_hash: str) -> Any:
    """Get reactions and comments for an article."""
    return await databus.fetch("article_reactions", content_hash=content_hash)

@router.post("/news/comment/{content_hash}")
async def comment_article(content_hash: str, request: Request) -> Any:
    """Comment on an article."""
    body = await request.json()
    return await databus.fetch(
        "article_comments",
        content_hash=content_hash,
        user=body.get("user", "anon"),
        text=body.get("text", ""),
    )

@router.post("/news/bb-post/{content_hash}")
async def create_bb_from_article(content_hash: str, request: Request) -> Any:
    """Turn article into Bulletin Board post."""
    body = await request.json() if request.headers.get("content-type") == "application/json" else {}
    return await databus.fetch(
        "bb_post", content_hash=content_hash, user=body.get("user", "system")
    )

@router.get("/news/ct-rundown")
async def ct_rundown(limit: int = 20) -> Any:
    """CT Rundown - top 20 Crypto Twitter stories, AI-summarized, category-diverse."""
    return await databus.fetch("ct_rundown", limit=limit)

@router.get("/news/ct-accounts")
async def ct_accounts() -> Any:
    """Curated CT account list - 150+ top accounts across 6 tiers."""
    return await databus.fetch("ct_accounts")

@router.get("/social/kol/{handle}")
async def kol_profile(handle: str) -> Any:
    """KOL performance profile - trust score, call history, win rate."""
    return await databus.fetch("kol_profile", handle=handle)

@router.get("/social/kol-leaderboard")
async def kol_leaderboard(limit: int = 20) -> Any:
    """KOL leaderboard - ranked by trust score and accuracy."""
    return await databus.fetch("kol_leaderboard", limit=limit)

@router.get("/social/shill-alerts")
async def shill_alerts() -> Any:
    """Active shill campaigns - coordinated promotion, pump-and-dump patterns."""
    return await databus.fetch("shill_detector")

@router.get("/social/scam-monitor")
async def scam_monitor() -> Any:
    """Scam channel monitoring - Telegram/Discord scam pattern detection."""
    return await databus.fetch("scam_monitor")

@router.get("/social/metrics")
async def social_metrics() -> Any:
    """Social metrics - trending topics, sentiment, KOL activity."""
    return await databus.fetch("social_metrics")

@router.get("/intel/daily")
async def daily_intel() -> Any:
    """Daily Intelligence Report - Groq AI-powered market briefing with all data sources."""
    return await databus.fetch("daily_intel")

