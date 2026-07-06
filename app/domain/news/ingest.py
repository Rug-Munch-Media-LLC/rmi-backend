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

import feedparser
import httpx
from pydantic import HttpUrl

from app.catalog.models import NewsItem, utcnow
from app.catalog.service import get_catalog
from app.core.logging import get_logger

logger = get_logger(__name__)

log = logging.getLogger(__name__)

# Default feeds (per v4.0 §T28)
DEFAULT_FEEDS: list[dict[str, str]] = [
    {"name": "coindesk", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
    {"name": "cointelegraph", "url": "https://cointelegraph.com/rss"},
    {"name": "theblock", "url": "https://www.theblock.co/rss.xml"},
    {"name": "decrypt", "url": "https://decrypt.co/feed"},
    {"name": "beincrypto", "url": "https://beincrypto.com/feed/"},
]


def _stable_id(source: str, url: str) -> str:
    """Stable news_id from source + URL."""
    h = hashlib.md5(f"{source}:{url}".encode()).hexdigest()[:24]
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


async def fetch_feed(client: httpx.AsyncClient, feed: dict[str, str]) -> list[dict]:
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
    except Exception as e:
        log.warning("feed_fetch_fail name=%s err=%s", feed["name"], e)
        return []


async def ingest_all(
    feeds: list[dict[str, str]] | None = None,
    embed: bool = True,
    collection: str = "news_articles",
) -> dict:
    """Ingest from all configured RSS feeds. Returns counts."""
    feeds = feeds or DEFAULT_FEEDS
    cat = get_catalog()
    await cat._init_stores()
    if not cat._health.postgres:
        return {"error": "postgres unavailable"}

    counts = {"feeds_ok": 0, "feeds_fail": 0, "items": 0, "ingested": 0, "duplicate": 0, "errors": 0}
    async with httpx.AsyncClient() as client:
        # Fetch all feeds in parallel
        results = await asyncio.gather(
            *[fetch_feed(client, f) for f in feeds], return_exceptions=True
        )
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
                        pub_dt = datetime(*entry["published"][:6], tzinfo=UTC)
                text = f"{entry['title']} {entry['summary']}"
                chains = _detect_chains(text)
                tickers = _extract_tickers(text)
                news_id = _stable_id(entry["source"], entry["url"])
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
                )
                items_to_ingest.append(ni)
            except Exception as e:
                counts["errors"] += 1
                log.debug("item_build_fail: %s", e)

    # Save to Postgres
    if cat._health.postgres and items_to_ingest:
        try:
            async with cat._pg_pool.acquire() as conn:
                for ni in items_to_ingest:
                    try:
                        # Check if exists
                        existing = await conn.fetchval(
                            "SELECT 1 FROM news_items WHERE news_id=$1", ni.news_id
                        )
                        if existing:
                            counts["duplicate"] += 1
                            continue
                        # Insert
                        await conn.execute(
                            """
                            INSERT INTO news_items (
                                news_id, url, title, summary, body_markdown,
                                source, published_at, ingested_at,
                                chains_mentioned, tokens_mentioned, sentiment_score
                            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                            """,
                            ni.news_id, str(ni.url), ni.title, ni.summary, ni.body_markdown,
                            ni.source, ni.published_at, ni.ingested_at,
                            ni.chains_mentioned, ni.tokens_mentioned, ni.sentiment_score,
                        )
                        counts["ingested"] += 1
                    except Exception as e:
                        counts["errors"] += 1
                        log.debug("item_insert_fail: %s", e)
        except Exception as e:
            log.warning("ingest_postgres_fail: %s", e)

    # Embed into RAG for semantic search
    if embed and items_to_ingest:
        try:
            for ni in items_to_ingest[:50]:  # cap RAG embeds
                r = await cat.rag_ingest(
                    content=f"{ni.title}\n{ni.summary}",
                    collection=collection,
                    doc_id=ni.news_id,
                    metadata={"source": ni.source, "news_id": ni.news_id},
                )
                if r.get("status") == "ok" and r.get("qdrant_point_id"):
                    # Update news_items with rag_embedding_id
                    try:
                        async with cat._pg_pool.acquire() as conn:
                            await conn.execute(
                                "UPDATE news_items SET rag_embedding_id=$1 WHERE news_id=$2",
                                r["qdrant_point_id"], ni.news_id,
                            )
                    except Exception:
                        pass
        except Exception as e:
            log.warning("ingest_rag_fail: %s", e)

    return counts


# ── CLI entry point ──────────────────────────────────────────────
if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = asyncio.run(ingest_all())
    logger.info(json.dumps(result, indent=2))
