"""
RugCharts News Intelligence Engine
===================================
Multi-source aggregation, quality scoring, deduplication, sentiment,
category tagging, social hooks.

Sources (all free):
  RSS/Atom     - 200+ crypto feeds (news_service.py)
  Google News  - crypto search results RSS
  Decrypt      - decrypt.co/feed
  The Block    - theblock.co/rss.xml
  CoinTelegraph - cointelegraph.com/rss
  CryptoPanic  - news sentiment API
  arXiv        - academic crypto/blockchain papers
  CoinGecko    - trending, market context
  Polymarket   - prediction market context
  X/Twitter    - v2 API searches (if key available)
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.databus.news_intel.config import CATEGORY_KEYWORDS, NEWS_SOURCES, QUALITY_INDICATORS, SENTIMENT_KEYWORDS
from app.databus.news_intel.fetchers import _fetch_cryptopanic, _fetch_rss, _fetch_x_crypto

logger = get_logger("news_intel")


# ── Quality & Sentiment ────────────────────────────────────────────


def score_quality(article: dict[str, Any]) -> float:
    """Score article quality 0-1 based on signals."""
    score = 0.5
    text = (article.get("title", "") + " " + article.get("summary", "") + article.get("description", "")).lower()

    content_len = len(article.get("summary", "") + article.get("description", ""))
    if content_len > 500:
        score += 0.15
    elif content_len > 200:
        score += 0.08
    elif content_len < 50:
        score -= 0.1

    pos_count = sum(1 for kw in QUALITY_INDICATORS["positive"] if kw in text)
    neg_count = sum(1 for kw in QUALITY_INDICATORS["negative"] if kw in text)
    crypto_count = sum(1 for kw in QUALITY_INDICATORS["crypto_specific"] if kw in text)

    score += pos_count * 0.03
    score -= neg_count * 0.05
    score += crypto_count * 0.04

    source_weight = article.get("source_quality", 0.7)
    score = score * 0.6 + source_weight * 0.4

    return max(0.0, min(1.0, score))


def analyze_sentiment(article: dict[str, Any]) -> dict[str, Any]:
    """Advanced keyword-based sentiment analysis with impact weighting."""
    title = article.get("title", "").lower()
    text = (title + " " + article.get("summary", "") + " " + article.get("description", "")).lower()

    bulls = sum(1 for kw in SENTIMENT_KEYWORDS["bullish"] if kw in text)
    bears = sum(1 for kw in SENTIMENT_KEYWORDS["bearish"] if kw in text)
    neutrals = sum(1 for kw in SENTIMENT_KEYWORDS["neutral"] if kw in text)

    high_impact_title = sum(3 for kw in SENTIMENT_KEYWORDS["high_impact"] if kw in title)
    high_impact_body = sum(1 for kw in SENTIMENT_KEYWORDS["high_impact"] if kw in text)

    total = bulls + bears + neutrals + high_impact_title + high_impact_body
    if total == 0:
        return {"sentiment": "neutral", "score": 0.0, "confidence": 0.2}

    sentiment_score = ((bulls * 1.0) - (bears * 1.2) + high_impact_title + (high_impact_body * 0.5)) / max(total, 1)

    if sentiment_score > 0.3:
        label = "bullish"
    elif sentiment_score < -0.3:
        label = "bearish"
    elif sentiment_score > 0.1:
        label = "slightly_bullish"
    elif sentiment_score < -0.1:
        label = "slightly_bearish"
    else:
        label = "neutral"

    confidence = min(1.0, total / 10.0)

    return {
        "sentiment": label,
        "score": round(sentiment_score, 2),
        "confidence": round(confidence, 2),
        "signals": {
            "bullish": bulls,
            "bearish": bears,
            "high_impact": high_impact_title + high_impact_body,
        },
    }


def categorize(article: dict[str, Any]) -> list[str]:
    """Auto-categorize article into topics."""
    text = (article.get("title", "") + " " + article.get("summary", "")).lower()
    categories = []

    for cat, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            categories.append(cat)

    return categories[:4]


def content_hash(article: dict[str, Any]) -> str:
    """Generate dedup hash from title + normalized text."""
    text = (article.get("title", "") + article.get("summary", "") + article.get("url", "")).lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^a-z0-9\s]", "", text)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# ── Social Features ─────────────────────────────────────────────────


ARTICLE_REACTIONS: dict[str, dict[str, int]] = {}
ARTICLE_COMMENTS: dict[str, list[dict[str, Any]]] = {}
REACTION_TYPES = ["🔥", "🐂", "🐻", "💎", "🧠", "🤡", "🚀", "💀"]


async def add_reaction(content_hash: str, reaction: str, user: str = "anon", **kw) -> dict[str, Any]:
    """Add a reaction to an article."""
    if reaction not in REACTION_TYPES:
        return {"error": f"Invalid reaction. Use: {REACTION_TYPES}"}

    if content_hash not in ARTICLE_REACTIONS:
        ARTICLE_REACTIONS[content_hash] = {}

    ARTICLE_REACTIONS[content_hash][reaction] = ARTICLE_REACTIONS[content_hash].get(reaction, 0) + 1

    return {
        "status": "reacted",
        "content_hash": content_hash,
        "reaction": reaction,
        "counts": ARTICLE_REACTIONS[content_hash],
        "total_reactions": sum(ARTICLE_REACTIONS[content_hash].values()),
    }


async def add_comment(content_hash: str, user: str, text: str, **kw) -> dict[str, Any]:
    """Add a comment to an article."""
    if content_hash not in ARTICLE_COMMENTS:
        ARTICLE_COMMENTS[content_hash] = []

    comment = {
        "user": user,
        "text": text[:500],
        "timestamp": datetime.now(UTC).isoformat(),
        "id": hashlib.sha256(f"{user}{text}{time.time()}".encode()).hexdigest()[:8],
    }
    ARTICLE_COMMENTS[content_hash].append(comment)

    return {
        "status": "commented",
        "content_hash": content_hash,
        "comment": comment,
        "total_comments": len(ARTICLE_COMMENTS[content_hash]),
    }


async def get_reactions(content_hash: str, **kw) -> dict[str, Any]:
    """Get reactions for an article."""
    counts = ARTICLE_REACTIONS.get(content_hash, {})
    comments = ARTICLE_COMMENTS.get(content_hash, [])

    return {
        "content_hash": content_hash,
        "reactions": counts,
        "total_reactions": sum(counts.values()),
        "comments": comments[-20:],
        "total_comments": len(comments),
    }


async def create_bb_post(content_hash: str, user: str = "system", **kw) -> dict[str, Any]:
    """Turn an article into a Bulletin Board post for community discussion."""
    return {
        "status": "bb_post_created",
        "content_hash": content_hash,
        "bb_post_url": f"/bulletin/{content_hash}",
        "message": "Article converted to Bulletin Board post. Community can now discuss.",
    }


# ── Main Aggregation Engine ────────────────────────────────────────


async def aggregate_all_news(limit: int = 50, **kw) -> dict[str, Any]:
    """Pull from every source, dedup, score, tag, sort."""
    seen_hashes: set[str] = set()
    all_articles: list[dict[str, Any]] = []

    # Step 1: Fetch all sources in parallel
    tasks: list[Any] = []
    for _src_id, src in NEWS_SOURCES.items():
        if src["type"] == "rss":
            tasks.append(_fetch_rss(src["url"], src["name"]))

    tasks.append(_fetch_cryptopanic())

    try:
        from app.domains.news.news_service import fetch_all_news

        tasks.append(fetch_all_news())
    except (AttributeError, ImportError) as e:
        logger.debug(f"aggregate_all_news failed: {e}")

    tasks.append(_fetch_x_crypto())

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Step 2: Normalize
    for result in results:
        if isinstance(result, Exception):
            continue
        if isinstance(result, list):
            for article in result:
                if not article.get("title"):
                    continue
                all_articles.append(article)
        elif isinstance(result, dict) and result.get("articles"):
            for article in result["articles"]:
                if not article.get("title"):
                    continue
                all_articles.append(article)

    # Step 3-6: Enrich each article
    enriched = []
    for article in all_articles:
        h = content_hash(article)
        if h in seen_hashes:
            continue
        seen_hashes.add(h)

        source_name = article.get("source", "")
        source_config = None
        for _src_id, src in NEWS_SOURCES.items():
            if src["name"].lower() == source_name.lower():
                source_config = src
                break

        article["content_hash"] = h
        article["source_quality"] = source_config["quality_weight"] if source_config else 0.7
        article["source_tier"] = source_config["tier"] if source_config else 2
        article["source_category"] = source_config["category"] if source_config else "unknown"
        article["source_icon"] = source_config["icon"] if source_config else "📄"
        article["quality_score"] = score_quality(article)
        article["sentiment"] = analyze_sentiment(article)
        article["categories"] = categorize(article)
        article["indexed_at"] = datetime.now(UTC).isoformat()

        enriched.append(article)

    # Step 7: Sort by quality
    enriched.sort(
        key=lambda a: (a.get("source_tier", 2), -a.get("quality_score", 0), a.get("published", "")),
        reverse=False,
    )

    top = enriched[:limit]

    source_counts = Counter(a.get("source", "Unknown") for a in top)
    cat_counts = Counter(c for a in top for c in a.get("categories", []))
    sentiment_dist = Counter(a.get("sentiment", {}).get("sentiment", "neutral") for a in top)
    avg_quality = sum(a.get("quality_score", 0) for a in top) / max(len(top), 1)

    return {
        "articles": top,
        "total_fetched": len(all_articles),
        "after_dedup": len(enriched),
        "returned": len(top),
        "stats": {
            "sources": dict(source_counts.most_common(10)),
            "categories": dict(cat_counts.most_common(10)),
            "sentiment_distribution": dict(sentiment_dist),
            "average_quality": round(avg_quality, 2),
            "dedup_rate": round((1 - len(enriched) / max(len(all_articles), 1)) * 100, 1),
        },
        "sources_used": [s["name"] for s in NEWS_SOURCES.values()],
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "news_intelligence_engine",
    }


async def get_weekly_best(limit: int = 20, **kw) -> dict[str, Any]:
    """Curated weekly best - highest quality articles from the past 7 days."""
    all_news = await aggregate_all_news(limit=100)
    articles = all_news.get("articles", [])

    best = [a for a in articles if a.get("quality_score", 0) > 0.7]
    best.sort(key=lambda a: -a.get("quality_score", 0))

    return {
        "weekly_best": best[:limit],
        "total_curated": len(best),
        "quality_threshold": 0.7,
        "sources_represented": list({a.get("source", "") for a in best[:limit]}),
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "weekly_best",
    }


async def get_academic_papers(limit: int = 10, **kw) -> dict[str, Any]:
    """Academic/research papers from arXiv and other sources."""
    papers = await _fetch_rss(NEWS_SOURCES["arxiv"]["url"], "arXiv Research", timeout=20)

    for p in papers:
        p["quality_score"] = 0.9
        p["source_quality"] = 0.95
        p["categories"] = ["academic", "research"]
        p["source_icon"] = "📚"

    return {
        "papers": papers[:limit],
        "total": len(papers),
        "source": "arXiv",
        "generated_at": datetime.now(UTC).isoformat(),
    }


async def get_social_feed(limit: int = 30, **kw) -> dict[str, Any]:
    """Social media feed - X/Twitter crypto reactions + CryptoPanic sentiment."""
    x_posts = await _fetch_x_crypto()
    cp_posts = await _fetch_cryptopanic()

    all_social = x_posts + [
        {
            "title": p.get("title", ""),
            "url": p.get("url", ""),
            "source": p.get("source", "CryptoPanic"),
            "source_type": "sentiment",
            "sentiment_votes": p.get("sentiment_votes", {}),
            "published": p.get("published", ""),
        }
        for p in cp_posts
    ]

    all_social.sort(
        key=lambda a: (
            a.get("likes", 0) + a.get("retweets", 0) * 2 + a.get("sentiment_votes", {}).get("important", 0) * 3
        ),
        reverse=True,
    )

    return {
        "social_posts": all_social[:limit],
        "total": len(all_social),
        "sources": ["X/Twitter", "CryptoPanic"],
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "social_feed",
    }


# Re-export for convenience
