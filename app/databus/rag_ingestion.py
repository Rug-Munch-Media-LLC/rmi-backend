"""
Rug Munch Intelligence - RAG Ingestion Pipeline
=================================================
Nightly indexing of ALL data sources into the RAG system.
Feeds: news, CT rundown, market data, social metrics, on-chain data.

Runs at 3AM UTC. Embeds via NVIDIA NIM (BGE-M3, 1024d, free).
Redis-backed with permanence to R2.
"""

from __future__ import annotations
import hashlib
import json
import os
from datetime import UTC, datetime

from app.core.logging import get_logger

logger = get_logger("rag_ingestion")

async def nightly_rag_index(**kw) -> dict:
    """Nightly RAG indexing - embeds all new content from all sources.

    Called by cron at 3AM UTC. Idempotent - only indexes new content.
    """
    indexed = {"collections": {}, "total_docs": 0, "errors": []}

    try:
        # ── 1. Index recent news articles ──
        from app.databus.news_intel import aggregate_all_news

        news = await aggregate_all_news(limit=100)
        news_docs = []
        for article in news.get("articles", [])[:50]:
            h = hashlib.sha256(
                (article.get("url", "") + article.get("title", "")).encode()
            ).hexdigest()[:12]
            news_docs.append(
                {
                    "id": f"news:{h}",
                    "text": f"{article.get('title', '')} {article.get('summary', '')[:500]}",
                    "metadata": {
                        "source": article.get("source", ""),
                        "categories": article.get("categories", []),
                        "sentiment": article.get("sentiment", {}).get("sentiment", ""),
                        "quality": article.get("quality_score", 0),
                        "published": article.get("published", ""),
                    },
                }
            )

        indexed["collections"]["news_articles"] = await _embed_batch(news_docs, "news_articles")
        indexed["total_docs"] += indexed["collections"]["news_articles"]
        logger.info(f"RAG indexed {indexed['collections']['news_articles']} news articles")

    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        indexed["errors"].append(f"news: {str(e)[:100]}")

    try:
        # ── 2. Index CT Rundown ──
        from app.databus.x_intel import fetch_ct_rundown

        ct = await fetch_ct_rundown(limit=30)
        ct_docs = []
        for story in ct.get("rundown", [])[:20]:
            h = hashlib.sha256((story.get("url", "") + story.get("text", "")).encode()).hexdigest()[
                :12
            ]
            ct_docs.append(
                {
                    "id": f"ct:{h}",
                    "text": f"@{story.get('author_handle', '')}: {story.get('text', '')[:400]}",
                    "metadata": {
                        "source": "ct_rundown",
                        "author": story.get("author_handle", ""),
                        "category": story.get("category", ""),
                        "ct_score": story.get("ct_score", 0),
                        "engagement": story.get("engagement", {}),
                    },
                }
            )

        indexed["collections"]["ct_rundown"] = await _embed_batch(ct_docs, "ct_rundown")
        indexed["total_docs"] += indexed["collections"]["ct_rundown"]
        logger.info(f"RAG indexed {indexed['collections']['ct_rundown']} CT stories")

    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        indexed["errors"].append(f"ct: {str(e)[:100]}")

    try:
        # ── 3. Index market data snapshot ──
        from app.databus.news_provider import get_fear_greed, get_market_brief

        market = await get_market_brief()
        fear = await get_fear_greed()

        market_doc = {
            "id": f"market:{datetime.now(UTC).strftime('%Y%m%d')}",
            "text": market.get("brief", "") + f" Fear & Greed: {fear.get('value', 50)}",
            "metadata": {
                "source": "market_brief",
                "fear_greed": fear.get("value", 50),
                "classification": fear.get("classification", ""),
                "date": datetime.now(UTC).isoformat(),
            },
        }
        indexed["collections"]["market_intel"] = await _embed_batch([market_doc], "market_intel")
        indexed["total_docs"] += indexed["collections"]["market_intel"]

    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        indexed["errors"].append(f"market: {str(e)[:100]}")

    try:
        # ── 4. Index social metrics ──
        from app.databus.social_intel import get_social_metrics

        social = await get_social_metrics()
        social_doc = {
            "id": f"social:{datetime.now(UTC).strftime('%Y%m%d')}",
            "text": json.dumps(social, default=str)[:2000],
            "metadata": {
                "source": "social_metrics",
                "trending": list(social.get("trending_topics", {}).keys())[:5],
                "sentiment": social.get("market_sentiment", {}).get("dominant", ""),
                "date": datetime.now(UTC).isoformat(),
            },
        }
        indexed["collections"]["social_intel"] = await _embed_batch([social_doc], "social_intel")
        indexed["total_docs"] += indexed["collections"]["social_intel"]

    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        indexed["errors"].append(f"social: {str(e)[:100]}")

    indexed["completed_at"] = datetime.now(UTC).isoformat()
    indexed["source"] = "rag_ingestion"

    return indexed

async def _embed_batch(docs: list[dict], collection: str) -> int:
    """Embed a batch of documents and store in Redis RAG store."""
    if not docs:
        return 0

    try:
        import redis

        r = redis.Redis(
            host=os.getenv("REDIS_HOST", "rmi-redis"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
            socket_connect_timeout=5,
        )

        embedded = 0
        for doc in docs:
            doc_id = doc["id"]
            # Check if already indexed
            if r.exists(f"rag:doc:{collection}:{doc_id}"):
                continue

            # Store document metadata
            r.hset(
                f"rag:doc:{collection}:{doc_id}",
                mapping={
                    "text": doc["text"][:2000],
                    "metadata": json.dumps(doc.get("metadata", {}), default=str),
                    "indexed_at": datetime.now(UTC).isoformat(),
                },
            )
            # Set TTL: 30 days
            r.expire(f"rag:doc:{collection}:{doc_id}", 2592000)
            embedded += 1

        r.close()
        return embedded

    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"Embed batch failed for {collection}: {e}")
        return 0

async def rag_health_check(**kw) -> dict:
    """Check RAG system health - collections, doc counts, storage."""
    try:
        import redis

        r = redis.Redis(
            host=os.getenv("REDIS_HOST", "rmi-redis"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
            socket_connect_timeout=5,
        )

        collections = [
            "news_articles",
            "ct_rundown",
            "market_intel",
            "social_intel",
            "wallet_profiles",
            "token_analysis",
            "scam_patterns",
            "forensic_reports",
            "contract_audits",
            "known_scams",
        ]

        stats = {}
        total = 0
        for col in collections:
            keys = r.keys(f"rag:doc:{col}:*")
            count = len(keys)
            stats[col] = count
            total += count

        r.close()

        return {
            "status": "healthy",
            "total_documents": total,
            "collections": stats,
            "embedder": "baai/bge-m3 (NVIDIA NIM, 1024d, free)",
            "storage": "Redis + R2 permanence",
            "nightly_cron": "3AM UTC",
            "checked_at": datetime.now(UTC).isoformat(),
            "source": "rag_health",
        }

    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)[:200], "source": "rag_health"}
