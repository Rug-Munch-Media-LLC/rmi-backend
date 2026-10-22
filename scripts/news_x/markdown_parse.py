"""Parser for rendered-reader markdown (pry and Bright Data).

Two shapes arrive from the readers:

* Bright Data renders one `*   [](/handle/status/<id>)` bullet per post, so the
  bullet is a reliable post boundary and the status id is present. The post
  timestamp is then decoded from the id itself (Twitter snowflake), which is
  exact — the human "Oct 21" label carries no year.
* Pry returns a bare pinned post (`Pinned\\n\\n<text>`) with no id and no date,
  so those posts fall back to the ingest clock and a content hash for dedupe.

Parsers are pure: a string in, Post objects out, no I/O.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from model import Post

SNOWFLAKE_EPOCH_MS = 1288834974657
MIN_TEXT = 40
_SHORT = 6

_BLOCK = re.compile(r"^\*\s+\[\]\(/([A-Za-z0-9_]{1,15})/status/(\d{6,25})\)", re.M)
_STATUS = re.compile(r"/([A-Za-z0-9_]{1,15})/status/(\d{6,25})")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_TAG = re.compile(r"<[^>]+>")
_NOISE = (
    "log in or sign up",
    "see what",
    "continue with",
    "terms",
    "privacy",
    "accessibility",
    "© 20",
    "posts",
    "followers",
    "following",
    "joined ",
    "show more",
    "pinned",
    "mention",
    "follow",
)
_METRIC = re.compile(r"^[\d.,]+[KM]?$")
_DATE_LABEL = re.compile(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{1,2}$")


def _snowflake(tweet_id: str) -> datetime:
    """Exact post time from the status id."""
    return datetime.fromtimestamp(((int(tweet_id) >> 22) + SNOWFLAKE_EPOCH_MS) / 1000, tz=UTC)


def _clean(block: str, handle: str) -> str:
    """Strip chrome from one post block, leaving the post text."""
    text = _IMAGE.sub(" ", block)
    text = _LINK.sub(r"\1", text)
    text = _TAG.sub(" ", text)
    kept: list[str] = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("*").strip()
        if not line or len(line) < _SHORT:
            continue
        low = line.lower()
        if _METRIC.match(line) or _DATE_LABEL.match(line) or low.startswith(_NOISE):
            continue
        if line in (handle, f"@{handle}") or line.lower() == handle.lower():
            continue
        kept.append(line)
    return " ".join(kept).strip()


def _id_post(handle: str, tweet_id: str, block: str, handle_in_block: str) -> Post | None:
    """One Bright Data bullet -> Post (id and time both known)."""
    if handle_in_block.lower() != handle.lower():
        return None
    text = _clean(block, handle)
    if len(text) < MIN_TEXT:
        return None
    return Post(
        handle=handle,
        text=text,
        created_at=_snowflake(tweet_id),
        tweet_id=tweet_id,
        urls=list(dict.fromkeys(re.findall(r"https?://[^\s)\]\"'<>]+", block)))[:8],
        media=re.findall(r"https?://pbs\.twimg\.com/media/[^\s)\"']+", block)[:4],
        is_retweet=text.startswith("RT @"),
        is_reply=text.startswith("@"),
        route="reader",
    )


def _paragraphs(handle: str, body: str, now: datetime) -> list[Post]:
    """Id-less shape (pry): one Post per sizeable paragraph."""
    posts: list[Post] = []
    for chunk in re.split(r"\n{2,}", body):
        text = _clean(chunk, handle)
        if len(text) < MIN_TEXT:
            continue
        found = _STATUS.search(chunk)
        tweet_id = found.group(2) if found and found.group(1).lower() == handle.lower() else None
        posts.append(
            Post(
                handle=handle,
                text=text,
                created_at=_snowflake(tweet_id) if tweet_id else now,
                tweet_id=tweet_id,
                urls=[],
                media=[],
                is_retweet=text.startswith("RT @"),
                is_reply=text.startswith("@"),
                route="reader",
            )
        )
    return posts


def parse_markdown(handle: str, body: str, now: datetime | None = None) -> list[Post]:
    """Rendered-reader markdown -> posts, whichever shape the reader used."""
    stamp = now or datetime.now(UTC)
    marks = list(_BLOCK.finditer(body))
    if not marks:
        return _paragraphs(handle, body, stamp)
    posts: list[Post] = []
    for index, match in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(body)
        post = _id_post(handle, match.group(2), body[match.start() : end], match.group(1))
        if post:
            posts.append(post)
    return posts
