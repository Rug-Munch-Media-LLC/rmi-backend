"""
X/Twitter Social Intelligence via Web Scraping
================================================
Uses web_search + web_extract for tweet discovery and content.
No API credits needed. Runs as a cron job every 6 hours.

Cache strategy:
  - Tweet text: cached 24h (doesn't change)
  - Engagement metrics: cached 1h (changes frequently)
  - Profile data: cached 24h
  - Sentiment/analysis: cached 6h

Cron: Every 6 hours, discover new tweets, extract content, update metrics.
"""

from datetime import UTC, datetime

from app.core.logging import get_logger
from app.databus.cache import CacheLayer, get_cache

logger = get_logger("databus.social_scraper")

# Twitter handle for our account
OUR_HANDLE = "CryptoRugMunch"
OUR_USER_ID = "1771377421117169668"  # @CryptoRugMunch

# Cache TTLs
CACHE_TTL_TWEET = 86400  # 24h - tweet text doesn't change
CACHE_TTL_METRICS = 3600  # 1h - engagement changes
CACHE_TTL_PROFILE = 86400  # 24h
CACHE_TTL_DISCOVERY = 21600  # 6h - new tweet discovery


class XWebScraper:
    """
    X/Twitter data via web search + extract. No API needed.

    Uses:
      - web_search: discover tweets by keyword/from:handle
      - web_extract: pull full tweet content from URLs
      - DataBus cache: dedup and TTL management

    Designed to run as a cron job every 6 hours.
    """

    def __init__(self, cache: CacheLayer = None):
        self.cache = cache or get_cache()

    async def discover_tweets(
        self, handle: str = OUR_HANDLE, since_date: str | None = None, limit: int = 50
    ) -> list[dict]:
        """
        Discover tweets from @handle using web_search.
        Returns list of {id, url, text_snippet, date, source}.
        """
        cache_key = f"social:x:discovery:{handle}:{since_date or 'latest'}"

        # Check cache first
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        # Import here to avoid circular imports in module scope
        from hermes_tools import web_extract, web_search

        all_tweets = {}
        queries = [
            f"from:{handle}",
            f"site:x.com/{handle} 2026",
            f"site:x.com/{handle} status",
        ]

        if since_date:
            queries.append(f"from:{handle} since:{since_date}")

        for q in queries:
            try:
                result = web_search(q, limit=10)
                for item in result.get("data", {}).get("web", []):
                    url = item.get("url", "")
                    desc = item.get("description", "")
                    title = item.get("title", "")

                    # Extract tweet ID from URL
                    tweet_id = url.split("/")[-1] if "/" in url else ""
                    if not tweet_id.isdigit():
                        continue

                    if tweet_id not in all_tweets:
                        all_tweets[tweet_id] = {
                            "id": tweet_id,
                            "url": url,
                            "title": title,
                            "description": desc,
                            "discovered_at": datetime.now(UTC).isoformat(),
                        }
            except Exception as e:
                logger.warning(f"Search error for '{q}': {e}")
                continue

        # Extract full content from discovered tweets
        tweet_urls = [
            t["url"]
            for t in all_tweets.values()
            if "CryptoRugMunch/status/" in t["url"]
            or "twitter.com/CryptoRugMunch/status/" in t["url"]
        ]

        if tweet_urls:
            for i in range(0, len(tweet_urls), 5):
                batch = tweet_urls[i : i + 5]
                try:
                    results = web_extract(batch)
                    for r in results.get("results", []):
                        if r.get("content"):
                            url = r.get("url", "")
                            tweet_id = url.split("/")[-1] if "/" in url else ""
                            if tweet_id in all_tweets:
                                all_tweets[tweet_id]["full_text"] = r["content"][:2000]
                                all_tweets[tweet_id]["extracted_at"] = datetime.now(UTC).isoformat()
                except Exception as e:
                    logger.warning(f"Extract error: {e}")
                    continue

        tweets = list(all_tweets.values())

        # Cache the discovery results
        await self.cache.set(cache_key, tweets, ttl=CACHE_TTL_DISCOVERY)

        # Cache individual tweets
        for tweet in tweets:
            await self.cache.set(f"social:x:tweet:{tweet['id']}", tweet, ttl=CACHE_TTL_TWEET)

        logger.info(f"Discovered {len(tweets)} tweets for @{handle}")
        return tweets

    async def get_profile(self, handle: str = OUR_HANDLE) -> dict | None:
        """
        Get profile data via web search. Returns cached if available.
        """
        cache_key = f"social:x:profile:{handle}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        from hermes_tools import web_search

        try:
            result = web_search(f"@{handle} twitter profile followers", limit=5)
            for item in result.get("data", {}).get("web", []):
                desc = item.get("description", "")
                if handle.lower() in desc.lower() and "follower" in desc.lower():
                    # Extract follower count from description
                    import re

                    match = re.search(r"(\d[\d,]+)\s+follower", desc)
                    followers = int(match.group(1).replace(",", "")) if match else None

                    profile = {
                        "handle": handle,
                        "followers": followers,
                        "source_url": item.get("url", ""),
                        "description": desc,
                        "updated_at": datetime.now(UTC).isoformat(),
                    }
                    await self.cache.set(cache_key, profile, ttl=CACHE_TTL_PROFILE)
                    return profile
        except Exception as e:
            logger.warning(f"Profile search error: {e}")

        return None

    async def get_mentions(self, handle: str = OUR_HANDLE, limit: int = 20) -> list[dict]:
        """
        Find tweets mentioning @handle.
        """
        cache_key = f"social:x:mentions:{handle}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        from hermes_tools import web_search

        mentions = []
        try:
            result = web_search(f"@{handle} -from:{handle}", limit=limit)
            for item in result.get("data", {}).get("web", []):
                url = item.get("url", "")
                if "/status/" in url and handle.lower() not in url.lower().split("/status/")[0]:
                    mentions.append(
                        {
                            "url": url,
                            "title": item.get("title", ""),
                            "description": item.get("description", ""),
                            "discovered_at": datetime.now(UTC).isoformat(),
                        }
                    )
        except Exception as e:
            logger.warning(f"Mentions search error: {e}")

        await self.cache.set(cache_key, mentions, ttl=CACHE_TTL_METRICS)
        return mentions

    async def get_trending_topics(self) -> list[dict]:
        """Get current crypto trending topics via web search."""
        cache_key = "social:x:trending:crypto"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        from hermes_tools import web_search

        topics = []
        searches = [
            "crypto rug pull trending today",
            "crypto scam alert today 2026",
            "cryptocurrency security news",
        ]

        for q in searches:
            try:
                result = web_search(q, limit=5)
                for item in result.get("data", {}).get("web", []):
                    topics.append(
                        {
                            "query": q,
                            "title": item.get("title", ""),
                            "url": item.get("url", ""),
                            "description": item.get("description", "")[:200],
                            "discovered_at": datetime.now(UTC).isoformat(),
                        }
                    )
            except Exception:
                continue

        await self.cache.set(cache_key, topics, ttl=CACHE_TTL_METRICS)
        return topics

    async def get_engagement_report(self, handle: str = OUR_HANDLE) -> dict:
        """
        Generate an engagement report based on discovered tweets.
        Computes avg likes, best performing tweets, posting frequency.
        """
        tweets = await self.discover_tweets(handle)
        if not tweets:
            return {"error": "No tweets discovered"}

        # Extract engagement metrics from descriptions
        import re

        total_likes = 0
        total_replies = 0
        tweets_with_metrics = 0

        for t in tweets:
            desc = (t or {}).get("description", "")
            likes_match = re.search(r"(\d+)\s+likes?", desc)
            replies_match = re.search(r"(\d+)\s+repl(?:ies|y)", desc)

            if likes_match:
                total_likes += int(likes_match.group(1))
                tweets_with_metrics += 1
            if replies_match:
                total_replies += int(replies_match.group(1))

        avg_likes = total_likes / max(1, tweets_with_metrics)

        report = {
            "handle": handle,
            "total_tweets_discovered": len(tweets),
            "tweets_with_metrics": tweets_with_metrics,
            "total_likes": total_likes,
            "total_replies": total_replies,
            "avg_likes_per_tweet": round(avg_likes, 1),
            "best_tweets": sorted(
                [t for t in tweets if t and t.get("description")],
                key=lambda t: (
                    int(re.search(r"(\d+)\s+likes?", t.get("description", "")).group(1))
                    if re.search(r"(\d+)\s+likes?", t.get("description", ""))
                    else 0
                ),
                reverse=True,
            )[:5],
            "generated_at": datetime.now(UTC).isoformat(),
        }

        return report


# Convenience function for cron jobs
async def run_social_scan():
    """Run a full social scan - called by cron every 6 hours."""
    cache = get_cache()
    scraper = XWebScraper(cache)

    # Discover new tweets
    tweets = await scraper.discover_tweets()
    logger.info(f"Social scan: discovered {len(tweets)} tweets")

    # Update profile
    profile = await scraper.get_profile()
    logger.info(f"Social scan: profile updated - {profile}")

    # Check mentions
    mentions = await scraper.get_mentions()
    logger.info(f"Social scan: found {len(mentions)} mentions")

    # Get trending topics
    trends = await scraper.get_trending_topics()
    logger.info(f"Social scan: found {len(trends)} trending items")

    # Generate engagement report
    report = await scraper.get_engagement_report()
    logger.info(
        f"Social scan: engagement report - avg {report.get('avg_likes_per_tweet', 0)} likes/tweet"
    )

    return {
        "tweets_found": len(tweets),
        "mentions_found": len(mentions),
        "trends_found": len(trends),
        "report": report,
    }
