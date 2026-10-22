"""Persist X posts into news_items (the canonical store).

Body quality comes from quality_x.score, which loads the canonical scorer
(app/domains/news/quality.py) by path instead of importing the app package, so
the lane runs in a bare shell. One override is X-specific and deliberate: a
short post is a *complete* post, not a truncated article, so a `stub` verdict
is floored to `thin`. Nothing shorter than 40 characters is stored at all (it
is an emoji or a link card).
"""

from __future__ import annotations

import hashlib
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "news_fallback"))

import db
import quality_x
from model import Post

MIN_POST = 40
_CASHTAG = re.compile(r"\$([A-Za-z][A-Za-z0-9]{1,9})\b")
_WS = re.compile(r"\s+")

_INSERT = """
INSERT INTO news_items (
  news_id, url, title, summary, body_markdown, image_url, source, published_at,
  ingested_at, chains_mentioned, tokens_mentioned, sentiment_score, source_tier,
  source_trust, content_hash, body_source, body_quality_score, body_quality_verdict,
  truncated, category, source_type, tweet_id
) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT DO NOTHING
RETURNING news_id
"""


def quality(text: str, url: str) -> tuple[str, int, str, bool]:
    """(body_source, score, verdict, truncated) with the X short-post floor."""
    scored = quality_x.score(text, url)
    return scored.body_source, scored.score, scored.verdict, scored.truncated


def _title(post: Post) -> str:
    """First line, clipped — matches how the RSS path titles a story."""
    first = post.text.strip().split("\n", 1)[0].strip()
    return (first or post.text.strip())[:280]


def content_hash(text: str) -> str:
    """Hash of the normalised body: a genuine re-post dedupes, an edit does not."""
    return hashlib.sha256(_WS.sub(" ", text.strip().lower()).encode()).hexdigest()


def _tokens(text: str) -> list[str]:
    """Cashtags mentioned, deduped and uppercased."""
    return sorted({m.upper() for m in _CASHTAG.findall(text)})


def to_row(post: Post, source: str, trust: float, klass: str) -> tuple[tuple[object, ...], dict]:
    """Build the insert tuple for one post."""
    news_id = f"x:{post.dedupe_key}"
    body_source, score, verdict, truncated = quality(post.text, post.url)
    row = (
        news_id,
        post.url,
        _title(post),
        post.text[:400],
        post.text,
        post.media[0] if post.media else None,
        source,
        post.created_at,
        datetime.now(UTC),
        [],
        _tokens(post.text),
        0.0,
        2 if klass == "project" else 1,
        trust,
        content_hash(post.text),
        body_source,
        score,
        verdict,
        truncated,
        klass,
        "x",
        post.tweet_id,
    )
    meta = {"news_id": news_id, "verdict": verdict, "score": score, "route": post.route}
    return row, meta


def store_many(posts: list[Post], source: str, trust: float, klass: str) -> dict[str, object]:
    """Insert posts idempotently; returns counts plus the inserted news_ids."""
    if not posts:
        return {"inserted": 0, "duplicate": 0, "skipped": 0, "news_ids": []}
    inserted: list[str] = []
    duplicate = skipped = 0
    with db.connect() as conn:
        with conn.cursor() as cur:
            for post in posts:
                if len(post.text.strip()) < MIN_POST:
                    skipped += 1
                    continue
                row, _ = to_row(post, source, trust, klass)
                cur.execute(_INSERT, row)
                if cur.fetchone() is None:
                    duplicate += 1
                else:
                    inserted.append(str(row[0]))
        conn.commit()
    return {
        "inserted": len(inserted),
        "duplicate": duplicate,
        "skipped": skipped,
        "news_ids": inserted,
    }


def rag_ready(news_id: str) -> bool:
    """True when the row exists without an embedding (safe to (re)embed)."""
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT rag_embedding_id FROM news_items WHERE news_id=%s", (news_id,))
        row = cur.fetchone()
    return bool(row) and row[0] is None


def mark_embedded(news_id: str, point_id: str) -> None:
    """Record the Qdrant point id so re-runs never embed twice."""
    with db.connect() as conn:
        conn.execute(
            "UPDATE news_items SET rag_embedding_id=%s WHERE news_id=%s", (point_id, news_id)
        )
        conn.commit()
