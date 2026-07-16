"""
RMI GLOBAL NEWS v4 - Google News, Bing, Reuters, NYT, BBC, Bloomberg, CNBC, WSJ, FT
Every major news organization's crypto coverage. The biggest on the internet.
"""

import hashlib
import json
import logging
import time

import httpx
from defusedxml import ElementTree as SafeET

from app.core.logging import get_logger

logger = get_logger("rmi.global")

# ═══════════════════════════════════════════════════════
# GOOGLE NEWS - Crypto section (free RSS)
# ═══════════════════════════════════════════════════════
GOOGLE_NEWS_FEEDS = [
    (
        "google-crypto",
        "https://news.google.com/rss/search?q=cryptocurrency+OR+bitcoin+OR+ethereum&hl=en-US&gl=US&ceid=US:en",
    ),
    (
        "google-defi",
        "https://news.google.com/rss/search?q=defi+OR+web3+OR+blockchain&hl=en-US&gl=US&ceid=US:en",
    ),
    (
        "google-regulation",
        "https://news.google.com/rss/search?q=crypto+regulation+OR+SEC+crypto+OR+crypto+bill&hl=en-US&gl=US&ceid=US:en",
    ),
    (
        "google-nft",
        "https://news.google.com/rss/search?q=NFT+OR+tokenization+OR+digital+assets&hl=en-US&gl=US&ceid=US:en",
    ),
]

# ═══════════════════════════════════════════════════════
# BING NEWS - Crypto section (free RSS)
# ═══════════════════════════════════════════════════════
BING_NEWS_FEEDS = [
    (
        "bing-crypto",
        "https://www.bing.com/news/search?q=cryptocurrency+bitcoin+ethereum&format=rss",
    ),
    ("bing-blockchain", "https://www.bing.com/news/search?q=blockchain+web3+defi&format=rss"),
]

# ═══════════════════════════════════════════════════════
# MAJOR NEWS ORGS - All with crypto RSS
# ═══════════════════════════════════════════════════════
MAJOR_NEWS = [
    # Reuters
    (
        "reuters-crypto",
        "https://www.reuters.com/arc/outboundfeeds/v3/all/?outputType=xml&section=cryptocurrency",
    ),
    (
        "reuters-tech",
        "https://www.reuters.com/arc/outboundfeeds/v3/all/?outputType=xml&section=technology",
    ),
    # Bloomberg
    ("bloomberg-crypto", "https://feeds.bloomberg.com/markets/crypto.rss"),
    ("bloomberg-tech", "https://feeds.bloomberg.com/technology/news.rss"),
    # CNBC
    ("cnbc-crypto", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    # BBC
    ("bbc-tech", "https://feeds.bbci.co.uk/news/technology/rss.xml"),
    ("bbc-business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
    # NYT
    ("nyt-technology", "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml"),
    ("nyt-business", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
    # WSJ
    ("wsj-tech", "https://feeds.a.dj.com/rss/RSSWSJD.xml"),
    ("wsj-markets", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
    # Financial Times
    ("ft-markets", "https://www.ft.com/markets/cryptofinance?format=rss"),
    ("ft-tech", "https://www.ft.com/technology?format=rss"),
    # Yahoo Finance
    ("yahoo-crypto", "https://finance.yahoo.com/news/topic/crypto/rss"),
    # MarketWatch
    ("marketwatch-crypto", "https://feeds.marketwatch.com/marketwatch/topics/cryptocurrency/"),
    # Forbes
    ("forbes-crypto", "https://www.forbes.com/crypto-blockchain/feed/"),
    ("forbes-digital-assets", "https://www.forbes.com/digital-assets/feed/"),
    # Fortune
    ("fortune-crypto", "https://fortune.com/tag/cryptocurrency/feed/"),
    # Business Insider
    ("bi-crypto", "https://markets.businessinsider.com/rss/news/cryptocurrencies"),
    # The Guardian
    ("guardian-crypto", "https://www.theguardian.com/technology/cryptocurrencies/rss"),
    # Wired
    ("wired-crypto", "https://www.wired.com/feed/tag/cryptocurrency/rss"),
    # TechCrunch
    ("techcrunch-crypto", "https://techcrunch.com/tag/cryptocurrency/feed/"),
    # The Verge
    ("verge-crypto", "https://www.theverge.com/rss/crypto/index.xml"),
    # Ars Technica
    ("ars-crypto", "https://feeds.arstechnica.com/arstechnica/technology"),
    # MIT Tech Review
    ("mit-blockchain", "https://www.technologyreview.com/topic/blockchain/feed"),
    # The Economist
    ("economist-fintech", "https://www.economist.com/finance-and-economics/rss.xml"),
    # AP News
    ("ap-crypto", "https://www.mysanantonio.com/rss/feed/cryptocurrency-28803.php"),
    # Al Jazeera
    ("aljazeera-tech", "https://www.aljazeera.com/xml/rss/technology.xml"),
    # South China Morning Post
    ("scmp-crypto", "https://www.scmp.com/rss/91/feed"),
]


def fetch_rss(source, url, limit=30):
    """Fetch RSS and return articles."""
    results = []
    try:
        resp = httpx.get(url, timeout=15, headers={"User-Agent": "RMI/5.0 GlobalBot"})
        if resp.status_code == 429:
            logger.warning(f"  {source}: RATE LIMITED")
            return results
        if resp.status_code != 200:
            return results

        # Some feeds have HTML entities that break XML parsing
        try:
            root = SafeET.fromstring(resp.content)
        except Exception:
            try:
                cleaned = resp.text.replace("&", "&amp;").replace("&amp;amp;", "&amp;")
                root = SafeET.fromstring(cleaned.encode())
            except Exception:
                return results

        items = (
            root.findall(".//item")
            or root.findall(".//{http://www.w3.org/2005/Atom}entry")
            or root.findall(".//{http://purl.org/rss/1.0/}item")
        )

        for item in items[:limit]:
            title = (item.findtext("title", "") or "").strip()
            desc = (
                item.findtext("description", "")
                or item.findtext("{http://www.w3.org/2005/Atom}summary", "")
                or ""
            ).strip()
            link = (
                item.findtext("link", "")
                or (
                    item.find("link") is not None
                    and (item.find("link").get("href", "") or item.find("link").text)
                )
                or ""
            ).strip()

            if title and len(title) > 10:
                # Filter for crypto relevance
                crypto_keywords = [
                    "crypto",
                    "bitcoin",
                    "ethereum",
                    "blockchain",
                    "defi",
                    "web3",
                    "token",
                    "nft",
                    "digital asset",
                    "stablecoin",
                    "mining",
                    "defi",
                    "exchange",
                    "wallet",
                    "smart contract",
                    "dao",
                    "metaverse",
                ]
                text = (title + " " + desc).lower()
                if any(kw in text for kw in crypto_keywords):
                    doc_id = "global:" + hashlib.sha256((source + title).encode()).hexdigest()[:16]
                    results.append(
                        {
                            "id": doc_id,
                            "title": f"[{source}] {title}",
                            "content": desc[:3000],
                            "url": link,
                            "source": source,
                            "sentiment": 0.0,
                            "tickers": [],
                            "published": "",
                            "ingested_at": time.time(),
                            "category": "global_news",
                        }
                    )

        if results:
            logger.info(f"  {source}: {len(results)} crypto articles")
    except Exception as e:
        logger.debug(f"  {source}: {str(e)[:80]}")
    return results


def fetch_all_global():
    """Fetch all global news sources."""
    all_articles = []

    logger.info("GOOGLE NEWS...")
    for s, u in GOOGLE_NEWS_FEEDS:
        all_articles.extend(fetch_rss(s, u))

    logger.info("BING NEWS...")
    for s, u in BING_NEWS_FEEDS:
        all_articles.extend(fetch_rss(s, u))

    logger.info("MAJOR NEWS ORGS...")
    for s, u in MAJOR_NEWS:
        all_articles.extend(fetch_rss(s, u, limit=20))

    # Store
    if all_articles:
        try:
            from dotenv import load_dotenv

            load_dotenv("/app/.env", override=True)
            import os

            import psycopg2
            import redis

            r = redis.Redis(
                host="rmi-redis",
                port=6379,
                password=os.getenv("REDIS_PASSWORD"),
                decode_responses=True,
            )

            new_count = 0
            for a in all_articles:
                if not r.exists(f"rmi:news:article:{a['id']}"):
                    r.zadd("rmi:news:global:index", {a["id"]: a["ingested_at"]})
                    r.set(f"rmi:news:article:{a['id']}", json.dumps(a))
                    new_count += 1

            # Update stats
            stats = json.loads(r.get("rmi:news:stats") or "{}")
            stats["global_feeds"] = len(MAJOR_NEWS) + len(GOOGLE_NEWS_FEEDS) + len(BING_NEWS_FEEDS)
            stats["global_articles"] = stats.get("global_articles", 0) + len(all_articles)
            r.set("rmi:news:stats", json.dumps(stats))

            # Postgres
            try:
                pg = psycopg2.connect(
                    host="rmi-postgres",
                    port=5432,
                    user="rmi",
                    password=os.getenv("POSTGRES_PASSWORD"),
                    dbname="rmi",
                )
                cur = pg.cursor()
                cur.execute(
                    "CREATE TABLE IF NOT EXISTS crypto_news (id TEXT PRIMARY KEY, title TEXT, content TEXT, source TEXT, sentiment REAL, ingested_at DOUBLE PRECISION, category TEXT DEFAULT 'news')"
                )
                for a in all_articles:
                    cur.execute(
                        "INSERT INTO crypto_news (id, title, content, source, sentiment, ingested_at, category) VALUES (%s,%s,%s,%s,%s,%s,'global') ON CONFLICT (id) DO NOTHING",
                        (a["id"], a["title"], a["content"], a["source"], 0.0, a["ingested_at"]),
                    )
                pg.commit()
                cur.close()
                pg.close()
            except Exception as e:
                logger.error(f"PG: {e}")

            return {
                "collected": len(all_articles),
                "new": new_count,
                "sources": len(MAJOR_NEWS) + len(GOOGLE_NEWS_FEEDS) + len(BING_NEWS_FEEDS),
            }
        except Exception as e:
            return {"error": str(e), "collected": len(all_articles)}
    return {"collected": 0}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [global] %(message)s")
    result = fetch_all_global()
    logger.info(json.dumps(result, indent=2))
