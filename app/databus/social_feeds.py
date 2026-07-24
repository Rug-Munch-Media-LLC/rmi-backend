"""
RMI Mega News v2 - Add Reddit + Twitter/Nitter RSS feeds
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("rmi.news.social")

# Reddit crypto subreddits (free RSS, no auth)
REDDIT_FEEDS = [
    ("reddit-cryptocurrency", "https://www.reddit.com/r/CryptoCurrency/.rss"),
    ("reddit-bitcoin", "https://www.reddit.com/r/Bitcoin/.rss"),
    ("reddit-ethereum", "https://www.reddit.com/r/ethereum/.rss"),
    ("reddit-solana", "https://www.reddit.com/r/solana/.rss"),
    ("reddit-cryptomarkets", "https://www.reddit.com/r/CryptoMarkets/.rss"),
    ("reddit-defi", "https://www.reddit.com/r/defi/.rss"),
    ("reddit-ethfinance", "https://www.reddit.com/r/ethfinance/.rss"),
    ("reddit-cryptotechnology", "https://www.reddit.com/r/CryptoTechnology/.rss"),
    ("reddit-altcoin", "https://www.reddit.com/r/altcoin/.rss"),
    ("reddit-web3", "https://www.reddit.com/r/web3/.rss"),
]

# Nitter instances for Twitter/X RSS (free, no auth, rotating)
NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
]

TWITTER_ACCOUNTS = [
    ("twitter-watanglass", "WatcherGuru"),
    ("twitter-cointelegraph", "Cointelegraph"),
    ("twitter-decryptmedia", "decryptmedia"),
    ("twitter-coindesk", "CoinDesk"),
    ("twitter-theblock", "TheBlock__"),
    ("twitter-bankless", "BanklessHQ"),
    ("twitter-defiignas", "DefiIgnas"),
    ("twitter-cryptokoryo", "CryptoKoryo"),
    ("twitter-lookonchain", "lookonchain"),
    ("twitter-whale_alert", "whale_alert"),
    ("twitter-cryptorank", "CryptoRank_io"),
    ("twitter-messari", "MessariCrypto"),
    ("twitter-glassnode", "glassnode"),
    ("twitter-defillama", "DefiLlama"),
    ("twitter-duneanalytics", "DuneAnalytics"),
]

def fetch_reddit(db_r=None) -> Any:
    """Fetch Reddit RSS feeds. Returns list of articles."""
    results = []
    for source, url in REDDIT_FEEDS:
        try:
            resp = httpx.get(url, timeout=15, headers={"User-Agent": "RMI/3.0 NewsBot"})
            if resp.status_code == 429:
                logger.warning(f"  {source}: rate limited, skipping")
                continue
            root = ET.fromstring(resp.content)
            items = root.findall(".//{http://www.w3.org/2005/Atom}entry")
            if not items:
                items = root.findall(".//item")
            for item in items[:25]:
                title = (item.findtext("title", "") or "").strip()
                content = (
                    item.findtext("content", "")
                    or item.findtext("description", "")
                    or item.findtext("{http://www.w3.org/2005/Atom}summary", "")
                    or ""
                ).strip()
                if not title or len(title) < 10:
                    continue
                doc_id = "reddit:" + hashlib.sha256((source + title).encode()).hexdigest()[:16]
                results.append(
                    {
                        "id": doc_id,
                        "title": f"[Reddit] {title}",
                        "content": content[:3000],
                        "url": "",
                        "source": source.split("-", 1)[1],
                        "sentiment": 0.0,
                        "tickers": [],
                        "published": "",
                        "ingested_at": time.time(),
                    }
                )
            logger.info(f"  {source}: {len(results)} articles")
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.warning(f"  {source}: {str(e)[:60]}")
    return results

def fetch_nitter(db_r=None) -> Any:
    """Fetch Twitter via Nitter RSS. Returns list of articles."""
    results = []
    for nitter_url in NITTER_INSTANCES:
        if results:
            break  # Stop once we get data from one instance
        for _feed_id, username in TWITTER_ACCOUNTS:
            try:
                url = f"{nitter_url}/{username}/rss"
                resp = httpx.get(url, timeout=15, headers={"User-Agent": "RMI/3.0 NewsBot"})
                if resp.status_code != 200:
                    continue
                root = ET.fromstring(resp.content)
                for item in root.findall(".//item")[:10]:
                    title = (item.findtext("title", "") or "").strip()
                    desc = (item.findtext("description", "") or "").strip()
                    if not title:
                        continue
                    doc_id = (
                        "twitter:" + hashlib.sha256((username + title).encode()).hexdigest()[:16]
                    )
                    results.append(
                        {
                            "id": doc_id,
                            "title": f"[X] {title}",
                            "content": desc[:2000],
                            "url": f"https://x.com/{username}",
                            "source": username,
                            "sentiment": 0.0,
                            "tickers": [],
                            "published": "",
                            "ingested_at": time.time(),
                        }
                    )
                logger.info(
                    f"  @{username}: {len([r for r in results if r['source'] == username])} tweets"
                )
            except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
                pass  # Try next instance
    return results

def merge_into_redis(articles, prefix="rmi:news") -> Any | int:
    """Merge social articles into existing Redis news index."""
    try:
        from dotenv import load_dotenv

        load_dotenv("/app/.env", override=True)
        import json
        import os

        import redis

        r = redis.Redis(
            host="rmi-redis", port=6379, password=os.getenv("REDIS_PASSWORD"), decode_responses=True
        )
        count = 0
        for a in articles:
            exists = r.exists(f"{prefix}:article:{a['id']}")
            if not exists:
                r.zadd(f"{prefix}:social:index", {a["id"]: a["ingested_at"]})
                r.set(f"{prefix}:article:{a['id']}", json.dumps(a))
                count += 1
        return count
    except (ConnectionError, OSError, TimeoutError) as e:
        logger.error(f"Redis merge failed: {e}")
        return 0

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [social] %(message)s")
    reddit = fetch_reddit()
    twitter = fetch_nitter()
    merged = merge_into_redis(reddit + twitter)
    logger.info(json.dumps({"reddit": len(reddit), "twitter": len(twitter), "merged_new": merged}))
