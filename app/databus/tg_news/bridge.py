"""Map one swept Telegram post to a ``news_items`` row and a RAG document.

The ``tg_news`` scraper writes JSONL; nothing turned that into database rows, so
a swept security desk was visible to the file reader and invisible to every SQL
consumer and to Rugbrain retrieval. This module owns the *shape*; ``persist``
owns the IO.

Weights (what Rugbrain ranks on):

* ``source_trust``  — curated channel weight (0.95 investigation desk … 0.35
  aggregator), the same 0.90 band the web security lane uses;
* ``source_weight`` — effective rank weight = ``source_trust * trust_score/100``,
  so one bait-shaped post cannot ride a good channel to the top;
* ``source_type``   — ``telegram``; ``body_source`` — ``tg:<handle>``.

Determinism: ``news_id`` is ``tg:<channel>:<message_id>`` — the key the preview
lane already used, so adopting an existing post updates it instead of creating a
second row for one post — and the RAG doc id is ``uuid5(URL,
"tg:<channel>:<message_id>")``, so a re-run maps to the same Qdrant point.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from app.databus.tg_news.classify import KIND_WEIGHTS
from app.databus.tg_news.verified_channels import ROLES, WEIGHTS

if TYPE_CHECKING:
    from collections.abc import Iterable

MAX_AGE_DAYS = 180
RAG_COLLECTION = "news_v2_8b"

#: Scam vocabulary: a security post naming a scam primitive becomes ``scam``;
#: the rest stay ``security``. Deterministic, no model call.
_SCAM = (
    "drainer",
    "phishing",
    "scam",
    "rug pull",
    "stolen",
    "stole",
    "exploit",
    "hacked",
    "compromis",
)


def channel_weight(handle: str) -> float:
    """Curated channel weight, else the role weight; 0.5 when neither is known."""
    lowered = handle.lower()
    if lowered in WEIGHTS:
        return float(WEIGHTS[lowered])
    role = ROLES.get(lowered)
    return KIND_WEIGHTS.get(role or "unknown", 0.5) if role else 0.5


def category(post: dict[str, Any], handle: str) -> str:
    """``scam`` when the body names a scam primitive, else ``security``/``news``."""
    if ROLES.get(handle.lower()) != "security":
        return "news"
    lowered = str(post.get("text") or "").lower()
    return "scam" if any(word in lowered for word in _SCAM) else "security"


def doc_id(handle: str, message_id: int) -> str:
    """Stable RAG document id for one post."""
    return "news-tg:" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"tg:{handle.lower()}:{message_id}"))


def rows(
    posts: Iterable[dict[str, Any]], handle: str, now: datetime, *, max_age_days: int = MAX_AGE_DAYS
) -> list[tuple[Any, ...]]:
    """One insert tuple per post; skips rows older than ``max_age_days``."""
    trust = channel_weight(handle)
    out = []
    for post in posts:
        message_id = post.get("message_id")
        try:
            published = datetime.fromisoformat(str(post.get("timestamp")).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if message_id is None or now - published > timedelta(days=max_age_days):
            continue
        text = str(post.get("text") or "")
        url = post.get("url") or f"https://t.me/{handle}/{message_id}"
        score = float(post.get("trust_score") or 50) / 100.0
        out.append(
            (
                f"tg:{handle}:{message_id}",
                url,
                (text.splitlines()[0].strip()[:140] if text.strip() else "(media post)"),
                text[:400],
                text,
                f"Telegram @{handle}",
                published,
                now,
                3,
                trust,
                round(trust * score, 3),
                hashlib.sha256(url.encode("utf-8")).hexdigest(),
                f"tg:{handle}",
                False,
                category(post, handle),
                "telegram",
            )
        )
    return out


def document(news_id: str, url: str, title: str, body: str, handle: str, trust: float, published: str) -> dict:
    """One RAG ingest document shaped like the X lane's payload."""
    message_id = news_id.rsplit(":", 1)[-1]
    doc = doc_id(handle, int(message_id)) if message_id.isdigit() else "news-tg:" + news_id
    return {
        "doc_id": doc,
        "content": f"{title}\n\n{body}",
        "collection": RAG_COLLECTION,
        "source": f"tg:{handle}",
        "severity": "high",
        "metadata": {
            "title": title,
            "url": url,
            "source": handle,
            "source_class": "security",
            "source_trust": trust,
            "source_weight": trust,
            "published": published,
            "lane": "tg_news",
            "source_type": "telegram",
        },
    }
