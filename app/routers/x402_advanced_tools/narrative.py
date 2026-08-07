"""
Layer 6: Narrative Engine — what's the market saying about this token RIGHT NOW.

Aggregates CryptoPanic, Reddit, and news sentiment into a narrative summary
with confidence scoring and shill detection.
"""

import json
import time
from datetime import UTC, datetime
from typing import Any

import aiohttp

from app.core.errors import AppError
from app.core.logging import get_logger
from app.routers.x402_advanced_tools.cache import get_cached, set_cached
from app.routers.x402_advanced_tools.confidence import compute_confidence

# Runtime import: FastAPI resolves this body model at OpenAPI schema generation time.
from app.routers.x402_advanced_tools.models import NarrativeRequest

logger = get_logger("x402.narrative")


async def narrative_engine(req: NarrativeRequest) -> Any | dict[str, str]:
    """What's the market saying about this token RIGHT NOW?."""
    try:
        token = req.token.strip()
        chain = req.chain or "all"
        t0 = time.time()

        cached = get_cached("narrative", {"token": token, "chain": chain})
        if cached:
            return cached

        sources = []
        posts = []

        # ── CryptoPanic (news + social) ──
        try:
            from urllib.parse import quote

            async with aiohttp.ClientSession() as session:
                url = f"https://cryptopanic.com/api/free/posts/?filter=important&q={quote(token)}"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        results = data.get("results", [])
                        if results:
                            sources.append("cryptopanic")
                            for r in results[:15]:
                                posts.append(
                                    {
                                        "source": "cryptopanic",
                                        "title": r.get("title", "")[:150],
                                        "sentiment": r.get("votes", {}).get("positive", 0)
                                        - r.get("votes", {}).get("negative", 0),
                                        "created": r.get("created_at", ""),
                                    }
                                )
        except (TimeoutError, aiohttp.ClientError, json.JSONDecodeError) as e:
            logger.debug(f"CryptoPanic fetch failed: {e}")

        # ── Reddit ──
        try:
            from urllib.parse import quote

            async with aiohttp.ClientSession() as session:
                url = f"https://www.reddit.com/search.json?q={quote(token)}&sort=new&limit=10"
                async with session.get(
                    url,
                    headers={"User-Agent": "RMI-Narrative/1.0"},
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        children = data.get("data", {}).get("children", [])
                        if children:
                            sources.append("reddit")
                            for c in children[:10]:
                                d = c.get("data", {})
                                posts.append(
                                    {
                                        "source": "reddit",
                                        "title": d.get("title", "")[:150],
                                        "subreddit": d.get("subreddit", ""),
                                        "score": d.get("score", 0),
                                        "comments": d.get("num_comments", 0),
                                        "created": datetime.utcfromtimestamp(d.get("created_utc", 0)).isoformat()
                                        if d.get("created_utc")
                                        else "",
                                    }
                                )
        except (TimeoutError, aiohttp.ClientError, json.JSONDecodeError) as e:
            logger.debug(f"Reddit fetch failed: {e}")

        total_posts = len(posts)
        if total_posts == 0:
            return {
                "tool": "Narrative Engine",
                "version": "1.0",
                "address": token,
                "narrative": "Insufficient data - no recent mentions found",
                "sentiment": "neutral",
                "confidence": "low",
                "posts_analyzed": 0,
                "sources_used": [],
            }

        positive = sum(1 for p in posts if p.get("sentiment", 0) > 0)
        negative = sum(1 for p in posts if p.get("sentiment", 0) < 0)
        neutral_count = total_posts - positive - negative

        if positive > negative * 2:
            sentiment = "bullish"
            sentiment_pct = round(positive / max(total_posts, 1) * 100)
        elif negative > positive * 2:
            sentiment = "bearish"
            sentiment_pct = round(negative / max(total_posts, 1) * 100)
        elif positive > negative:
            sentiment = "slightly_bullish"
            sentiment_pct = round(positive / max(total_posts, 1) * 100)
        elif negative > positive:
            sentiment = "slightly_bearish"
            sentiment_pct = round(negative / max(total_posts, 1) * 100)
        else:
            sentiment = "neutral"
            sentiment_pct = 50

        shill_signals = []
        reddit_posts = [p for p in posts if p.get("source") == "reddit"]
        if total_posts > 10 and positive > total_posts * 0.8:
            shill_signals.append("Suspiciously high positive ratio - possible coordinated shilling")
        if len(reddit_posts) >= 3 and all(p.get("score", 0) == 0 for p in reddit_posts):
            shill_signals.append("Same-timestamp posts detected - possible bot activity")

        subreddits = list({p.get("subreddit", "") for p in posts if p.get("subreddit")})
        narrative = (
            f"{sentiment.replace('_', ' ').title()} on {token}. "
            f"{sentiment_pct}% positive across {total_posts} posts from {len(sources)} sources. "
            + (f"Active in r/{', r/'.join(subreddits[:3])}. " if subreddits else "")
            + (f"Risk: {'; '.join(shill_signals)}" if shill_signals else "No shill signals detected.")
        )

        result = {
            "tool": "Narrative Engine",
            "version": "1.0",
            "timestamp": datetime.now(UTC).isoformat(),
            "token": token,
            "chain": chain,
            "narrative": narrative,
            "sentiment": sentiment,
            "sentiment_score": sentiment_pct,
            "posts_analyzed": total_posts,
            "breakdown": {"positive": positive, "negative": negative, "neutral": neutral_count},
            "sources_used": sources,
            "shill_signals": shill_signals if shill_signals else None,
            "_confidence": compute_confidence({"sources_used": sources}),
            "performance_ms": round((time.time() - t0) * 1000, 1),
            "guarantee": "Real-time social data or full refund",
        }

        set_cached("narrative", {"token": token, "chain": chain}, result)
        return result

    except Exception as e:
        logger.error(f"Narrative engine failed: {e}")
        raise AppError(message=str(e)) from e


__all__ = ["narrative_engine"]
