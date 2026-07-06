"""
RMI Mega News Aggregator - Largest Free Crypto News Pipeline
50+ RSS feeds, automatic dedup, sentiment scoring, multi-DB storage
"""

import hashlib
import json
import logging
import re
import time
from xml.etree import ElementTree as ET

import httpx

logger = logging.getLogger("rmi.news")

# ═══════════════════════════════════════════════════════
# 50+ CRYPTO RSS FEEDS - All Free, No API Keys
# ═══════════════════════════════════════════════════════
RSS_FEEDS = [
    # Tier 1 - Major outlets
    ("cointelegraph", "https://cointelegraph.com/rss"),
    ("decrypt", "https://decrypt.co/feed"),
    ("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("theblock", "https://www.theblock.co/rss"),
    ("cryptoslate", "https://cryptoslate.com/feed/"),
    ("beincrypto", "https://beincrypto.com/feed/"),
    ("bitcoinmagazine", "https://bitcoinmagazine.com/.rss/full/"),
    ("newsbtc", "https://www.newsbtc.com/feed/"),
    ("cryptopotato", "https://cryptopotato.com/feed/"),
    ("ambcrypto", "https://ambcrypto.com/feed/"),
    ("cryptobriefing", "https://cryptobriefing.com/feed/"),
    ("dailyhodl", "https://dailyhodl.com/feed/"),
    ("zycrypto", "https://zycrypto.com/feed/"),
    ("bitcoinist", "https://bitcoinist.com/feed/"),
    ("cryptonews", "https://cryptonews.com/feed/"),
    # Tier 2 - Protocol/chain specific
    ("ethereum-blog", "https://blog.ethereum.org/feed.xml"),
    ("solana-blog", "https://solana.com/feed"),
    ("polkadot-blog", "https://polkadot.network/blog/feed/"),
    ("chainlink-blog", "https://blog.chain.link/feed/"),
    ("a16z-crypto", "https://a16zcrypto.com/feed/"),
    # Tier 3 - Research / Data
    ("messari", "https://messari.io/feed"),
    ("glassnode", "https://insights.glassnode.com/feed/"),
    ("kaiko", "https://blog.kaiko.com/feed"),
    ("defillama", "https://defillama.com/feed"),
    ("dune-analytics", "https://dune.com/blog/rss.xml"),
    # Tier 4 - DeFi / Trading
    ("defi-pulse", "https://defipulse.com/blog/feed/"),
    ("bankless", "https://newsletter.banklesshq.com/feed"),
    ("the-defiant", "https://thedefiant.io/feed"),
    ("coingecko-buzz", "https://www.coingecko.com/en/blog/rss"),
    ("coinmarketcap", "https://coinmarketcap.com/feed/"),
    # Tier 5 - Regulation
    ("sec-crypto", "https://www.sec.gov/cgi-bin/rss?crypto"),
    ("cfpb", "https://www.consumerfinance.gov/feed/"),
    # Tier 6 - Additional high-signal feeds
    ("bitcoin-core", "https://bitcoincore.org/en/rss.xml"),
    ("bitcoin-ops", "https://bitcoinops.org/en/feed.xml"),
    ("lightning-blog", "https://lightning.engineering/rss/"),
    ("unchained", "https://unchainedcrypto.com/feed/"),
    ("blockworks", "https://blockworks.co/feed"),
    ("protos", "https://protos.com/feed/"),
    ("the-crypto-times", "https://thecryptotimes.com/feed/"),
    ("crypto-daily", "https://cryptodaily.co.uk/feed/"),
    ("coinjournal", "https://coinjournal.net/feed/"),
    ("trustnodes", "https://www.trustnodes.com/feed"),
    ("cryptopolitan", "https://www.cryptopolitan.com/feed/"),
    ("crypto-news-flash", "https://www.crypto-news-flash.com/feed/"),
    ("live-bitcoin-news", "https://www.livebitcoinnews.com/feed/"),
    ("crypto-reporter", "https://www.crypto-reporter.com/feed/"),
    ("coinpedia", "https://coinpedia.org/feed/"),
    ("cryptoadventure", "https://cryptoadventure.org/feed/"),
    ("coincodex", "https://coincodex.com/blog/feed/"),
]

# Simple sentiment word lists (no ML dependency)
POSITIVE_WORDS = {
    "surge",
    "soar",
    "rally",
    "bullish",
    "buy",
    "gain",
    "record",
    "boom",
    "adopt",
    "approve",
    "launch",
    "partner",
    "growth",
    "profit",
    "higher",
    "green",
    "breakout",
    "upgrade",
    "win",
    "success",
}
NEGATIVE_WORDS = {
    "crash",
    "hack",
    "exploit",
    "scam",
    "fraud",
    "ban",
    "sue",
    "fine",
    "investigation",
    "sanction",
    "drop",
    "plunge",
    "bear",
    "sell",
    "loss",
    "decline",
    "lower",
    "red",
    "warning",
    "risk",
    "fud",
    "fear",
}


def score_sentiment(text: str) -> float:
    """Fast lexicon-based sentiment: -1 (bearish) to +1 (bullish)"""
    words = set(text.lower().split())
    pos = len(words & POSITIVE_WORDS)
    neg = len(words & NEGATIVE_WORDS)
    total = pos + neg
    return (pos - neg) / total if total > 0 else 0.0


def extract_tickers(text: str) -> list[str]:
    """Extract crypto tickers from text"""
    tickers = set()
    for match in re.finditer(r"\b[A-Z]{2,5}\b", text):
        t = match.group()
        if t not in ("THE", "AND", "FOR", "BTC", "ETH", "SOL"):
            tickers.add(t)
    return list(tickers)[:5]


def fetch_all_feeds(db=None) -> dict:
    """Fetch ALL 50+ RSS feeds, deduplicate, score, store. Returns stats."""
    results = []
    sources_seen = set()
    errors = []

    for source, url in RSS_FEEDS:
        try:
            resp = httpx.get(url, timeout=15, headers={"User-Agent": "RMI/3.0 NewsBot"})
            if resp.status_code != 200:
                errors.append(f"{source}: HTTP {resp.status_code}")
                continue

            root = ET.fromstring(resp.content)
            items = root.findall(".//item")
            if not items:
                items = root.findall(".//{http://www.w3.org/2005/Atom}entry")

            count = 0
            for item in items:
                title = item.findtext("title", "").strip()
                description = (
                    item.findtext("description", "")
                    or item.findtext("{http://www.w3.org/2005/Atom}summary", "")
                ).strip()
                link = (
                    item.findtext("link", "")
                    or (item.find("link") is not None and item.find("link").get("href", ""))
                ).strip()
                pub_date = (
                    item.findtext("pubDate", "")
                    or item.findtext("{http://www.w3.org/2005/Atom}updated", "")
                    or ""
                )

                if not title or len(title) < 10:
                    continue

                doc_id = hashlib.sha256((source + title).encode()).hexdigest()[:20]
                if doc_id in sources_seen:
                    continue
                sources_seen.add(doc_id)

                sentiment = score_sentiment(title + " " + description)
                tickers = extract_tickers(title + " " + description)

                article = {
                    "id": doc_id,
                    "title": title,
                    "content": (description or title)[:5000],
                    "url": link,
                    "source": source,
                    "sentiment": sentiment,
                    "tickers": tickers,
                    "published": pub_date,
                    "ingested_at": time.time(),
                }
                results.append(article)
                count += 1

            logger.info(f"  {source}: {count} articles")
        except Exception as e:
            errors.append(f"{source}: {str(e)[:80]}")

    # Store in Redis for hot access
    try:
        from dotenv import load_dotenv

        load_dotenv("/app/.env", override=True)
        import os

        import redis

        r = redis.Redis(
            host="rmi-redis", port=6379, password=os.getenv("REDIS_PASSWORD"), decode_responses=True
        )

        # Index by time (sorted set)
        for a in results:
            r.zadd("rmi:news:index", {a["id"]: a["ingested_at"]})
            r.set(f"rmi:news:article:{a['id']}", json.dumps(a))

        # Stats
        stats = {
            "total_fetched": len(results),
            "sources_successful": len({a["source"] for a in results}),
            "sources_failed": len(errors),
            "errors": errors[:10],
            "last_ingest": time.time(),
            "sentiment_avg": sum(a["sentiment"] for a in results) / max(len(results), 1),
        }
        r.set("rmi:news:stats", json.dumps(stats))

        # Also store in Postgres for persistence
        try:
            import psycopg2

            pg = psycopg2.connect(
                host="rmi-postgres",
                port=5432,
                user="rmi",
                password=os.getenv("POSTGRES_PASSWORD"),
                dbname="rmi",
            )
            cur = pg.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS crypto_news (
                    id TEXT PRIMARY KEY,
                    title TEXT, content TEXT, url TEXT, source TEXT,
                    sentiment REAL, tickers TEXT[],
                    published TEXT, ingested_at DOUBLE PRECISION
                )
            """)
            for a in results:
                cur.execute(
                    """
                    INSERT INTO crypto_news (id, title, content, url, source, sentiment, tickers, published, ingested_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (id) DO UPDATE SET sentiment=EXCLUDED.sentiment, tickers=EXCLUDED.tickers
                """,
                    (
                        a["id"],
                        a["title"],
                        a["content"],
                        a["url"],
                        a["source"],
                        a["sentiment"],
                        a["tickers"],
                        a["published"],
                        a["ingested_at"],
                    ),
                )
            pg.commit()
            cur.close()
            pg.close()
            stats["in_postgres"] = len(results)
        except Exception as e:
            stats["postgres_error"] = str(e)[:100]

    except Exception as e:
        stats = {"total_fetched": len(results), "redis_error": str(e)[:100]}

    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [news] %(message)s")
    stats = fetch_all_feeds()
    logger.info(json.dumps(stats, indent=2))
