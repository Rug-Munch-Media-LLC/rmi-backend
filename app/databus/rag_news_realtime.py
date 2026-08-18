"""
Continuous RAG News Ingestion
==============================
Ingests ALL 200+ DataBus news sources into Qdrant news_v2 in real-time.

Runs every 30 minutes via the DataBus scheduler chain.
Feeds: aggregate_all_news (RSS × 200+), CryptoPanic, domains news service,
X/Crypto Twitter, Telegram news sweep — all already consolidated.

Uses the Qdrant hybrid store (bge-m3 dense + sparse, Infinity rerank).
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("rag_news_realtime")

# How many recent articles to pull per cycle
REALTIME_LIMIT = int(os.getenv("RAG_REALTIME_NEWS_LIMIT", "50"))

# Minutes between runs — DataBus scheduler uses this
REALTIME_INTERVAL = int(os.getenv("RAG_REALTIME_INTERVAL", "30"))

# Qdrant collection target
QDRANT_COLLECTION = "news_v2"


async def rag_news_realtime(**kw) -> dict[str, Any]:
    """Continuous RAG news ingestion — runs every 30m via DataBus scheduler.

    Pipeline:
      aggregate_all_news (200+ RSS feeds) → story clusterer → Qdrant news_v2

    Returns ingestion stats for monitoring.
    """
    from app.databus.news_intel import aggregate_all_news
    from app.rag.qdrant_store import get_qdrant_store

    now = datetime.now(UTC)
    logger.info("rag_news_realtime: starting ingestion cycle")

    try:
        feed = await aggregate_all_news(limit=REALTIME_LIMIT)
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, ImportError) as e:
        logger.warning("rag_news_realtime: aggregate_all_news failed: %s", e)
        return {"status": "error", "error": str(e)[:200], "collection": QDRANT_COLLECTION}

    articles = feed.get("articles", []) or []
    if not articles:
        logger.info("rag_news_realtime: no new articles to ingest")
        return {"status": "ok", "fetched": 0, "ingested": 0, "collection": QDRANT_COLLECTION}

    # Get Qdrant upsert function
    try:
        from app.rag.qdrant_store import upsert_rag_document

        use_qdrant = True
    except Exception:
        use_qdrant = False

    ingested = 0
    skipped = 0
    errors: list[str] = []

    for article in articles:
        title = str(article.get("title", "") or "").strip()
        if not title:
            skipped += 1
            continue

        url = str(article.get("url", "") or article.get("link", "") or "").strip()
        summary = str(article.get("summary") or article.get("description") or "")[:800]
        source = str(article.get("source", "") or "")[:100]
        published = str(
            article.get("published_at") or article.get("published") or article.get("created_at") or now.isoformat()
        )[:50]

        # Stable doc_id for dedup
        content_key = url or title
        doc_id = f"news:{hashlib.sha256(content_key.encode()).hexdigest()[:16]}"

        # Build content for embedding
        content = f"# {title}\n\n{summary}\n\nSource: {source}" if summary else title

        # Build payload
        payload = {
            "source": source,
            "source_type": article.get("source_type", ""),
            "published_at": published,
            "url": url,
            "title": title,
            "indexed_at": now.isoformat(),
            "ingestion": "realtime",
            "categories": article.get("categories", []),
            "sentiment_label": article.get("sentiment", {}).get("sentiment", ""),
            "sentiment_score": article.get("sentiment", {}).get("score", 0),
            "text": content[:2000],
        }

        try:
            if use_qdrant:
                ok = await upsert_rag_document(
                    doc_id=doc_id,
                    text=content[:2000],
                    payload=payload,
                    collection=QDRANT_COLLECTION,
                )
                if ok:
                    ingested += 1
                else:
                    errors.append(f"{doc_id}: qdrant_upsert_failed")
            else:
                # Fallback to old path
                errors.append(f"{doc_id}: qdrant_unavailable")
        except Exception as e:
            errors.append(f"{doc_id}: {e!s}")

    return {
        "status": "ok",
        "collection": QDRANT_COLLECTION,
        "fetched": len(articles),
        "ingested": ingested,
        "skipped": skipped,
        "errors": errors[:5],
        "indexed_at": now.isoformat(),
    }


async def _fallback_ingest_via_batch_ingestor(articles: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """Fallback: use the existing BatchIngestor path when Qdrant is unavailable."""
    from app.rag.ingestion import BatchIngestor

    ingestor = BatchIngestor()
    ingested = 0
    errors: list[str] = []

    for article in articles:
        title = str(article.get("title", "") or "").strip()
        if not title:
            continue
        url = str(article.get("url", "") or article.get("link", "") or "").strip()
        summary = str(article.get("summary") or article.get("description") or "")[:800]
        source = str(article.get("source", "") or "")[:100]

        content = f"# {title}\n\n{summary}\n\nSource: {source}" if summary else title
        content_key = url or title
        doc_id = f"news:{hashlib.sha256(content_key.encode()).hexdigest()[:16]}"

        meta = {
            "source": source,
            "source_type": article.get("source_type", ""),
            "published_at": str(article.get("published_at") or article.get("published") or now.isoformat()),
            "url": url,
            "title": title,
            "indexed_at": now.isoformat(),
        }

        try:
            report = await ingestor.ingest(
                content=content,
                collection=QDRANT_COLLECTION,
                doc_id=doc_id,
                metadata=meta,
                source_type="news",
            )
            if report.accepted:
                ingested += 1
        except Exception as e:
            errors.append(f"{doc_id}: {e!s}")

    return {
        "status": "ok",
        "collection": QDRANT_COLLECTION,
        "fetched": len(articles),
        "ingested": ingested,
        "errors": errors[:5],
        "indexed_at": now.isoformat(),
        "fallback": True,
    }
