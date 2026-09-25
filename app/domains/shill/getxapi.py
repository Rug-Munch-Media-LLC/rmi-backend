"""GetXAPI-backed X/Twitter reads for shill intelligence.

GetXAPI is the active replacement for the retired ApiPass X lane.  The
provider is deliberately small: profile reads and authored-tweet reads only,
with aggressive caching and fail-open behavior.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, cast

import httpx

from app.core.logging import get_logger
from app.databus.cache import CacheLayer, get_cache
from app.databus.vault import resolve_key

logger = get_logger("shill.getxapi")

GETXAPI_BASE_URL = os.getenv("GETXAPI_BASE_URL", "https://api.getxapi.com").rstrip("/")
PROFILE_CACHE_TTL = 86400
TWEET_CACHE_TTL = 900
NEGATIVE_CACHE_TTL = 900
REQUEST_TIMEOUT_S = 15.0


def key_configured() -> bool:
    """Return whether the GetXAPI credential is available without exposing it."""
    return bool(resolve_key("GETXAPI_API_KEY"))


def _cache_key(prefix: str, handle: str) -> str:
    return f"shill:getxapi:{prefix}:{handle.lower()}"


def _negative_result(handle: str, reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "handle": handle,
        "reason": reason,
        "source": "getxapi",
    }


def _profile_from_data(handle: str, data: dict[str, Any]) -> dict[str, Any]:
    username = str(data.get("userName") or handle).strip().lstrip("@")
    return {
        "available": True,
        "handle": username,
        "user_id": str(data.get("id") or ""),
        "name": str(data.get("name") or username),
        "screen_name": username,
        "created_at": str(data.get("createdAt") or ""),
        "avatar_url": str(data.get("profilePicture") or ""),
        "banner_url": str(data.get("coverPicture") or ""),
        "bio": str(data.get("description") or ""),
        "location": str(data.get("location") or ""),
        "website": str(data.get("url") or ""),
        "verified": bool(data.get("isVerified") or False),
        "is_blue_verified": bool(data.get("isBlueVerified") or False),
        "metrics": {
            "followers": int(data.get("followers") or 0),
            "following": int(data.get("following") or 0),
            "statuses": int(data.get("statusesCount") or 0),
            "likes": int(data.get("favouritesCount") or 0),
            "media": int(data.get("mediaCount") or 0),
        },
        "url": f"https://x.com/{username}",
        "source": "getxapi",
    }


def _tweet_from_data(handle: str, data: dict[str, Any]) -> dict[str, Any]:
    tweet_id = str(data.get("id") or "").strip()
    url = str(data.get("url") or data.get("twitterUrl") or "")
    if not tweet_id and url:
        tweet_id = url.rstrip("/").rsplit("/", 1)[-1]
    if not tweet_id:
        tweet_id = hashlib.sha256(str(data.get("text") or "").encode()).hexdigest()[:24]
    raw_author: object = cast("object", data.get("author"))
    author = cast("dict[str, Any]", raw_author) if isinstance(raw_author, dict) else {}
    username = str(author.get("userName") or handle).lstrip("@")
    return {
        "tweet_id": tweet_id,
        "text": str(data.get("text") or ""),
        "url": url or f"https://x.com/{username}/status/{tweet_id}",
        "author": username,
        "created_at": str(data.get("createdAt") or ""),
        "likes": int(data.get("likeCount") or 0),
        "retweets": int(data.get("retweetCount") or 0),
        "replies": int(data.get("replyCount") or 0),
        "source": "getxapi",
    }


async def _get_json(path: str, params: dict[str, Any], api_key: str) -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
            response = await client.get(
                f"{GETXAPI_BASE_URL}{path}",
                params=params,
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except (httpx.HTTPError, httpx.TimeoutException, OSError) as exc:
        logger.warning("getxapi_request_failed path=%s error=%s", path, str(exc)[:120])
        return None
    if response.status_code != 200:
        logger.warning("getxapi_http_status path=%s status=%s", path, response.status_code)
        return None
    try:
        payload: object = response.json()
    except ValueError:
        logger.warning("getxapi_invalid_json path=%s", path)
        return None
    return cast("dict[str, Any]", payload) if isinstance(payload, dict) else None


async def fetch_profile(handle: str, *, force: bool = False, cache: CacheLayer | None = None) -> dict[str, Any] | None:
    """Read one public profile through GetXAPI, with a 24-hour cache."""
    handle = str(handle or "").strip().lstrip("@")
    if not handle:
        return None
    api_key = resolve_key("GETXAPI_API_KEY")
    if not api_key:
        logger.debug("getxapi_profile_skipped_no_key handle=%s", handle)
        return None
    cache = cache or get_cache()
    key = _cache_key("profile", handle)
    if not force:
        cached, _stale = await cache.get(key)
        if cached:
            return dict(cached)
    payload = await _get_json("/twitter/user/info", {"userName": handle}, api_key)
    data: object = payload.get("data") if payload else None
    if not isinstance(data, dict):
        await cache.set(key, _negative_result(handle, "not_found"), ttl=NEGATIVE_CACHE_TTL)
        return None
    profile = _profile_from_data(handle, cast("dict[str, Any]", data))
    await cache.set(key, profile, ttl=PROFILE_CACHE_TTL)
    return profile


async def fetch_user_tweets(
    handle: str, limit: int = 10, *, force: bool = False, cache: CacheLayer | None = None
) -> list[dict[str, Any]]:
    """Read authored posts through GetXAPI, with a 15-minute cache."""
    handle = str(handle or "").strip().lstrip("@")
    if not handle:
        return []
    api_key = resolve_key("GETXAPI_API_KEY")
    if not api_key:
        logger.debug("getxapi_tweets_skipped_no_key handle=%s", handle)
        return []
    cache = cache or get_cache()
    key = _cache_key("tweets", handle)
    if not force:
        cached, _stale = await cache.get(key)
        if cached is not None:
            return list(cached)[: max(1, int(limit))]
    payload = await _get_json("/twitter/user/tweets", {"userName": handle}, api_key)
    raw_tweets: object = payload.get("tweets") if payload else None
    if not isinstance(raw_tweets, list):
        raw_tweets = []
    tweet_items = cast("list[object]", raw_tweets)
    tweets = [_tweet_from_data(handle, cast("dict[str, Any]", item)) for item in tweet_items if isinstance(item, dict)]
    await cache.set(key, tweets, ttl=TWEET_CACHE_TTL)
    return tweets[: max(1, int(limit))]


__all__ = ["fetch_profile", "fetch_user_tweets", "key_configured"]
