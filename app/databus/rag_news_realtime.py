"""
Continuous RAG News Ingestion
==============================
Ingests ALL 200+ DataBus news sources into Qdrant news_v2 in real-time.

Runs every 30 minutes via the app lifespan background loop.
Feeds: aggregate_all_news (RSS × 200+), CryptoPanic, domains news service,
X/Crypto Twitter, Telegram news sweep — all already consolidated into a
single news_intel pipeline.

Uses story clustering (MinHash+DBSCAN) for cross-source dedup before
embedding into Qdrant via upsert_rag_document (qwen3-4b dense + sparse).
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

# Qdrant collection target
QDRANT_COLLECTION = "news_v2"


async def rag_news_realtime(**kw) -> dict[str, Any]:
    """Continuous RAG news ingestion — runs every 30m from lifespan loop.

    Pipeline:
      aggregate_all_news (200+ RSS feeds + CT + CryptoPanic)
      → story clusterer (MinHash+DBSCAN dedup)
      → upsert_rag_document (qwen3-4b embed → Qdrant news_v2)

    Returns ingestion stats for monitoring.
    """
    from app.databus.news_intel import aggregate_all_news

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

    # ── Phase 1: Cluster articles into stories for cross-source dedup ──
    # Use the same story clusterer that the nightly indexer uses.
    # The clusterer produces ONE story doc per unique story, with multiple
    # sources attached. This prevents 20 RSS feeds from creating 20 Qdrant
    # points for the same news event.
    from app.domains.news.clusterer import NewsItem as ClusterNewsItem, build_story_rag_docs
    from app.integrations.rugbrain_client import rugbrain_ingest

    cluster_input: list[ClusterNewsItem] = []
    article_map: dict[str, dict[str, Any]] = {}

    for article in articles:
        title = str(article.get("title", "") or "").strip()
        if not title:
            continue
        url = str(article.get("url", "") or article.get("link", "") or "").strip()
        summary = str(article.get("summary") or article.get("description") or "")[:500]
        source = str(article.get("source", "") or "")[:100]
        published_str = str(
            article.get("published_at") or article.get("published") or article.get("created_at") or now.isoformat()
        )[:50]
        try:
            pub_dt = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pub_dt = now
        key = url or f"hash:{hashlib.sha256(title.encode()).hexdigest()[:12]}"
        sent_raw = article.get("sentiment")
        sent_val = (
            float(sent_raw.get("score") or 0.0)
            if isinstance(sent_raw, dict)
            else (float(sent_raw or 0.0) if sent_raw else 0.0)
        )
        cluster_input.append(
            ClusterNewsItem(
                id=key,
                title=title,
                body=summary,
                source=source,
                url=url,
                published_at=pub_dt,
                sentiment=sent_val,
            )
        )
        article_map[key] = article

    if not cluster_input:
        return {"status": "ok", "fetched": len(articles), "ingested": 0, "collection": QDRANT_COLLECTION}

    story_docs = build_story_rag_docs(cluster_input)
    dedup_ratio = len(cluster_input) / max(len(story_docs), 1)
    logger.info(
        "rag_news_realtime: %d articles → %d stories (%.1f:1 dedup)",
        len(cluster_input),
        len(story_docs),
        dedup_ratio,
    )

    # ── Phase 2: Ingest each story doc into Qdrant ──
    ingested = 0
    errors: list[str] = []

    for doc in story_docs:
        doc_id = doc.get("doc_id", "")
        content = doc.get("content", "")[:2000]
        metadata = dict(doc.get("metadata", {}))
        metadata.setdefault("indexed_at", now.isoformat())
        metadata.setdefault("ingestion", "realtime_clustered")
        metadata.setdefault("text", content)

        try:
            ok = await rugbrain_ingest(
                content=content,
                collection=QDRANT_COLLECTION,
                doc_id=doc_id,
                metadata=metadata,
            )
            if ok:
                ingested += 1
            else:
                errors.append(f"{doc_id}: ingest_failed")
        except Exception as e:
            errors.append(f"{doc_id}: {e!s}")

    return {
        "status": "ok",
        "collection": QDRANT_COLLECTION,
        "fetched": len(articles),
        "ingested": ingested,
        "stories": len(story_docs),
        "errors": errors[:5],
        "indexed_at": now.isoformat(),
    }
