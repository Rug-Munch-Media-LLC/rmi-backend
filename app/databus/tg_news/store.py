"""Append-only JSONL store with deterministic dedupe.

Two keys are checked before a post is written:

* ``(channel, message_id)`` — the Telegram identity of the post;
* ``sha256(normalised text)[:16]`` — collapses re-posts of the same body.

Locking: ``flock`` on the append handle keeps concurrent CLI runs safe.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import re
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.databus.tg_news.state import channel_path

logger = get_logger("tg_news")

if TYPE_CHECKING:
    from pathlib import Path

_WS_RE = re.compile(r"\s+")


def text_hash(text: str | None) -> str | None:
    """Stable content fingerprint; ``None`` for empty/media-only posts."""
    normalised = _WS_RE.sub(" ", (text or "").lower()).strip()
    if not normalised:
        return None
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:16]


def _existing(path: Path) -> tuple[set[str], set[str]]:
    ids: set[str] = set()
    hashes: set[str] = set()
    if not path.exists():
        return ids, hashes
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError as exc:
                logger.debug("skipping corrupt JSONL row in %s: %s", path, exc)
                continue
            if row.get("message_id") is not None:
                ids.add(f"{row.get('channel')}:{row['message_id']}")
            if row.get("text_hash"):
                hashes.add(str(row["text_hash"]))
    return ids, hashes


def append_posts(channel: str, posts: list[dict[str, Any]], root: str | Path | None = None) -> dict[str, int]:
    """Append only unseen posts for ``channel``; returns append/skip counters."""
    path = channel_path(channel, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    seen_ids, seen_hashes = _existing(path)
    fresh: list[dict[str, Any]] = []
    for post in posts:
        key = f"{post.get('channel', channel)}:{post.get('message_id')}"
        digest = post.get("text_hash") or text_hash(post.get("text"))
        if key in seen_ids or (digest and digest in seen_hashes):
            continue
        post["text_hash"] = digest
        fresh.append(post)
        seen_ids.add(key)
        if digest:
            seen_hashes.add(digest)
    if fresh:
        with path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                for post in fresh:
                    handle.write(json.dumps(post, ensure_ascii=False, sort_keys=False) + "\n")
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
    return {"appended": len(fresh), "skipped": len(posts) - len(fresh)}
