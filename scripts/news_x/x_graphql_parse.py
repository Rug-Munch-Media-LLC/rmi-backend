"""Pure parsing for the GraphQL timeline: instructions -> Post objects.

No I/O: the transport hands over the decoded instruction list, so this can be
tested against a captured payload without a network call.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from datetime import UTC, datetime

from model import Post


def created(value: object) -> datetime | None:
    """X's 'Fri Oct 23 12:34:56 +0000 2026' timestamp -> aware UTC."""
    try:
        return datetime.strptime(str(value), "%a %b %d %H:%M:%S %z %Y").astimezone(UTC)
    except (ValueError, TypeError):
        return None


def core_tweet(result: object) -> dict:
    """Unwrap TweetWithVisibilityResults, which nests the real tweet."""
    if not isinstance(result, dict):
        return {}
    if result.get("__typename") == "TweetWithVisibilityResults":
        result = result.get("tweet") or {}
    return result if isinstance(result, dict) else {}


def walk(instructions: list) -> Iterator[dict]:
    """Yield itemContent from every timeline instruction shape."""
    for instr in instructions or []:
        for entry in instr.get("entries") or []:
            content = entry.get("content") or {}
            if content.get("entryType") == "TimelineTimelineItem":
                yield content.get("itemContent") or {}
            for item in content.get("items") or []:
                yield ((item.get("item") or {}).get("itemContent")) or {}
        for item in instr.get("moduleItems") or []:
            yield ((item.get("item") or {}).get("itemContent")) or {}


def user_id(result: dict) -> str:
    """Numeric user id from the base64 'User:123' GID, or a plain numeric id."""
    raw = str(result.get("rest_id") or result.get("id") or "")
    if raw.isdigit():
        return raw
    try:
        decoded = base64.b64decode(raw + "=" * (-len(raw) % 4)).decode()
    except (ValueError, TypeError):
        return ""
    return decoded.rsplit(":", 1)[-1]


def profile(result: dict, handle: str) -> dict:
    """Normalise the two profile shapes: legacy.* (old) and core.* (live 2026)."""
    core = result.get("core") or {}
    legacy = result.get("legacy") or {}
    counts = result.get("relationship_counts") or {}
    verification = result.get("verification") or {}
    return {
        "handle": str(core.get("screen_name") or legacy.get("screen_name") or handle),
        "user_id": user_id(result),
        "followers": int(counts.get("followers") or legacy.get("followers_count") or 0),
        "verified": bool(result.get("is_blue_verified") or verification.get("verified")),
        "name": str(core.get("name") or legacy.get("name") or ""),
    }


def to_post(tweet: dict, handle: str) -> Post | None:
    """One tweet result -> Post, or None when it is not a usable post."""
    legacy = tweet.get("legacy") or {}
    note = ((tweet.get("note_tweet") or {}).get("note_tweet_results") or {}).get("result") or {}
    text = str(note.get("text") or legacy.get("full_text") or "").strip()
    when = created(legacy.get("created_at"))
    if not text or not when or not legacy.get("id_str"):
        return None
    entities = legacy.get("entities") or {}
    urls = [
        str(u.get("expanded_url"))
        for u in entities.get("urls") or []
        if isinstance(u, dict) and u.get("expanded_url")
    ]
    media = [
        str(m.get("media_url_https"))
        for m in ((legacy.get("extended_entities") or {}).get("media") or [])
        if isinstance(m, dict) and m.get("media_url_https")
    ]
    return Post(
        handle=handle,
        text=text,
        created_at=when,
        tweet_id=str(legacy["id_str"]),
        urls=list(dict.fromkeys(urls)),
        media=media,
        is_retweet=bool(legacy.get("retweeted_status_result")) or text.startswith("RT @"),
        is_reply=bool(legacy.get("in_reply_to_status_id_str")),
        route="graphql",
    )


def parse_timeline(instructions: list, handle: str) -> list[Post]:
    """Every distinct post in one timeline response, in server order."""
    posts: list[Post] = []
    seen: set[str] = set()
    for content in walk(instructions):
        tweet = core_tweet((content.get("tweet_results") or {}).get("result"))
        post = to_post(tweet, handle) if tweet else None
        if post and post.tweet_id not in seen:
            seen.add(str(post.tweet_id))
            posts.append(post)
    return posts


def bottom_cursor(instructions: list) -> str:
    """The Bottom cursor of a timeline page, or '' when the page is the last."""
    for instr in instructions or []:
        for entry in instr.get("entries") or []:
            content = entry.get("content") or {}
            if (
                content.get("entryType") == "TimelineTimelineCursor"
                and content.get("cursorType") == "Bottom"
            ):
                return str(content.get("value") or "")
    return ""
