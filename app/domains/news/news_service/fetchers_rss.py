"""RSS and Reddit fetcher functions."""
from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import feedparser
import httpx

from app.core.logging import get_logger
from app.domains.news.news_service.sources import (
    PROXY_REQUIRED_FEEDS,
    REDDIT_SUBREDDITS,
    REDDIT_USER_AGENT,
    RSS_FEEDS,
    _get_proxy,
)

if TYPE_CHECKING:
    from app.domains.news.news_service.service import NewsService

logger = get_logger(__name__)

async def fetch_rss(svc: NewsService, url: str, source_name: str, limit: int = 5, use_proxy: bool = False) -> list[dict[str, Any]]:
    needs_proxy = use_proxy or url in PROXY_REQUIRED_FEEDS
    for attempt in range(3 if needs_proxy else 1):
        try:
            client_kwargs = {"timeout": 12.0, "follow_redirects": True}
            if needs_proxy and attempt > 0:
                proxy = _get_proxy()
                if proxy:
                    client_kwargs["proxy"] = proxy
            async with httpx.AsyncClient(**client_kwargs) as client:
                headers = {"User-Agent": "Mozilla/5.0 (compatible; RMI-NewsBot/3.0; +https://rugmunch.io)"}
                if needs_proxy:
                    headers.update({"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                                    "Accept-Language": "en-US,en;q=0.5", "DNT": "1"})
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    break
                elif needs_proxy and attempt < 2:
                    await asyncio.sleep(0.5)
                    continue
                return []
        except (httpx.HTTPError, TimeoutError, OSError) as e:
            logger.debug(f"RSS fetch failed: {e}")
            if needs_proxy and attempt < 2:
                await asyncio.sleep(0.5)
                continue
            return []
    else:
        return []

    try:
        feed = feedparser.parse(resp.text)
    except (httpx.HTTPError, requests.RequestException) as e :
        logger.debug("fetch_rss_error", exc_info=True)
        logger.debug(f"Feed parse failed: {e}")
        return []

    articles = []
    for entry in feed.entries[:limit]:
        try:
            title = entry.get("title", "")
            link = entry.get("link", "")
            desc = entry.get("summary", "") or entry.get("description", "") or title
            pub_parsed = entry.get("published_parsed") or entry.get("updated_parsed")
            if pub_parsed and isinstance(pub_parsed, (tuple, list)) and len(pub_parsed) >= 6:
                published = datetime(*pub_parsed[:6], tzinfo=UTC).isoformat()
                pub_dt = datetime(*pub_parsed[:6])
                if datetime.now(UTC) - pub_dt > timedelta(hours=72):
                    continue
            else:
                published = datetime.now(UTC).isoformat()
            content_hash = hashlib.sha256(f"rss:{title}:{link}".encode()).hexdigest()
            if content_hash in svc.seen_hashes:
                continue
            svc.seen_hashes.add(content_hash)
            tfa = f"{title} {desc}"
            articles.append({
                "id": f"rss-{content_hash[:12]}", "title": title, "url": link,
                "description": desc[:300], "source": source_name, "published_at": published,
                "category": svc._categorize(tfa), "sentiment": svc._analyze_sentiment(tfa),
                "kind": "external", "tier": "news",
            })
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            continue
    if articles:
        logger.info(f"RSS {source_name}: {len(articles)} articles")
    return articles

async def fetch_all_rss(svc: NewsService, limit_per_source: int = 3) -> list[dict[str, Any]]:
    all_articles = []
    for i in range(0, len(RSS_FEEDS), 20):
        batch = RSS_FEEDS[i:i + 20]
        results = await asyncio.gather(*[fetch_rss(svc, url, n, limit_per_source) for url, n in batch], return_exceptions=True)
        for r in results:
            if isinstance(r, list): all_articles.extend(r)
        if i + 20 < len(RSS_FEEDS):
            await asyncio.sleep(0.3)
    return all_articles

async def fetch_reddit_hot(svc: NewsService, subreddit: str, limit: int = 5) -> list[dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"https://www.reddit.com/r/{subreddit}/hot.json",
                                    params={"limit": limit, "raw_json": "1"},
                                    headers={"User-Agent": REDDIT_USER_AGENT})
            if resp.status_code != 200: return []
            data = resp.json()
        articles = []
        for post in data.get("data", {}).get("children", []):
            pd = post.get("data", {})
            title, permalink = pd.get("title", ""), pd.get("permalink", "")
            url = f"https://reddit.com{permalink}" if permalink else ""
            text = (pd.get("selftext", "") or "")[:300]
            created = datetime.fromtimestamp(pd.get("created_utc", 0), tz=UTC).isoformat()
            if pd.get("score", 0) < 5: continue
            ch = hashlib.sha256(f"reddit:{title}:{permalink}".encode()).hexdigest()
            if ch in svc.seen_hashes: continue
            svc.seen_hashes.add(ch)
            tfa = f"{title} {text}"
            articles.append({
                "id": f"reddit-{ch[:12]}", "title": f"r/{subreddit}: {title}", "url": url,
                "description": text[:250], "source": f"Reddit r/{subreddit}", "published_at": created,
                "category": svc._categorize_reddit(subreddit, tfa),
                "sentiment": svc._analyze_sentiment(tfa),
                "kind": "social", "tier": "social",
                "reddit_score": pd.get("score", 0), "reddit_comments": pd.get("num_comments", 0),
            })
        if articles: logger.info(f"Reddit r/{subreddit}: {len(articles)} posts")
        return articles
    except (httpx.HTTPError, requests.RequestException, json.JSONDecodeError, ValueError, TypeError, KeyError, ConnectionError, OSError, TimeoutError) as e :
        logger.debug("fetch_reddit_hot_error", exc_info=True)
        logger.debug(f"Reddit fetch failed: {e}")
        return []

async def fetch_all_reddit(svc: NewsService) -> list[dict[str, Any]]:
    results = await asyncio.gather(*[fetch_reddit_hot(svc, sub, 5) for sub in REDDIT_SUBREDDITS], return_exceptions=True)
    return [a for r in results if isinstance(r, list) for a in r]
