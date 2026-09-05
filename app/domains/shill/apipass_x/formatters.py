"""Output formatting: hostile provider payloads → clean tweet/profile shapes."""

from __future__ import annotations

from typing import Any

from app.domains.shill.apipass_x.helpers import (
    _as_dict,
    _as_int,
    _decode_user_id,
    _first,
    _tweet_url,
)


def normalize_tweet(raw: Any) -> dict[str, Any] | None:
    """Normalize one raw tweet payload into the shared tweet shape."""
    if not isinstance(raw, dict):
        return None
    nested = raw.get("tweet")
    tweet: dict[str, Any] = nested if isinstance(nested, dict) else raw
    if not isinstance(tweet, dict):
        return None
    tweet_id = str(_first(tweet, "tweet_id", "id", "id_str", "rest_id", default="") or "").strip()
    text = str(_first(tweet, "text", "full_text", "tweet_text", "content", default="") or "").strip()
    if not text or not tweet_id:
        return None

    author = _as_dict(tweet.get("author"))
    user = _as_dict(tweet.get("user"))
    core_user = None
    core = tweet.get("core")
    if isinstance(core, dict):
        _ur = core.get("user_results")
        if isinstance(_ur, dict):
            core_user = _ur.get("result") if isinstance(_ur.get("result"), dict) else None
    legacy = None
    if isinstance(core_user, dict) and isinstance(core_user.get("legacy"), dict):
        legacy = core_user["legacy"]
    handle = (
        str(
            _first(author, "username", "screen_name", "handle", default="")
            or _first(user, "username", "screen_name", "handle", default="")
            or _first(tweet, "username", "screen_name", "handle", default="")
            or (str(legacy.get("screen_name") or "") if legacy else "")
        )
        .strip()
        .lstrip("@")
    )
    author_name = (
        str(
            _first(author, "name", "display_name", "full_name", default="")
            or _first(user, "name", "display_name", default="")
            or _first(tweet, "name", "display_name", default="")
            or (str(legacy.get("name") or "") if legacy else "")
        ).strip()
        or handle
    )

    created = str(_first(tweet, "created_at", "createdAt", "timestamp", default="") or "").strip()
    metrics = tweet.get("engagement") if isinstance(tweet.get("engagement"), dict) else {}
    if not metrics and isinstance(tweet.get("public_metrics"), dict):
        metrics = tweet["public_metrics"]
    if not metrics and isinstance(tweet.get("metrics"), dict):
        metrics = tweet["metrics"]
    if not metrics:
        metrics = {
            "retweet_count": _first(tweet, "retweet_count", "retweets", default=0),
            "like_count": _first(tweet, "like_count", "likes", "favorite_count", default=0),
            "reply_count": _first(tweet, "reply_count", "replies", default=0),
            "quote_count": _first(tweet, "quote_count", "quotes", default=0),
        }

    return {
        "tweet_id": tweet_id,
        "text": text[:500],
        "handle": handle,
        "author_name": author_name,
        "created_at": created,
        "retweet_count": _as_int(_first(metrics, "retweet_count", "retweets", "retweetCount")),
        "like_count": _as_int(_first(metrics, "like_count", "likes", "likeCount", "favorite_count")),
        "reply_count": _as_int(_first(metrics, "reply_count", "replies", "replyCount")),
        "quote_count": _as_int(_first(metrics, "quote_count", "quotes")),
        "url": _tweet_url(handle, tweet_id),
        "source": "apipass_x",
    }


def _extract_tweets(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Pull every tweet-shaped dict out of a provider resultJson (tolerant)."""
    if not isinstance(payload, dict):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _walk(value: Any) -> None:
        if isinstance(value, dict):
            norm = normalize_tweet(value)
            if norm is not None:
                tid = norm["tweet_id"]
                if tid not in seen:
                    seen.add(tid)
                    out.append(norm)
                return
            for v in value.values():
                _walk(v)
        elif isinstance(value, list):
            for v in value:
                _walk(v)

    _walk(payload)
    return out


def _find_user_node(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    def _candidate(v: Any, depth: int = 0) -> dict[str, Any] | None:
        if depth > 6 or not isinstance(v, dict):
            return None
        if any(k in v for k in ("legacy", "core", "rest_id", "id_str")):
            return v
        if v.get("__typename") == "UserUnavailable" or "reason" in v or "message" in v:
            return v
        for key in ("result", "user", "data", "variables"):
            inner = v.get(key)
            if isinstance(inner, dict):
                found = _candidate(inner, depth + 1)
                if found:
                    return found
        for val in v.values():
            if isinstance(val, dict):
                found = _candidate(val, depth + 1)
                if found:
                    return found
        return None

    return _candidate(payload)


def normalize_profile(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Normalize a ``user-by-screen-name`` resultJson into the rich X profile."""
    node = _find_user_node(payload)
    if not isinstance(node, dict):
        return None
    if node.get("__typename") == "UserUnavailable" or "reason" in node or "message" in node:
        return {
            "available": False,
            "reason": str(node.get("reason") or node.get("__typename") or ""),
            "message": str(node.get("message") or ""),
            "__typename": str(node.get("__typename") or ""),
        }
    core = _as_dict(node.get("core"))
    avatar = _as_dict(node.get("avatar"))
    loc = _as_dict(node.get("location"))
    ver = _as_dict(node.get("verification"))
    leg = _as_dict(node.get("legacy"))
    handle = str(core.get("screen_name") or "").strip().lstrip("@")
    if not handle:
        handle = str(node.get("screen_name") or node.get("username") or "").strip().lstrip("@")
    rest_id = _decode_user_id(node.get("rest_id") or node.get("id") or "")
    vinfo = _as_dict(node.get("verification_info"))
    vreason = _as_dict(vinfo.get("reason"))
    vdesc = _as_dict(vreason.get("description"))
    pinned = [str(x) for x in (leg.get("pinned_tweet_ids_str") or []) if x]
    return {
        "available": True,
        "handle": handle,
        "user_id": rest_id,
        "name": str(core.get("name") or handle),
        "screen_name": str(core.get("screen_name") or handle),
        "created_at": str(core.get("created_at") or ""),
        "avatar_url": str(avatar.get("image_url") or ""),
        "banner_url": str(leg.get("profile_banner_url") or ""),
        "bio": str(leg.get("description") or ""),
        "location": str(loc.get("location") or ""),
        "website": str(leg.get("url") or ""),
        "verified": bool(ver.get("verified") or False),
        "is_blue_verified": bool(node.get("is_blue_verified") or False),
        "verification_reason": str(vdesc.get("text") or ""),
        "metrics": {
            "followers": _as_int(leg.get("followers_count")),
            "following": _as_int(leg.get("friends_count")),
            "statuses": _as_int(leg.get("statuses_count")),
            "likes": _as_int(leg.get("favourites_count")),
            "listed": _as_int(leg.get("listed_count")),
            "media": _as_int(leg.get("media_count")),
            "fast_followers": _as_int(leg.get("fast_followers_count")),
            "normal_followers": _as_int(leg.get("normal_followers_count")),
        },
        "pinned_tweet_ids": pinned,
        "pinned_url": _tweet_url(handle, pinned[0]) if pinned else "",
        "url": f"https://x.com/{handle}",
        "source": "apipass_x",
    }


__all__ = [
    "normalize_profile",
    "normalize_tweet",
]
