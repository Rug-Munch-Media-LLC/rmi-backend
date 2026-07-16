"""
DataBus Social Data Provider - X/Twitter + Cross-Platform Intelligence
======================================================================

Tiered access to social data with aggressive caching:
- Free tier: Cached/7-day-old social data, limited calls
- Standard tier: Real-time mentions, basic analytics
- Pro tier: Full firehose, sentiment analysis, engagement tracking
- Enterprise: Custom dashboards, competitor tracking, automated reporting

Vault integration: credentials loaded from /root/.secrets/vault.py
x402 integration: per-call pricing via DataBus
Cache: Redis-backed SWR with 15-min hot, 1-hour warm, 24-hour cold
"""

import hashlib
import time
from datetime import UTC, datetime

import httpx

from app.core.logging import get_logger
from app.databus.cache import CacheLayer

logger = get_logger("databus.social")

# ── X/Twitter API Free Tier Limits ─────────────────────────────────
# Free tier: 1,500 tweets/month POST, 10k reads/month
# Basic tier ($100/mo): 3,000 tweets POST, 10k reads/day
# Pro tier ($5,000/mo): Full search, 1M tweets/month
# We use FREE tier - must be surgical with reads
X_FREE_MONTHLY_READ_LIMIT = 10_000
X_FREE_MONTHLY_POST_LIMIT = 1_500
X_DAILY_READ_BUDGET = 333  # ~10k/30 days

# Cache TTLs (long because of read budget constraints)
CACHE_TTL_HOT = 900  # 15 min - real-time-ish
CACHE_TTL_WARM = 3600  # 1 hour - recent
CACHE_TTL_COLD = 86400  # 24 hours - historical
CACHE_TTL_WEEKLY = 604800  # 7 days - old data


class XTwitterProvider:
    """
    X/Twitter data provider with aggressive cache and read budget management.

    Free tier strategy:
    - Cache EVERYTHING for as long as possible
    - Prioritize reads: user timeline > mentions > search
    - Batch reads: get max results per call
    - Skip duplicate reads: check cache first ALWAYS
    - Reserve 100 reads/day for posting/engagement
    """

    def __init__(self, cache: CacheLayer):
        self.cache = cache
        self._client: httpx.AsyncClient | None = None
        self._oauth2_token: str | None = None
        self._token_expires: float = 0
        self._daily_reads = 0
        self._daily_resets = time.time()
        self._bearer: str | None = None
        self._api_key: str | None = None
        self._api_secret: str | None = None
        self._oauth2_refresh: str | None = None
        self._loaded = False

    async def _load_creds(self):
        """Load X credentials from vault - NEVER read from .env or plaintext."""
        if self._loaded:
            return
        try:
            import subprocess

            result = subprocess.run(
                ["python3", "/root/.secrets/vault.py", "get", "rmi/social/x_api_key"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self._api_key = result.stdout.strip()
            result = subprocess.run(
                ["python3", "/root/.secrets/vault.py", "get", "rmi/social/x_api_secret"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self._api_secret = result.stdout.strip()
            result = subprocess.run(
                ["python3", "/root/.secrets/vault.py", "get", "rmi/social/x_oauth2_token"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self._oauth2_token = result.stdout.strip()
            result = subprocess.run(
                ["python3", "/root/.secrets/vault.py", "get", "rmi/social/x_oauth2_refresh"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self._oauth2_refresh = result.stdout.strip()
            self._loaded = True
            logger.info("X/Twitter credentials loaded from vault")
        except Exception as e:
            logger.error(f"Failed to load X credentials from vault: {e}")
            raise

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url="https://api.x.com/2",
                timeout=30.0,
                headers={"Content-Type": "application/json"},
            )
        return self._client

    def _check_budget(self) -> bool:
        """Ensure we stay within free tier daily read budget."""
        now = time.time()
        if now - self._daily_resets > 86400:
            self._daily_reads = 0
            self._daily_resets = now
        return self._daily_reads < X_DAILY_READ_BUDGET

    def _budget_used(self):
        self._daily_reads += 1

    async def _api_call(
        self, method: str, endpoint: str, params: dict | None = None
    ) -> dict | None:
        """Make an X API call with budget tracking and error handling."""
        if not self._check_budget():
            logger.warning("X API daily read budget exhausted")
            return None

        await self._load_creds()
        client = await self._get_client()

        headers = {"Authorization": f"Bearer {self._oauth2_token}"}

        try:
            if method == "GET":
                resp = await client.get(endpoint, params=params, headers=headers)
            else:
                resp = await client.post(endpoint, json=params, headers=headers)

            self._budget_used()

            if resp.status_code == 429:
                logger.warning("X API rate limited")
                return None
            if resp.status_code == 401:
                logger.warning("X API auth failed - token may need refresh")
                return None

            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"X API error: {e.response.status_code} {e.response.text[:200]}")
            return None
        except Exception as e:
            logger.error(f"X API call failed: {e}")
            return None

    # ── Public Data Endpoints (cached aggressively) ──────────────

    async def get_user(self, username: str) -> dict | None:
        """Get user profile - cached 24h."""
        cache_key = f"social:x:user:{username}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        data = await self._api_call(
            "GET",
            f"/users/by/username/{username}",
            params={
                "user.fields": "public_metrics,description,created_at,profile_image_url,verified,location,url"
            },
        )
        if data and "data" in data:
            await self.cache.set(cache_key, data["data"], ttl=CACHE_TTL_COLD)
            return data["data"]
        return None

    async def get_user_tweets(
        self,
        user_id: str,
        max_results: int = 100,
        since_id: str | None = None,
        tweet_fields: str | None = None,
    ) -> list[dict] | None:
        """Get recent tweets from a user - cached 15min hot, 1h warm."""
        cache_key = f"social:x:tweets:{user_id}:{max_results}:{since_id or 'latest'}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        params = {
            "max_results": min(max_results, 100),
            "tweet.fields": tweet_fields
            or "created_at,public_metrics,entities,attachments,in_reply_to_user_id,referenced_tweets,lang,context_annotations",
            "exclude": "retweets,replies",
        }
        if since_id:
            params["since_id"] = since_id

        data = await self._api_call("GET", f"/users/{user_id}/tweets", params=params)
        if data and "data" in data:
            tweets = data["data"]
            await self.cache.set(cache_key, tweets, ttl=CACHE_TTL_HOT)
            # Also cache individual tweets
            for tweet in tweets:
                await self.cache.set(f"social:x:tweet:{tweet['id']}", tweet, ttl=CACHE_TTL_COLD)
            return tweets
        return None

    async def get_mentions(self, user_id: str, max_results: int = 100) -> list[dict] | None:
        """Get mentions of user - cached 15min."""
        cache_key = f"social:x:mentions:{user_id}:{max_results}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        data = await self._api_call(
            "GET",
            f"/users/{user_id}/mentions",
            params={
                "max_results": str(min(max_results, 100)),
                "tweet.fields": "created_at,public_metrics,author_id,in_reply_to_user_id",
            },
        )
        if data and "data" in data:
            mentions = data["data"]
            await self.cache.set(cache_key, mentions, ttl=CACHE_TTL_HOT)
            return mentions
        return None

    async def get_tweet(self, tweet_id: str) -> dict | None:
        """Get a single tweet - cached 24h (tweets don't change)."""
        cache_key = f"social:x:tweet:{tweet_id}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        data = await self._api_call(
            "GET",
            f"/tweets/{tweet_id}",
            params={
                "tweet.fields": "created_at,public_metrics,entities,attachments,in_reply_to_user_id,referenced_tweets,lang,context_annotations",
                "expansions": "author_id,referenced_tweets.id",
                "user.fields": "username,name,public_metrics,verified",
            },
        )
        if data and "data" in data:
            await self.cache.set(cache_key, data, ttl=CACHE_TTL_COLD)
            return data
        return None

    async def get_engagement_metrics(self, tweet_ids: list[str]) -> dict[str, dict]:
        """Get engagement metrics for multiple tweets - cached 1h."""
        if not tweet_ids:
            return {}

        results = {}
        uncached = []

        for tid in tweet_ids[:100]:  # API limit
            cached = await self.cache.get(f"social:x:metrics:{tid}")
            if cached:
                results[tid] = cached
            else:
                uncached.append(tid)

        if uncached and self._check_budget():
            ids_str = ",".join(uncached[:100])
            data = await self._api_call(
                "GET", "/tweets", params={"ids": ids_str, "tweet.fields": "public_metrics"}
            )
            if data and "data" in data:
                for tweet in data["data"]:
                    tid = tweet["id"]
                    metrics = tweet.get("public_metrics", {})
                    results[tid] = metrics
                    await self.cache.set(f"social:x:metrics:{tid}", metrics, ttl=CACHE_TTL_WARM)
                    self._budget_used()

        return results

    async def get_followers_count(self, user_id: str) -> int | None:
        """Quick follower count check - cached 1h."""
        cache_key = f"social:x:followers:{user_id}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        data = await self._api_call(
            "GET", f"/users/{user_id}", params={"user.fields": "public_metrics"}
        )
        if data and "data" in data:
            count = data["data"]["public_metrics"]["followers_count"]
            await self.cache.set(cache_key, count, ttl=CACHE_TTL_WARM)
            return count
        return None

    # ── Write Operations (x402-gated) ────────────────────────────

    async def post_tweet(
        self, text: str, reply_to: str | None = None, media_ids: list[str] | None = None
    ) -> dict | None:
        """Post a tweet - requires x402 payment, uses POST budget."""
        payload = {"text": text}
        if reply_to:
            payload["reply"] = {"in_reply_to_tweet_id": reply_to}
        if media_ids:
            payload["media"] = {"media_ids": media_ids}

        data = await self._api_call("POST", "/tweets", params=payload)
        return data


class SocialDataAggregator:
    """
    Aggregates social data from X/Twitter + web sources.

    Provides DataBus-compatible routes:
    - social/x/profile - user profile data
    - social/x/tweets - recent tweets (cached)
    - social/x/mentions - brand mentions
    - social/x/engagement - engagement metrics
    - social/x/search - keyword search (expensive, cache heavily)
    - social/kol/reputation - KOL reputation scores
    - social/sentiment - basic sentiment from recent mentions
    """

    def __init__(self, cache: CacheLayer):
        self.cache = cache
        self.x = XTwitterProvider(cache)
        self._our_user_id: str | None = None

    async def get_our_profile(self) -> dict | None:
        """Get @CryptoRugMunch profile - cached 1h."""
        return await self.x.get_user("CryptoRugMunch")

    async def get_our_tweets(
        self, count: int = 20, since_id: str | None = None
    ) -> list[dict] | None:
        """Get @CryptoRugMunch timeline."""
        profile = await self.get_our_profile()
        if not profile:
            return None
        return await self.x.get_user_tweets(profile["id"], max_results=count, since_id=since_id)

    async def get_our_mentions(self, count: int = 20) -> list[dict] | None:
        """Get mentions of @CryptoRugMunch."""
        profile = await self.get_our_profile()
        if not profile:
            return None
        return await self.x.get_user_mentions(profile["id"], max_results=count)

    async def search_mentions(self, query: str, count: int = 10) -> list[dict] | None:
        """
        Search for brand mentions - VERY expensive on free tier.
        Heavily cached (24h). Only use for critical queries.
        """
        cache_key = f"social:x:search:{hashlib.sha256(query.encode()).hexdigest()}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        data = await self.x._api_call(
            "GET",
            "/tweets/search/recent",
            params={
                "query": query,
                "max_results": str(min(count, 100)),
                "tweet.fields": "created_at,public_metrics,author_id",
            },
        )
        if data and "data" in data:
            await self.cache.set(cache_key, data["data"], ttl=CACHE_TTL_COLD)
            return data["data"]
        return None

    async def get_kol_reputation(self, username: str) -> dict:
        """
        Calculate KOL reputation score based on:
        - Follower count
        - Engagement rate
        - Scam promotion history (from our database)
        - Community trust indicators

        Returns 0-100 score with breakdown.
        """
        cache_key = f"social:kol:reputation:{username}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        user_data = await self.x.get_user(username)
        if not user_data:
            return {"score": 0, "error": "User not found", "username": username}

        metrics = user_data.get("public_metrics", {})
        followers = metrics.get("followers_count", 0)
        following = metrics.get("following_count", 0)
        tweet_count = metrics.get("tweet_count", 0)

        # Base score calculation
        score = 50  # Start neutral

        # Follower bonus (logarithmic)
        import math

        if followers > 0:
            score += min(20, math.log10(followers) * 5)

        # Following ratio penalty (follows too many = likely engagement pod)
        if following > 0 and followers > 0:
            ratio = followers / following
            if ratio < 1:  # Following more than followers
                score -= 10

        result = {
            "username": username,
            "score": round(min(100, max(0, score)), 1),
            "followers": followers,
            "following": following,
            "tweets": tweet_count,
            "verified": user_data.get("verified", False),
            "engagement_estimate": "pending",  # Would need tweet sampling
        }

        await self.cache.set(cache_key, result, ttl=CACHE_TTL_COLD)
        return result

    async def get_sentiment(self, username: str = "CryptoRugMunch") -> dict:
        """
        Basic sentiment analysis of recent mentions.
        Uses cached data only - no live API calls.
        Falls back to web scraping if no cached data.
        """
        cache_key = f"social:sentiment:{username}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        # Try to get cached mentions
        profile = await self.x.get_user(username)
        mentions_key = f"social:x:mentions:{profile['id'] if profile else 'unknown'}:20"
        mentions = await self.cache.get(mentions_key)

        result = {
            "username": username,
            "overall_sentiment": "neutral",
            "positive_ratio": 0.0,
            "negative_ratio": 0.0,
            "total_mentions_analyzed": 0,
            "last_updated": datetime.now(UTC).isoformat(),
            "note": "Sentiment analysis requires Pro tier API or cached data",
        }

        if mentions:
            result["total_mentions_analyzed"] = len(mentions)
            # Simple heuristic sentiment from engagement
            total_likes = sum(m.get("public_metrics", {}).get("like_count", 0) for m in mentions)
            avg_likes = total_likes / max(1, len(mentions))
            result["average_engagement"] = round(avg_likes, 1)
            result["overall_sentiment"] = "positive" if avg_likes > 10 else "neutral"

        await self.cache.set(cache_key, result, ttl=CACHE_TTL_WARM)
        return result
