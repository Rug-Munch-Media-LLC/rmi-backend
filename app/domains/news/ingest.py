"""T28 RSS Ingest - populates news_items + crypto_news from RSS feeds.

Sources (v4.0 master stack): 5+ RSS feeds
- CoinDesk
- Cointelegraph
- The Block
- Decrypt
- BeInCrypto

Caches raw HTML to MinIO, writes metadata to news_items,
embeds into RAG engine for semantic search.

Run as a cron: every 15 minutes.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
from datetime import UTC, datetime
from typing import Any

import feedparser
import httpx
from pydantic import HttpUrl

from app.catalog.models import NewsItem, utcnow
from app.catalog.service import get_catalog
from app.core.logging import get_logger
from app.domains.news.news_service.sources import RSS_FEEDS
from app.domains.news.ratings import rate_article

logger = get_logger(__name__)

log = get_logger(__name__)

# All RSS feeds from sources.py (expanded to 200+ sources)
DEFAULT_FEEDS: list[dict[str, str]] = [{"name": name, "url": url} for url, name in RSS_FEEDS]


def _stable_id(source: str, url: str) -> str:
    """Stable news_id from source + URL."""
    h = hashlib.sha256(f"{source}:{url}".encode()).hexdigest()[:24]
    return f"{source}:{h}"


def _detect_chains(text: str) -> list[str]:
    """Heuristic chain detection from text content."""
    text_l = text.lower()
    chains = []
    chain_map = {
        "solana": ["solana", "sol ", "$sol"],
        "ethereum": ["ethereum", "eth ", "$eth", "ether"],
        "bitcoin": ["bitcoin", "btc ", "$btc"],
        "base": ["base ", "basechain", "coinbase base"],
        "arbitrum": ["arbitrum", "arb "],
        "polygon": ["polygon", "matic"],
        "bsc": ["bsc", "bnb chain", "binance smart chain"],
        "tron": ["tron", "trx "],
        "avalanche": ["avalanche", "avax"],
    }
    for chain, keywords in chain_map.items():
        if any(k in text_l for k in keywords):
            chains.append(chain)
    return chains


def _extract_tickers(text: str) -> list[str]:
    """Extract $TICKER style mentions."""
    import re

    return list(set(re.findall(r"\$([A-Z]{2,6})\b", text)))


async def fetch_feed(client: httpx.AsyncClient, feed: dict[str, str]) -> list[dict[str, Any]]:
    """Fetch and parse a single RSS feed."""
    try:
        r = await client.get(feed["url"], timeout=10.0, follow_redirects=True)
        r.raise_for_status()
        parsed = feedparser.parse(r.content)
        items = []
        for entry in parsed.entries[:30]:  # cap per feed
            items.append(
                {
                    "source": feed["name"],
                    "title": entry.get("title", ""),
                    "url": entry.get("link", ""),
                    "summary": entry.get("summary", "")[:2000],
                    "published": entry.get("published_parsed") or entry.get("updated_parsed"),
                }
            )
        return items
    except (httpx.HTTPError, KeyError, TypeError, ConnectionError, OSError, TimeoutError) as e:
        logger.debug("fetch_feed_error", exc_info=True)
        log.warning("feed_fetch_fail name=%s err=%s", feed["name"], e)
        return []


async def ingest_all(
    feeds: list[dict[str, str]] | None = None,
    embed: bool = True,
    collection: str = "news_articles",
) -> dict[str, Any]:
    """Ingest from all configured RSS feeds. Returns counts."""
    feeds = feeds or DEFAULT_FEEDS
    cat = get_catalog()
    await cat._init_stores()
    if not cat._health.postgres:
        return {"error": "postgres unavailable"}

    counts = {
        "feeds_ok": 0,
        "feeds_fail": 0,
        "items": 0,
        "ingested": 0,
        "duplicate": 0,
        "errors": 0,
        "total_feeds": len(feeds),
        "rag_stories": 0,
    }
    # Batch fetches to avoid overwhelming connections with 200+ feeds
    results: list[Any] = []
    async with httpx.AsyncClient() as client:
        for i in range(0, len(feeds), 20):
            batch = feeds[i : i + 20]
            batch_results = await asyncio.gather(*[fetch_feed(client, f) for f in batch], return_exceptions=True)
            results.extend(batch_results)
            if i + 20 < len(feeds):
                await asyncio.sleep(0.25)  # Polite delay between batches
    items_to_ingest: list[NewsItem] = []
    for _i, r in enumerate(results):
        if isinstance(r, Exception) or not r:
            counts["feeds_fail"] += 1
            continue
        counts["feeds_ok"] += 1
        for entry in r:
            counts["items"] += 1
            try:
                pub_dt = utcnow()
                if entry.get("published"):
                    with contextlib.suppress(Exception):
                        yr, mo, dy, hr, mi, se = entry["published"][:6]
                        pub_dt = datetime(yr, mo, dy, hr, mi, se, tzinfo=UTC)
                text = f"{entry['title']} {entry['summary']}"
                chains = _detect_chains(text)
                tickers = _extract_tickers(text)
                news_id = _stable_id(entry["source"], entry["url"])
                # Ground News-style rating card (bias / sentiment / factual / tier)
                rating = rate_article(
                    title=entry["title"],
                    content=entry["summary"],
                    source=entry["source"],
                )
                ni = NewsItem(
                    news_id=news_id,
                    url=HttpUrl(entry["url"]) if entry["url"] else HttpUrl("https://unknown.local"),
                    title=entry["title"][:500],
                    summary=entry["summary"][:2000],
                    body_markdown=None,
                    source=entry["source"],
                    published_at=pub_dt,
                    ingested_at=utcnow(),
                    chains_mentioned=chains,
                    tokens_mentioned=tickers,
                    sentiment_score=rating.sentiment_score / 100.0,  # normalize to -1..1 column scale
                    bias=rating.bias,
                    sentiment_label=rating.sentiment_label,
                    factual_rating=rating.factual_rating,
                    source_tier=rating.source_tier,
                    rating_confidence=rating.confidence,
                )
                items_to_ingest.append(ni)
            except (httpx.HTTPError, KeyError, TypeError) as e:
                logger.debug("unknown_error", exc_info=True)
                counts["errors"] += 1
                log.debug("item_build_fail: %s", e)

    # Save to Postgres
    new_items: list[NewsItem] = []
    if cat._health.postgres and items_to_ingest:
        try:
            async with cat._pg_pool.acquire() as conn:
                for ni in items_to_ingest:
                    try:
                        # Check if exists
                        existing = await conn.fetchval("SELECT 1 FROM news_items WHERE news_id=$1", ni.news_id)
                        if existing:
                            counts["duplicate"] += 1
                            continue
                        # Insert
                        await conn.execute(
                            """
                            INSERT INTO news_items (
                                news_id, url, title, summary, body_markdown,
                                source, published_at, ingested_at,
                                chains_mentioned, tokens_mentioned, sentiment_score,
                                bias, sentiment_label, factual_rating, source_tier,
                                rating_confidence
                            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
                            """,
                            ni.news_id,
                            str(ni.url),
                            ni.title,
                            ni.summary,
                            ni.body_markdown,
                            ni.source,
                            ni.published_at,
                            ni.ingested_at,
                            ni.chains_mentioned,
                            ni.tokens_mentioned,
                            ni.sentiment_score,
                            ni.bias,
                            ni.sentiment_label,
                            ni.factual_rating,
                            ni.source_tier,
                            ni.rating_confidence,
                        )
                        counts["ingested"] += 1
                        new_items.append(ni)
                    except (httpx.HTTPError, KeyError, ValueError, TypeError) as e:
                        logger.debug("unknown_error", exc_info=True)
                        counts["errors"] += 1
                        log.debug("item_insert_fail: %s", e)
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as e:
            logger.debug("unknown_error", exc_info=True)
            log.warning("ingest_postgres_fail: %s", e)

    # Embed into RAG for semantic search — ONE RAG doc per story cluster
    # (content-derived cluster_id), so the same article across 20 feeds
    # collapses to a single doc carrying source_count + the rating card.
    if embed and new_items:
        try:
            from app.domains.news.clusterer import NewsItem as ClusterNewsItem, build_story_rag_docs

            cluster_input = [
                ClusterNewsItem(
                    id=ni.news_id,
                    title=ni.title or "",
                    body=ni.summary or "",
                    source=ni.source or "",
                    url=str(ni.url),
                    published_at=ni.published_at,
                    sentiment=ni.sentiment_score or 0.0,
                )
                for ni in new_items
            ]
            story_docs = build_story_rag_docs(cluster_input)
            for doc in story_docs[:200]:  # cap RAG embeds (up from 50 for 200+ feeds)
                rag_result = await cat.rag_ingest(
                    content=doc["content"],
                    collection=collection,
                    doc_id=doc["doc_id"],
                    metadata=doc["metadata"],
                )
                if rag_result.get("status") == "ok" and rag_result.get("qdrant_point_id"):
                    point_id = rag_result["qdrant_point_id"]
                    counts["rag_stories"] += 1
                    try:
                        async with cat._pg_pool.acquire() as conn:
                            await conn.executemany(
                                "UPDATE news_items SET rag_embedding_id=$1 WHERE news_id=$2",
                                [(point_id, str(iid)) for iid in doc["metadata"].get("item_ids", [])],
                            )
                    except (ConnectionError, OSError):
                        pass
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as e:
            logger.debug("unknown_error", exc_info=True)
            log.warning("ingest_rag_fail: %s", e)

    return counts


# ── CLI entry point ──────────────────────────────────────────────
if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = asyncio.run(ingest_all())
    logger.info(json.dumps(result, indent=2))
    print("\n📊 Ingest Summary:")
    print(f"   Total feeds configured: {result.get('total_feeds', '?')}")
    print(f"   Feeds OK: {result.get('feeds_ok', 0)}")
    print(f"   Feeds failed: {result.get('feeds_fail', 0)}")
    print(f"   Raw items: {result.get('items', 0)}")
    print(f"   Newly ingested: {result.get('ingested', 0)}")
    print(f"   Duplicates skipped: {result.get('duplicate', 0)}")
    print(f"   RAG story docs embedded: {result.get('rag_stories', 0)}")
    print(f"   Errors: {result.get('errors', 0)}")
