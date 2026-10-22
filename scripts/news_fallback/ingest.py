"""Persist fallback articles into news_items and (research/security) into RAG."""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import db
import httpx

if TYPE_CHECKING:
    from extract import Article

RAG_INGEST = "http://127.0.0.1:8012/api/v1/ingest"
RAG_COLLECTION = "news_v2"  # resolves to the serving news_v2_8b lane

_REG = ("cftc", "esma", "fatf", "fincen", "sec ", "ofac", "regulator", "treasury")
_RESEARCH = (
    "research",
    "capital",
    "diligence",
    "halborn",
    "dedaub",
    "quantstamp",
    "hexens",
    "kaiko",
    "coinmetrics",
    "messari",
    "dune",
    "nansen",
    "ic3",
    "stanford",
    "mit ",
    "berkeley",
    "paradigm",
    "variant",
    "dragonfly",
    "pantera",
    "multicoin",
    "placeholder",
    "spartan",
    "electric",
    "the tie",
    "cms",
    "chainstack",
    "alchemy",
)
_SECURITY = ("security", "rekt", "solidified", "consensys", "blockanalytica", "cryptosecurity")

_INSERT = """
INSERT INTO news_items (
  news_id, url, title, summary, body_markdown, source, published_at, ingested_at,
  chains_mentioned, tokens_mentioned, sentiment_score, source_tier, source_trust,
  content_hash, body_source, truncated, category
) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT DO NOTHING
RETURNING news_id
"""


def classify(source: str) -> tuple[str, float, bool]:
    """Return (class, source_trust, send_to_rag) for a source name."""
    low = source.lower()
    if any(k in low for k in _REG):
        return "regulation", 0.95, True
    if any(k in low for k in _SECURITY):
        return "security", 0.90, True
    if any(k in low for k in _RESEARCH):
        return "research", 0.85, True
    if any(k in low for k in ("exchange", "binance", "coinbase", "kraken", "okx")):
        return "exchange", 0.70, False
    return "media", 0.65, False


def _ingest_rag(article: Article, source: str, klass: str, trust: float) -> str:
    key = os.environ.get("RMI_RAG_API_KEY", "")
    if not key:
        return "rag_skipped_no_key"
    doc_id = "news-fallback:" + hashlib.sha256(article.url.encode()).hexdigest()[:32]
    payload = {
        "documents": [
            {
                "doc_id": doc_id,
                "content": f"{article.title}\n\n{article.body}",
                "collection": RAG_COLLECTION,
                "source": f"news_fallback:{source}",
                "severity": "high" if klass in ("security", "regulation") else "medium",
                "metadata": {
                    "title": article.title,
                    "url": article.url,
                    "source": source,
                    "source_class": klass,
                    "source_trust": trust,
                    "published": (article.published or datetime.now(UTC)).isoformat(),
                    "lane": "rss_fallback",
                },
            }
        ]
    }
    try:
        resp = httpx.post(RAG_INGEST, json=payload, headers={"X-API-Key": key}, timeout=60.0)
        body = resp.json()
        if body.get("ingested"):
            return f"rag_200:{doc_id}"
        return f"rag_{resp.status_code}:0"
    except (httpx.HTTPError, ValueError) as exc:
        return f"rag_error:{type(exc).__name__}"


def store(article: Article, source: str, route: str) -> dict[str, object]:
    """Insert one article; RAG-ingest when its source class warrants it."""
    klass, trust, to_rag = classify(source)
    url = article.url
    news_id = f"{source}:{hashlib.sha256(url.encode()).hexdigest()[:24]}"
    published = article.published or datetime.now(UTC)
    summary = article.body[:400]
    truncated = len(article.body) >= 20000
    row = (
        news_id,
        url,
        article.title,
        summary,
        article.body,
        source,
        published,
        datetime.now(UTC),
        [],
        [],
        0.0,
        2,
        trust,
        hashlib.sha256(url.encode()).hexdigest(),
        f"fallback:{route}",
        truncated,
        "news",
    )
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(_INSERT, row)
            inserted = cur.fetchone() is not None
        conn.commit()
    rag = _ingest_rag(article, source, klass, trust) if (inserted and to_rag) else "skipped"
    if rag.startswith("rag_200:"):
        point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, rag.split(":", 1)[1]))
        with db.connect() as conn:
            conn.execute("UPDATE news_items SET rag_embedding_id=%s WHERE news_id=%s", (point_id, news_id))
            conn.commit()
    return {"news_id": news_id, "inserted": inserted, "class": klass, "rag": rag}
