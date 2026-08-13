"""
Rug Munch Intelligence - RAG Ingestion Pipeline
=================================================
Nightly indexing of ALL data sources into the RAG system.
Feeds: news, CT rundown, market data, social metrics, on-chain data.

Runs at 3AM UTC. Embeds via NVIDIA NIM (BGE-M3, 1024d, free).
Redis-backed with permanence to B2.  # Migrated from R2 to B2 2026-07-31
"""

from __future__ import annotations

import email.utils
import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("rag_ingestion")


# ── News timestamp helpers ──────────────────────────────────────────

# Staleness threshold: news docs older than this are flagged as stale.
NEWS_STALE_AFTER_HOURS = float(os.getenv("NEWS_STALE_AFTER_HOURS", "48"))

# Live news source used by ``index_databus_news`` — the wired DataBus
# ``news_intel`` chain pulls RSS + CryptoPanic + X + the domains news
# service (200+ sources, quality-scored) in real time.
_DATABUS_NEWS_SOURCE = "aggregate_all_news (news_intel chain)"


def _normalize_published(value: Any) -> str:
    """Best-effort normalize an article timestamp to ISO-8601 UTC.

    Accepts ISO strings (with/without tz, ``Z`` suffix), RFC-2822 RSS
    dates (feedparser ``published``, e.g. ``Wed, 30 Jul 2026 10:00:00
    GMT``), bare dates, and unix epoch numbers — matching what the
    news_intel fetchers emit (``published``), the domains news service
    emits (``published_at``), and what ``_parse_published_at`` in
    app.rag.retrieval accepts. Returns "" when the value cannot be parsed
    (caller falls back to now).
    """
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC).isoformat()
        except (ValueError, OSError, OverflowError):
            return ""
    s = str(value).strip()
    if not s:
        return ""
    # Numeric string epoch (e.g. "1785763800").
    if s.isdigit() and len(s) >= 9:
        try:
            return datetime.fromtimestamp(float(s), tz=UTC).isoformat()
        except (ValueError, OSError, OverflowError):
            return ""
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # RFC-2822 RSS dates ("Wed, 30 Jul 2026 10:00:00 GMT").
        try:
            parsed = email.utils.parsedate_to_datetime(s)
        except (TypeError, ValueError):
            return ""
        dt = parsed
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


async def index_databus_news(limit: int = 50, **kw) -> dict[str, Any]:
    """Ingest fresh DataBus news headlines into the RAG ``news_v2`` collection.

    Pulls from the wired ``news_intel`` chain (``aggregate_all_news``:
    RSS + CryptoPanic + X + the domains news service, deduped and
    quality-scored) and routes each article through the canonical RAG
    ingestion pipeline (BatchIngestor → quality filter → dedup → embed)
    into ``news_v2`` — the collection the ``get_news`` RAG tool searches.

    Every article is stored with normalized ``published_at`` metadata
    (plus title / source_url / source_name / categories / sentiment) so
    the retrieval layer's temporal-decay recency boost ranks fresh events
    first. Idempotent: chunk-hash dedup skips already-ingested articles.
    """
    from app.rag.ingestion import BatchIngestor

    try:
        from app.databus.news_intel import aggregate_all_news

        feed = await aggregate_all_news(limit=limit)
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException, ImportError) as e:
        logger.warning(f"databus news aggregate failed: {e}")
        return {"status": "error", "error": str(e)[:200], "collection": "news_v2", "source": _DATABUS_NEWS_SOURCE}

    articles = feed.get("articles", []) or []
    now_iso = datetime.now(UTC).isoformat()
    ingestor = BatchIngestor()
    ingested = 0
    duplicates = 0
    errors: list[str] = []

    for article in articles:
        title = str(article.get("title", "") or "").strip()
        url = str(article.get("url", "") or article.get("link", "") or "").strip()
        if not title:
            continue
        published = _normalize_published(
            article.get("published_at") or article.get("published") or article.get("created_at")
        )
        content = (f"Title: {title}\n\n{article.get('summary') or article.get('description') or ''}").strip()
        if not content:
            continue
        doc_id = f"news:{hashlib.sha256((url + title).encode()).hexdigest()[:12]}"
        metadata = {
            "title": title,
            "source_url": url,
            "source_name": str(article.get("source", "") or "")[:200],
            "published_at": published or now_iso,
            "categories": article.get("categories", []) or [],
            "sentiment": article.get("sentiment", {}).get("sentiment", "neutral")
            if isinstance(article.get("sentiment"), dict)
            else str(article.get("sentiment", "neutral")),
            "content_hash": str(article.get("content_hash", "") or ""),
            "source": "databus",
        }
        try:
            report = await ingestor.ingest(
                content=content,
                collection="news_v2",
                doc_id=doc_id,
                metadata=metadata,
                source_type="news",
            )
            if report.accepted:
                ingested += 1
            elif report.duplicate:
                duplicates += 1
            else:
                errors.extend(report.errors or [])
        except Exception as e:  # pragma: no cover - defensive
            errors.append(f"{doc_id}: {e!s}")

    return {
        "status": "ok",
        "collection": "news_v2",
        "fetched": len(articles),
        "ingested": ingested,
        "duplicates": duplicates,
        "errors": errors[:10],
        "feed_generated_at": feed.get("generated_at", ""),
        "source": _DATABUS_NEWS_SOURCE,
        "indexed_at": now_iso,
    }


async def _latest_news_v2_timestamp() -> tuple[datetime | None, dict[str, Any] | None]:
    """Scan the ``news_v2`` ANN index for the freshest document timestamp.

    Returns ``(latest_dt, latest_metadata)`` by reading the persisted
    Redis hash (``rag:ann:news_v2:docs``) — a read-only scan, no index
    mutation. Falls back to the in-memory ANN index when Redis is
    unreachable. Timestamp keys tried: ``published_at`` (crawler +
    databus), ``published`` (news_intel), ``date`` (nightly indexer),
    ``timestamp``/``fetched_at`` (ingest-time fallbacks).
    """
    latest: datetime | None = None
    latest_meta: dict[str, Any] | None = None
    raw_entries: dict[str, Any] = {}

    try:
        import redis

        r = redis.Redis(
            host=os.getenv("REDIS_HOST", "rmi-redis"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
            socket_connect_timeout=3,
        )
        raw_entries = {str(k): str(v) for k, v in (r.hgetall("rag:ann:news_v2:docs") or {}).items()}
        r.close()
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
        raw_entries = {}

    if not raw_entries:
        try:
            from app.rag.ann_index import get_index

            idx = get_index("news_v2")
            idx._load()  # read-only load of persisted index
            raw_entries = {
                doc_id: {
                    "metadata": entry.get("metadata", {}),
                    "text": entry.get("text", ""),
                }
                for doc_id, entry in (idx._meta or {}).items()
            }
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException, ImportError):
            raw_entries = {}

    for blob in raw_entries.values():
        try:
            entry = json.loads(blob) if isinstance(blob, str) else blob
            meta = entry.get("metadata", {}) or {}
        except (ValueError, TypeError):
            continue
        for key in ("published_at", "published", "date", "timestamp", "fetched_at", "indexed_at"):
            raw = meta.get(key)
            if not raw:
                continue
            ts = _normalize_published(raw)
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts)
            except ValueError:
                continue
            if latest is None or dt > latest:
                latest = dt
                latest_meta = meta
            break

    return latest, latest_meta


async def check_news_staleness(**kw) -> dict[str, Any]:
    """Flag multi-day-lag news ingestion and report the freshest wired source.

    Compares the newest indexed timestamp in the ``news_v2`` collection
    against now. When the gap exceeds ``NEWS_STALE_AFTER_HOURS`` (48h
    default) the ingestion is flagged stale. Probes the live DataBus
    ``news_intel`` chain so the report names the freshest wired source
    (``index_databus_news`` re-ingests from it on demand).
    """
    now = datetime.now(UTC)
    latest, latest_meta = await _latest_news_v2_timestamp()

    live_freshness: dict[str, Any] = {"checked": True, "source": _DATABUS_NEWS_SOURCE}
    try:
        from app.databus.news_intel import aggregate_all_news

        live = await aggregate_all_news(limit=5)
        live_articles = live.get("articles", []) or []
        live_ts = None
        live_meta = None
        for a in live_articles:
            ts = _normalize_published(a.get("published_at") or a.get("published") or a.get("created_at"))
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts)
            except ValueError:
                continue
            if live_ts is None or dt > live_ts:
                live_ts = dt
                live_meta = a
        live_freshness["generated_at"] = live.get("generated_at", "")
        live_freshness["freshest_article"] = {
            "title": str((live_meta or {}).get("title", ""))[:120],
            "source": str((live_meta or {}).get("source", "")),
            "published_at": live_ts.isoformat() if live_ts else "",
        }
        live_freshness["latest_article_age_hours"] = (
            round((now - live_ts).total_seconds() / 3600.0, 2) if live_ts else None
        )
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException, ImportError) as e:
        live_freshness = {"checked": False, "error": str(e)[:200], "source": _DATABUS_NEWS_SOURCE}

    if latest is None:
        return {
            "status": "stale",
            "stale": True,
            "reason": "no_timestamped_docs_in_news_v2",
            "collection": "news_v2",
            "latest_indexed_at": None,
            "lag_hours": None,
            "live": live_freshness,
            "remediation": "run databus chain 'rag_news_realtime' (index_databus_news)",
            "source": "news_staleness",
        }

    lag_hours = round((now - latest).total_seconds() / 3600.0, 2)
    stale = lag_hours > NEWS_STALE_AFTER_HOURS
    return {
        "status": "stale" if stale else "fresh",
        "stale": stale,
        "collection": "news_v2",
        "latest_indexed_at": latest.isoformat(),
        "latest_source": str((latest_meta or {}).get("source_name", "") or (latest_meta or {}).get("source", "")),
        "lag_hours": lag_hours,
        "stale_after_hours": NEWS_STALE_AFTER_HOURS,
        "live": live_freshness,
        "remediation": "run databus chain 'rag_news_realtime' (index_databus_news)" if stale else None,
        "source": "news_staleness",
    }


async def nightly_rag_index(**kw) -> dict[str, Any]:
    """Nightly RAG indexing - embeds all new content from all sources.

    Called by cron at 3AM UTC. Idempotent - only indexes new content.
    """
    indexed: dict[str, Any] = {"collections": {}, "total_docs": 0, "errors": []}

    try:
        # ── 1. Index recent news articles ──
        # Live DataBus news_intel chain → RAG news_v2 (the collection the
        # get_news RAG tool searches). Every article carries normalized
        # ``published_at`` metadata so the recency boost can rank it.
        news_result = await index_databus_news(limit=100)
        news_count = news_result.get("ingested", 0)
        indexed["collections"]["news_v2"] = news_count
        indexed["total_docs"] += news_count
        if news_result.get("errors"):
            indexed["errors"].append(f"news: {news_result['errors'][0]}")
        logger.info(f"RAG indexed {news_count} databus news articles into news_v2")

    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        indexed["errors"].append(f"news: {str(e)[:100]}")

    try:
        # ── 2. Index CT Rundown ──
        from app.databus.x_intel import fetch_ct_rundown

        ct = await fetch_ct_rundown(limit=30) or {}
        ct_docs = []
        for story in ct.get("rundown", [])[:20]:
            h = hashlib.sha256((story.get("url", "") + story.get("text", "")).encode()).hexdigest()[:12]
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

        market = await get_market_brief() or {}
        fear = await get_fear_greed() or {}

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


async def _embed_batch(docs: list[dict[str, Any]], collection: str) -> int:
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

            # Actually embed the document (bge-m3 via Ollama) and add the
            # vector to the ANN index so it is searchable — previously this
            # function only stored text+metadata and never computed vectors.
            try:
                from app.rag.ann_index import get_index
                from app.rag.embeddings import get_embedding

                vector = await get_embedding(doc["text"][:2000])
                get_index(collection).add(
                    doc_id,
                    vector,
                    metadata=doc.get("metadata", {}),
                    text=doc["text"][:2000],
                )
            except Exception as e:
                logger.warning(f"Embed failed for {collection}:{doc_id}: {e}")

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


async def rag_health_check(**kw) -> dict[str, Any]:
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
            "news_v2",
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
            "storage": "Redis + B2 permanence",
            "nightly_cron": "3AM UTC",
            "checked_at": datetime.now(UTC).isoformat(),
            "source": "rag_health",
        }

    except Exception as e:
        return {"status": "error", "error": str(e)[:200], "source": "rag_health"}
