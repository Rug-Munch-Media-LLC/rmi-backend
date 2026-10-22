"""Post model + pure parsers for the three transport shapes.

No I/O here: every parser takes a string and returns Post objects, so the
transport chain and the storage layer can be tested independently.
"""

from __future__ import annotations

import hashlib
import html as htmllib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

_NEXT = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
_ITEM = re.compile(r'<div class="timeline-item[^"]*"(.*?)(?=<div class="timeline-item|$)', re.S)
_ID = re.compile(r'data-id="(\d+)"')
_TIME = re.compile(r'data-time="(\d+)"')
_TEXT = re.compile(r'<div class="tweet-content[^"]*"[^>]*>(.*?)</div>', re.S)
_IMG = re.compile(r'<img src="([^"]+)"')
_TAG = re.compile(r"<[^>]+>")
_MEDIA_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4")


@dataclass(slots=True)
class Post:
    """One ingested post, transport-agnostic."""

    handle: str
    text: str
    created_at: datetime
    tweet_id: str | None = None
    urls: list[str] = field(default_factory=list)
    media: list[str] = field(default_factory=list)
    is_retweet: bool = False
    is_reply: bool = False
    route: str = ""

    @property
    def dedupe_key(self) -> str:
        """Tweet id when the transport exposes one, else a content hash."""
        if self.tweet_id:
            return self.tweet_id
        digest = hashlib.sha256(self.text.strip().encode()).hexdigest()[:20]
        return f"h{self.handle.lower()}{digest}"

    @property
    def url(self) -> str:
        """Canonical status URL, or the profile URL when the id is unknown."""
        if self.tweet_id:
            return f"https://x.com/{self.handle}/status/{self.tweet_id}"
        return f"https://x.com/{self.handle}"


def _ts(value: object) -> datetime | None:
    """Parse RFC3339 or epoch seconds into an aware UTC datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC)
    text = str(value).strip()
    if text.isdigit():
        return datetime.fromtimestamp(float(text), tz=UTC)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _links(entities: dict[str, object] | None, fallback: str) -> list[str]:
    """All http(s) URLs referenced by a syndication tweet object."""
    out: list[str] = []
    for key in ("urls", "media"):
        for item in (entities or {}).get(key) or []:
            if isinstance(item, dict) and item.get("expanded_url"):
                out.append(str(item["expanded_url"]))
    out.extend(re.findall(r"https?://[^\s\"'<>]+", fallback))
    return list(dict.fromkeys(out))


def parse_syndication(handle: str, body: str) -> list[Post]:
    """Timeline from the syndication HTML page (the id-complete path)."""
    match = _NEXT.search(body)
    if not match:
        return []
    try:
        entries = json.loads(match.group(1))["props"]["pageProps"]["timeline"]["entries"]
    except (ValueError, KeyError, TypeError):
        return []
    posts: list[Post] = []
    for entry in entries:
        tweet = (entry.get("content") or {}).get("tweet") or {}
        text = tweet.get("full_text") or ""
        created = _ts(tweet.get("created_at"))
        if not text or not created or not tweet.get("id_str"):
            continue
        posts.append(
            Post(
                handle=handle,
                text=text,
                created_at=created,
                tweet_id=str(tweet["id_str"]),
                urls=_links(tweet.get("entities"), text),
                media=[
                    str(media["media_url_https"])
                    for media in ((tweet.get("extended_entities") or {}).get("media") or [])
                    if isinstance(media, dict) and media.get("media_url_https")
                ],
                is_retweet=bool(tweet.get("retweeted_status") or text.startswith("RT @")),
                is_reply=bool(tweet.get("in_reply_to_status_id_str")),
                route="syndication",
            )
        )
    return posts


def parse_nitter(handle: str, body: str) -> list[Post]:
    """Timeline from a nitter mirror (data-id + data-time per item)."""
    posts: list[Post] = []
    for block in _ITEM.findall(body):
        tid, stamp, text = _ID.search(block), _TIME.search(block), _TEXT.search(block)
        created = _ts(stamp.group(1)) if stamp else None
        if not (tid and created and text):
            continue
        clean = htmllib.unescape(_TAG.sub(" ", text.group(1))).strip()
        if not clean:
            continue
        posts.append(
            Post(
                handle=handle,
                text=clean,
                created_at=created,
                tweet_id=tid.group(1),
                urls=re.findall(r"https?://[^\s\"'<>]+", clean),
                media=[m for m in _IMG.findall(block) if m.lower().endswith(_MEDIA_EXT)],
                is_retweet="retweet" in block.lower(),
                is_reply="replying to" in block.lower(),
                route="nitter",
            )
        )
    return posts
