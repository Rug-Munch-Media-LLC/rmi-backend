"""
RMI MEGA SCRAPER v3 - Substack, Mirror.xyz, Medium, Blog Scrapers
Grabs EVERYTHING crypto. Biggest free news DB on the internet.
"""

import hashlib
import json
import logging
import time

import httpx
from defusedxml import ElementTree as SafeET

from app.core.logging import get_logger
from typing import Any

logger = get_logger("rmi.scraper")

# ═══════════════════════════════════════════════════════
# SUBSTACK - 50+ top crypto newsletters (free RSS)
# ═══════════════════════════════════════════════════════
SUBSTACK_FEEDS = [
    ("substack-bankless", "https://substack.com/@bankless/feed"),
    ("substack-milkroad", "https://substack.com/@milkroad/feed"),
    ("substack-messaricrypto", "https://substack.com/@messari/feed"),
    ("substack-defiweekly", "https://substack.com/@defiweekly/feed"),
    ("substack-thedefiedge", "https://substack.com/@thedefiedge/feed"),
    ("substack-cryptopragmatist", "https://substack.com/@cryptopragmatist/feed"),
    ("substack-blockworks", "https://substack.com/@blockworks/feed"),
    ("substack-coindesk", "https://substack.com/@coindesk/feed"),
    ("substack-reflexivity", "https://substack.com/@reflexivityresearch/feed"),
    ("substack-delphi", "https://substack.com/@delphidigital/feed"),
    ("substack-0xresearch", "https://substack.com/@0xresearch/feed"),
    ("substack-decentralised", "https://substack.com/@decentralisedco/feed"),
    ("substack-cryptoventure", "https://substack.com/@cryptoventure/feed"),
    ("substack-onchaintimes", "https://substack.com/@onchaintimes/feed"),
    ("substack-web3isgoinggreat", "https://substack.com/@web3isgreat/feed"),
    ("substack-dirtroads", "https://substack.com/@dirtroads/feed"),
    ("substack-frontiertech", "https://substack.com/@frontiertech/feed"),
    ("substack-chainalysis", "https://substack.com/@chainalysis/feed"),
    ("substack-elliptic", "https://substack.com/@elliptic/feed"),
    ("substack-trmlabs", "https://substack.com/@trmlabs/feed"),
    ("substack-thetie", "https://substack.com/@thetie/feed"),
    ("substack-glassnode", "https://substack.com/@glassnode/feed"),
    ("substack-nansen", "https://substack.com/@nansen/feed"),
    ("substack-arkham", "https://substack.com/@arkhamintel/feed"),
    ("substack-paradigm", "https://substack.com/@paradigm/feed"),
    ("substack-a16zcrypto", "https://substack.com/@a16zcrypto/feed"),
    ("substack-dragonfly", "https://substack.com/@dragonfly/feed"),
    ("substack-pantera", "https://substack.com/@panteracapital/feed"),
    ("substack-multicoin", "https://substack.com/@multicoincap/feed"),
    ("substack-variant", "https://substack.com/@variantfund/feed"),
    ("substack-electriccapital", "https://substack.com/@electriccapital/feed"),
    ("substack-coinfund", "https://substack.com/@coinfund/feed"),
    ("substack-framework", "https://substack.com/@frameworkventures/feed"),
    ("substack-1confirmation", "https://substack.com/@1confirmation/feed"),
    ("substack-placeholder", "https://substack.com/@placeholder/feed"),
    ("substack-union-square", "https://substack.com/@usv/feed"),
]

# ═══════════════════════════════════════════════════════
# MIRROR.XYZ - Decentralized crypto publishing
# ═══════════════════════════════════════════════════════
MIRROR_FEEDS = [
    ("mirror-weekly", "https://mirror.xyz/0x/feed"),
    # Individual writers (their RSS)
    ("mirror-zoranjoc", "https://zoranjoc.mirror.xyz/feed"),
    ("mirror-liam", "https://liam.mirror.xyz/feed"),
    ("mirror-dcbuilder", "https://dcbuilder.mirror.xyz/feed"),
    ("mirror-pacman", "https://pacman.mirror.xyz/feed"),
    ("mirror-nick", "https://nick.mirror.xyz/feed"),
]

# ═══════════════════════════════════════════════════════
# MEDIUM - Crypto publications
# ═══════════════════════════════════════════════════════
MEDIUM_FEEDS = [
    ("medium-coinfund", "https://blog.coinfund.io/feed"),
    ("medium-a16z", "https://a16zcrypto.com/feed/"),
    ("medium-paradigm", "https://medium.com/feed/paradigm"),
    ("medium-dragonfly", "https://medium.com/feed/dragonfly-research"),
    ("medium-multicoin", "https://multicoin.capital/feed/"),
    ("medium-electric", "https://medium.com/feed/electric-capital"),
    ("medium-coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
]

# ═══════════════════════════════════════════════════════
# CRYPTO RESEARCH / TECH BLOGS
# ═══════════════════════════════════════════════════════
RESEARCH_FEEDS = [
    ("arxiv-crypto", "https://rss.arxiv.org/rss/cs.CR"),
    ("github-trending-crypto", "https://github.com/trending?l=solidity&since=daily"),
    ("cryptopanic-news", "https://cryptopanic.com/news/rss/"),
    ("coingecko-research", "https://www.coingecko.com/en/research/rss"),
    ("thelafeed", "https://www.thela.news/feed"),
    ("cryptonewsbtc", "https://www.newsbtc.com/feed/"),
    ("cryptodailyuk", "https://cryptodaily.co.uk/feed/"),
    ("cryptoglobe", "https://www.cryptoglobe.com/feed/"),
    ("coinculture", "https://coinculture.com/au/feed/"),
    ("cryptopress", "https://cryptopress.com/feed/"),
    ("cryptopurview", "https://cryptopurview.com/feed/"),
    ("coinchapter", "https://coinchapter.com/feed/"),
    ("cryptowisser", "https://www.cryptowisser.com/feed/"),
    ("blockster", "https://blockster.com/feed/"),
    ("crypto-news-feed", "https://www.crypto-news-feed.com/feed/"),
    ("coininsider", "https://www.coininsider.com/feed/"),
    ("dailycoin", "https://dailycoin.com/feed/"),
    ("thecryptoupdates", "https://thecryptoupdates.com/feed/"),
    ("cryptoflies", "https://cryptoflies.com/feed/"),
    ("coincentral", "https://coincentral.com/feed/"),
]


def fetch_generic_feeds(feed_list, prefix="", label="generic") -> Any:
    """Fetch any list of RSS feeds. Returns articles."""
    results = []
    for source, url in feed_list:
        try:
            resp = httpx.get(url, timeout=15, headers={"User-Agent": "RMI/4.0 MegaScraper"})
            if resp.status_code != 200:
                continue
            root = SafeET.fromstring(resp.content)
            items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
            for item in items[:20]:
                title = (item.findtext("title", "") or "").strip()
                content = (
                    item.findtext("description", "")
                    or item.findtext("content", "")
                    or item.findtext("{http://www.w3.org/2005/Atom}summary", "")
                    or ""
                ).strip()
                if title and len(title) > 5:
                    doc_id = (
                        f"{prefix}{source}:"
                        + hashlib.sha256((source + title).encode()).hexdigest()[:16]
                    )
                    results.append(
                        {
                            "id": doc_id,
                            "title": title,
                            "content": content[:3000],
                            "url": url,
                            "source": source,
                            "sentiment": 0.0,
                            "tickers": [],
                            "published": "",
                            "ingested_at": time.time(),
                        }
                    )
            logger.info(f"  {prefix}{source}: {len(results)} articles")
        except Exception as e:
            logger.debug(f"  {prefix}{source}: {str(e)[:60]}")
    return results


def store_all() -> dict[str, int] | dict[str, str]:
    """Fetch all sources and store in Redis + Postgres."""
    all_articles = []
    all_articles.extend(fetch_generic_feeds(SUBSTACK_FEEDS, "[Substack] ", "substack"))
    all_articles.extend(fetch_generic_feeds(MIRROR_FEEDS, "[Mirror] ", "mirror"))
    all_articles.extend(fetch_generic_feeds(MEDIUM_FEEDS, "[Medium] ", "medium"))
    all_articles.extend(fetch_generic_feeds(RESEARCH_FEEDS, "[Research] ", "research"))

    try:
        from dotenv import load_dotenv

        load_dotenv("/app/.env", override=True)
        import os

        import psycopg2
        import redis

        r = redis.Redis(
            host="rmi-redis", port=6379, password=os.getenv("REDIS_PASSWORD"), decode_responses=True
        )

        # Dedup and store
        count = 0
        for a in all_articles:
            if not r.exists(f"rmi:news:article:{a['id']}"):
                r.zadd("rmi:news:substack:index", {a["id"]: a["ingested_at"]})
                r.set(f"rmi:news:article:{a['id']}", json.dumps(a))
                count += 1

        # Update stats
        existing = json.loads(r.get("rmi:news:stats") or "{}")
        existing["substack_articles"] = count
        existing["total_sources"] = (
            existing.get("total_sources", 0)
            + len(SUBSTACK_FEEDS)
            + len(MIRROR_FEEDS)
            + len(MEDIUM_FEEDS)
            + len(RESEARCH_FEEDS)
        )
        r.set("rmi:news:stats", json.dumps(existing))

        # Also Postgres
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
                "CREATE TABLE IF NOT EXISTS crypto_news (id TEXT PRIMARY KEY, title TEXT, content TEXT, source TEXT, sentiment REAL, ingested_at DOUBLE PRECISION, category TEXT DEFAULT 'research')"
            )
            for a in all_articles:
                cur.execute(
                    "INSERT INTO crypto_news (id, title, content, source, sentiment, ingested_at, category) VALUES (%s,%s,%s,%s,%s,%s,'research') ON CONFLICT (id) DO NOTHING",
                    (a["id"], a["title"], a["content"], a["source"], 0.0, a["ingested_at"]),
                )
            pg.commit()
            cur.close()
            pg.close()
        except Exception as e:
            logger.error(f"Postgres: {e}")

        return {
            "substack": len(SUBSTACK_FEEDS),
            "mirror": len(MIRROR_FEEDS),
            "medium": len(MEDIUM_FEEDS),
            "research": len(RESEARCH_FEEDS),
            "new_articles": count,
        }
    except Exception as e:
        logger.error(f"Store failed: {e}")
        return {"error": str(e), "articles_collected": len(all_articles)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [mega] %(message)s")
    result = store_all()
    logger.info(json.dumps(result, indent=2))
