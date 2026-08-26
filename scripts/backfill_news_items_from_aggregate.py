"""Sync the Redis news aggregate into Postgres news_items (Tier-1 restore).

The 15-min cron's RSS leg only pulls CryptoPanic's single feed, so
news_items was Telegram-only and trending's primary tier starved. This
script upserts the aggregated feed (200 sources -> deduped -> reranked
-> images extracted) into news_items with stable ids.

Run: standalone (env from backend) or via rmi-news-ingest.sh step 1.5.
Idempotent: ON CONFLICT (news_id) DO NOTHING.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


UPSERT = """
INSERT INTO news_items (
    news_id, url, title, summary, source, published_at,
    image_url, sentiment_score, chains_mentioned, tokens_mentioned,
    source_tier, sentiment_label
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,NULL,NULL)
ON CONFLICT (news_id) DO NOTHING
"""


def _stable_id(url: str) -> str:
    return "rss:" + hashlib.sha256(url.encode()).hexdigest()[:20]


async def main(limit: int = 120) -> int:
    from app.catalog.service import get_catalog
    from app.databus.news_provider import get_aggregated_news

    agg = await get_aggregated_news(limit=limit)
    articles = (agg or {}).get("articles") or []
    if not articles:
        # Cold cache (TTL gap between background refreshes) — force one.
        from app.databus.news_provider import refresh_news_cache

        await refresh_news_cache()
        agg = await get_aggregated_news(limit=limit)
        articles = (agg or {}).get("articles") or []
    if not articles:
        print("aggregate empty — nothing to sync")
        return 0

    catalog = get_catalog()
    await catalog._init_stores()
    if not catalog._pg_pool:
        print("no pg pool")
        return 0

    inserted = 0
    async with catalog._pg_pool.acquire() as conn:
        for a in articles:
            url = a.get("url") or ""
            title = a.get("title") or ""
            if not url or not title:
                continue
            published = a.get("published_at") or datetime.now(UTC).isoformat()  # type: ignore[attr-defined]
            try:
                pub_dt = datetime.fromisoformat(str(published).replace("Z", "+00:00"))
            except ValueError:
                pub_dt = datetime.now()
            tags = await conn.fetchval(
                "SELECT count(*) FROM news_items WHERE url = $1", url
            )
            if tags:
                continue
            await conn.execute(
                UPSERT,
                _stable_id(url),
                url,
                title[:300],
                (a.get("description") or "")[:1000],
                a.get("source") or "unknown",
                pub_dt,
                a.get("image_url"),
                float(a.get("sentiment_score") or 0.0),
                list(a.get("chains_mentioned") or []),
                list(a.get("tokens_mentioned") or []),
            )
            inserted += 1
    print(f"synced {inserted}/{len(articles)} aggregate articles into news_items")
    return inserted


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 120))
