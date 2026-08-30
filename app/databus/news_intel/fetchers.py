"""
News Intelligence — Source Fetchers
"""

from __future__ import annotations

from typing import Any

import feedparser
import httpx

from app.core.logging import get_logger
from app.databus.vault import resolve_key

logger = get_logger("news_intel")


async def _fetch_rss(url: str, source_name: str, timeout: int = 15) -> list[dict[str, Any]]:
    """Fetch and parse an RSS/Atom feed."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(url, headers={"User-Agent": "RugCharts/1.0 News Bot"})
            if r.status_code != 200:
                return []

            feed = feedparser.parse(r.text)
            articles = []
            for entry in feed.entries[:20]:
                articles.append(
                    {
                        "title": entry.get("title", ""),
                        "url": entry.get("link", ""),
                        "summary": entry.get("summary", entry.get("description", "")),
                        "published": entry.get("published", entry.get("updated", "")),
                        "source": source_name,
                        "source_type": "rss",
                        "author": entry.get("author", ""),
                    }
                )
            return articles
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug("RSS fetch failed for %s: %s", source_name, e)
        return []


async def _fetch_cryptopanic() -> list[dict[str, Any]]:
    """Fetch from CryptoPanic API."""
    key = resolve_key("CRYPTOPANIC_API_KEY")
    if not key:
        return []

    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                "https://cryptopanic.com/api/v1/posts/",
                params={"auth_token": key, "kind": "news", "limit": 20},
            )
            if r.status_code != 200:
                return []

            data = r.json()
            articles = []
            for post in data.get("results", []):
                articles.append(
                    {
                        "title": post.get("title", ""),
                        "url": post.get("url", ""),
                        "summary": post.get("description", ""),
                        "published": post.get("published_at", post.get("created_at", "")),
                        "source": "CryptoPanic",
                        "source_type": "api",
                        "sentiment_votes": {
                            "bullish": post.get("votes", {}).get("positive", 0),
                            "bearish": post.get("votes", {}).get("negative", 0),
                            "important": post.get("votes", {}).get("important", 0),
                        },
                    }
                )
            return articles
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug("CryptoPanic failed: %s", e)
        return []


async def _fetch_x_crypto() -> list[dict[str, Any]]:
    """Fetch crypto news from X/Twitter search (if API key available)."""
    x_key = resolve_key("X_API_KEY") or resolve_key("TWITTER_BEARER_TOKEN")
    if not x_key:
        return []

    try:
        queries = [
            "crypto news -is:retweet -is:reply lang:en",
            "bitcoin ETF -is:retweet lang:en",
            "DeFi protocol -is:retweet lang:en",
        ]
        articles = []
        async with httpx.AsyncClient(timeout=15) as c:
            for q in queries[:2]:
                r = await c.get(
                    "https://api.twitter.com/2/tweets/search/recent",
                    headers={"Authorization": f"Bearer {x_key}"},
                    params={
                        "query": q,
                        "max_results": 10,
                        "tweet.fields": "created_at,public_metrics,author_id",
                        "expansions": "author_id",
                    },
                )
                if r.status_code == 200:
                    data = r.json()
                    users = {u["id"]: u.get("username", "") for u in data.get("includes", {}).get("users", [])}
                    for tweet in data.get("data", []):
                        metrics = tweet.get("public_metrics", {})
                        articles.append(
                            {
                                "title": tweet.get("text", "")[:120],
                                "url": f"https://x.com/i/web/status/{tweet['id']}",
                                "published": tweet.get("created_at", ""),
                                "source": f"@{users.get(tweet.get('author_id', ''), 'unknown')}",
                                "source_type": "x",
                                "likes": metrics.get("like_count", 0),
                                "retweets": metrics.get("retweet_count", 0),
                                "replies": metrics.get("reply_count", 0),
                            }
                        )
        return articles
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug("X fetch failed: %s", e)
        return []
