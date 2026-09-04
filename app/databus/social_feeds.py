"""
RMI Mega News v2 - Add Reddit + Twitter/Nitter RSS feeds
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from defusedxml import ElementTree as SafeET  # protects RSS parsing against XML entity-expansion attacks

from app.core.logging import get_logger

logger = get_logger("rmi.news.social")

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

# ---------------------------------------------------------------------------
# Unified RSS feed registry (200+ sources).
#
# Each entry is a tuple: (name, url, category, enabled)
#   category: news | security | defi | governance | newsletter | rekt | reddit
#   enabled:  bool — set False to disable a feed without deleting it.
#
# URLs are the canonical RSS endpoints for each outlet. Many are behind
# Cloudflare and return 403/429 to datacenter egress IPs; httpx follows
# 3xx redirects and the fetcher treats non-200 as a skip (never a crash).
# ---------------------------------------------------------------------------

# 1. Crypto news outlets + protocol blogs (news)
NEWS_FEEDS = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", "news", True),
    ("CoinTelegraph", "https://cointelegraph.com/rss", "news", True),
    ("The Block", "https://www.theblock.co/rss.xml", "news", True),
    ("Decrypt", "https://decrypt.co/feed", "news", True),
    ("Bankless", "https://www.bankless.com/feed", "news", True),
    ("The Defiant", "https://thedefiant.io/feed", "news", True),
    ("Messari", "https://messari.io/feed", "news", True),
    ("CryptoSlate", "https://cryptoslate.com/feed/", "news", True),
    ("CryptoBriefing", "https://cryptobriefing.com/feed/", "news", True),
    ("BeInCrypto", "https://beincrypto.com/feed/", "news", True),
    ("NewsBTC", "https://www.newsbtc.com/feed/", "news", True),
    ("Bitcoinist", "https://bitcoinist.com/feed/", "news", True),
    ("CryptoPotato", "https://cryptopotato.com/feed/", "news", True),
    ("The Daily Hodl", "https://dailyhodl.com/feed/", "news", True),
    ("CryptoNews", "https://cryptonews.com/feed/", "news", True),
    ("CryptoDaily", "https://cryptodaily.co.uk/feed/", "news", True),
    ("NullTX", "https://nulltx.com/feed/", "news", True),
    ("CryptoGlobe", "https://www.cryptoglobe.com/feed/", "news", True),
    ("CryptoMode", "https://cryptomode.com/feed/", "news", True),
    ("CryptoNewsZ", "https://www.cryptonewsz.com/feed/", "news", True),
    ("CryptoRank", "https://cryptorank.io/news/feed", "news", True),
    ("CoinGape", "https://coingape.com/feed/", "news", True),
    ("CoinJournal", "https://coinjournal.net/feed/", "news", True),
    ("Bitcoin.com News", "https://news.bitcoin.com/feed", "news", True),
    ("Ethereum World News", "https://ethereumworldnews.com/feed/", "news", True),
    ("Bitcoin Magazine", "https://bitcoinmagazine.com/feed", "news", True),
    ("AMBCrypto", "https://ambcrypto.com/feed/", "news", True),
    ("U.Today", "https://u.today/rss", "news", True),
    ("ZyCrypto", "https://zycrypto.com/feed/", "news", True),
    ("Unchained", "https://unchainedcrypto.com/feed/", "news", True),
    ("Blockworks", "https://blockworks.co/feed", "news", True),
    ("Protos", "https://protos.com/feed/", "news", True),
    ("CoinPedia", "https://coinpedia.org/feed/", "news", True),
    ("Finbold", "https://finbold.com/feed/", "news", True),
    ("Crypto News Flash", "https://www.crypto-news-flash.com/feed/", "news", True),
    ("Live Bitcoin News", "https://www.livebitcoinnews.com/feed/", "news", True),
    ("InsideBitcoins", "https://insidebitcoins.com/feed/", "news", True),
    ("Trustnodes", "https://www.trustnodes.com/feed", "news", True),
    ("CryptoReporter", "https://www.crypto-reporter.com/feed/", "news", True),
    ("CryptoAdventure", "https://cryptoadventure.org/feed/", "news", True),
    ("CryptoPolita", "https://www.cryptopolitan.com/feed/", "news", True),
    ("DailyCoin", "https://dailycoin.com/feed/", "news", True),
    ("CoinSpeaker", "https://www.coinspeaker.com/feed/", "news", True),
    ("DLNews", "https://www.dlnews.com/arc/outboundfeeds/rss/", "news", True),
    ("Blockonomi", "https://blockonomi.com/feed/", "news", True),
    ("DefiLlama Blog", "https://defillama.com/feed", "news", True),
    ("Uniswap Blog", "https://blog.uniswap.org/rss.xml", "news", True),
    ("Aave Blog", "https://aave.com/blog/rss.xml", "news", True),
    ("MakerDAO Blog", "https://blog.makerdao.com/feed/", "news", True),
    ("Lido Blog", "https://blog.lido.fi/feed", "news", True),
    ("Chainlink Blog", "https://blog.chain.link/feed/", "news", True),
    ("Wormhole Blog", "https://blog.wormhole.com/feed.xml", "news", True),
    ("LayerZero Blog", "https://blog.layerzero.network/feed.xml", "news", True),
    ("Pyth Network Blog", "https://blog.pyth.network/feed.xml", "news", True),
    ("Helius Blog", "https://blog.helius.dev/feed.xml", "news", True),
    ("Jito Blog", "https://blog.jito.wtf/feed.xml", "news", True),
    ("Jupiter Blog", "https://blog.jupiter.com/feed.xml", "news", True),
    ("Solana Blog", "https://solana.com/news/rss.xml", "news", True),
    ("Sui Blog", "https://blog.sui.io/feed", "news", True),
    ("Aptos Blog", "https://aptoslabs.medium.com/feed", "news", True),
    ("TON Blog", "https://blog.ton.org/feed.xml", "news", True),
    ("Near Blog", "https://near.org/blog/feed/", "news", True),
    ("Arbitrum Blog", "https://blog.arbitrum.io/feed", "news", True),
    ("Optimism Blog", "https://blog.optimism.io/feed.xml", "news", True),
    ("Base Blog", "https://blog.base.org/feed", "news", True),
    ("Polygon Blog", "https://blog.polygon.technology/feed.xml", "news", True),
    ("zkSync Blog", "https://blog.zksync.io/feed.xml", "news", True),
    ("Scroll Blog", "https://blog.scroll.io/feed.xml", "news", True),
    ("Mantle Blog", "https://blog.mantle.xyz/feed.xml", "news", True),
    ("Blast Blog", "https://blog.blast.io/feed", "news", True),
    ("Celestia Blog", "https://blog.celestia.org/feed", "news", True),
    ("EigenLayer Blog", "https://blog.eigenlayer.xyz/feed.xml", "news", True),
]

# 2. Security / audit / threat-intel blogs (security)
SECURITY_FEEDS = [
    ("PeckShield", "https://peckshield.medium.com/feed", "security", True),
    ("SlowMist", "https://slowmist.medium.com/feed", "security", True),
    ("Beosin", "https://beosin.com/feed", "security", True),
    ("CertiK", "https://certik.medium.com/feed", "security", True),
    ("Chainalysis", "https://chainalysis.com/blog/feed", "security", True),
    ("Elliptic", "https://www.elliptic.co/blog/rss.xml", "security", True),
    ("TRM Labs", "https://www.trmlabs.com/blog/rss.xml", "security", True),
    ("HashEx", "https://hashex.org/feed", "security", True),
    ("Immunefi", "https://immunefi.com/blog/feed", "security", True),
    ("Halborn", "https://halborn.com/feed.xml", "security", True),
    ("OpenZeppelin", "https://blog.openzeppelin.com/rss.xml", "security", True),
    ("Trail of Bits", "https://blog.trailofbits.com/feed", "security", True),
    ("Consensys Diligence", "https://consensys.io/diligence/blog/feed/", "security", True),
    ("Sigma Prime", "https://blog.sigmaprime.io/feed", "security", True),
    ("Quantstamp", "https://quantstamp.com/blog/rss.xml", "security", True),
    ("Hacken", "https://hacken.io/feed/", "security", True),
    ("Kudelski Security", "https://research.kudelskisecurity.com/feed/", "security", True),
    ("Least Authority", "https://leastauthority.com/blog/feed/", "security", True),
    ("Solidity Finance", "https://www.solidity.finance/blog/feed/", "security", True),
    ("Trust Security", "https://www.trust-security.xyz/feed/", "security", True),
    ("The Hacker News", "https://feeds.feedburner.com/TheHackersNews", "security", True),
    ("BleepingComputer", "https://www.bleepingcomputer.com/feed/", "security", True),
    ("Krebs on Security", "https://krebsonsecurity.com/feed/", "security", True),
    ("Dark Reading", "https://www.darkreading.com/rss.xml", "security", True),
    ("Hexens", "https://hexens.io/rss.xml", "security", True),
    ("Dedaub", "https://medium.com/feed/dedaub", "security", True),
    ("Osec", "https://osec.io/rss.xml", "security", True),
    ("Web3 is Going Great", "https://www.web3isgoinggreat.com/feed", "security", True),
    ("Crypto Security", "https://cryptosecurity.substack.com/feed", "security", True),
    ("Consensys Blog", "https://consensys.io/blog/feed/", "security", True),
]

# 3. DeFi analytics / on-chain data (defi)
DEFI_FEEDS = [
    ("DefiLlama", "https://defillama.com/feed", "defi", True),
    ("Dune Analytics", "https://dune.com/blog/rss", "defi", True),
    ("Nansen", "https://nansen.ai/blog/rss.xml", "defi", True),
    ("Glassnode", "https://insights.glassnode.com/feed/", "defi", True),
    ("Santiment", "https://medium.com/feed/santiment", "defi", True),
    ("Messari", "https://messari.io/feed", "defi", True),
    ("Token Terminal", "https://tokenterminal.com/blog/rss.xml", "defi", True),
    ("DappRadar", "https://dappradar.com/blog/feed", "defi", True),
    ("IntoTheBlock", "https://medium.com/feed/intotheblock", "defi", True),
    ("CoinMetrics", "https://coinmetrics.io/blog/feed/", "defi", True),
    ("Yearn Finance", "https://blog.yearn.fi/feed/", "defi", True),
    ("Synthetix", "https://blog.synthetix.io/feed", "defi", True),
    ("Injective", "https://blog.injective.com/feed", "defi", True),
    ("Morpho", "https://medium.com/feed/morpho-labs", "defi", True),
    ("Balancer", "https://medium.com/feed/balancer-protocol", "defi", True),
    ("Compound", "https://medium.com/feed/compound-finance", "defi", True),
    ("Venus", "https://medium.com/feed/venusprotocol", "defi", True),
    ("PancakeSwap", "https://pancakeswap.medium.com/feed", "defi", True),
    ("Dose of DeFi", "https://doseofdefi.substack.com/feed", "defi", True),
    ("DeFi Education", "https://defieducation.substack.com/feed", "defi", True),
    ("Bitquery", "https://bitquery.io/feed", "defi", True),
    ("TokenInsight", "https://tokeninsight.substack.com/feed", "defi", True),
]

# 4. Reddit crypto subreddits (reddit) — expanded from 10 to 24
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
    ("reddit-chainlink", "https://www.reddit.com/r/Chainlink/.rss"),
    ("reddit-polkadot", "https://www.reddit.com/r/Polkadot/.rss"),
    ("reddit-cardano", "https://www.reddit.com/r/Cardano/.rss"),
    ("reddit-avax", "https://www.reddit.com/r/Avax/.rss"),
    ("reddit-ethtrader", "https://www.reddit.com/r/ethtrader/.rss"),
    ("reddit-btc", "https://www.reddit.com/r/btc/.rss"),
    ("reddit-dogecoin", "https://www.reddit.com/r/dogecoin/.rss"),
    ("reddit-nft", "https://www.reddit.com/r/nft/.rss"),
    ("reddit-0xpolygon", "https://www.reddit.com/r/0xPolygon/.rss"),
    ("reddit-arbitrum", "https://www.reddit.com/r/Arbitrum/.rss"),
    ("reddit-optimism", "https://www.reddit.com/r/optimism/.rss"),
    ("reddit-cosmosnetwork", "https://www.reddit.com/r/cosmosnetwork/.rss"),
    ("reddit-ethereumclassic", "https://www.reddit.com/r/EthereumClassic/.rss"),
    ("reddit-stellar", "https://www.reddit.com/r/Stellar/.rss"),
]

# 5. Protocol governance forums (governance) — Discourse /posts.rss
GOVERNANCE_FEEDS = [
    ("Uniswap Governance", "https://gov.uniswap.org/posts.rss", "governance", True),
    ("Aave Governance", "https://governance.aave.com/posts.rss", "governance", True),
    ("Compound Governance", "https://www.comp.xyz/posts.rss", "governance", True),
    ("MakerDAO Forum", "https://forum.makerdao.com/posts.rss", "governance", True),
    ("Lido Governance", "https://research.lido.fi/posts.rss", "governance", True),
    ("EigenLayer Forum", "https://forum.eigenlayer.xyz/posts.rss", "governance", True),
    ("Arbitrum Governance", "https://forum.arbitrum.foundation/posts.rss", "governance", True),
    ("Optimism Governance", "https://gov.optimism.io/posts.rss", "governance", True),
    ("Polygon Governance", "https://forum.polygon.technology/posts.rss", "governance", True),
    ("Scroll Governance", "https://forum.scroll.io/posts.rss", "governance", True),
    ("zkSync Governance", "https://forum.zksync.io/posts.rss", "governance", True),
    ("Ethereum Magicians", "https://ethereum-magicians.org/posts.rss", "governance", True),
    ("Ethresear", "https://ethresear.ch/posts.rss", "governance", True),
    ("OpenZeppelin Forum", "https://forum.openzeppelin.com/posts.rss", "governance", True),
    ("Flashbots Collective", "https://collective.flashbots.net/latest.rss", "governance", True),
    ("Balancer Governance", "https://forum.balancer.fi/posts.rss", "governance", True),
    ("Curve Governance", "https://gov.curve.fi/posts.rss", "governance", True),
    ("Yearn Governance", "https://gov.yearn.finance/posts.rss", "governance", True),
    ("ENS Governance", "https://discuss.ens.domains/posts.rss", "governance", True),
    ("Gitcoin Governance", "https://gov.gitcoin.co/posts.rss", "governance", True),
    ("Aragon Forum", "https://forum.aragon.org/posts.rss", "governance", True),
    ("Gnosis Forum", "https://forum.gnosis.io/posts.rss", "governance", True),
    ("Safe Governance", "https://forum.safe.global/posts.rss", "governance", True),
    ("Starknet Governance", "https://community.starknet.io/posts.rss", "governance", True),
    ("Celestia Governance", "https://forum.celestia.org/posts.rss", "governance", True),
    ("Sui Governance", "https://forum.sui.io/posts.rss", "governance", True),
    ("Aptos Governance", "https://forum.aptosfoundation.org/posts.rss", "governance", True),
    ("Near Governance", "https://gov.near.org/posts.rss", "governance", True),
    ("Solana Governance", "https://forum.solana.com/posts.rss", "governance", True),
    ("Cosmos Governance", "https://forum.cosmos.network/posts.rss", "governance", True),
    ("Polkadot Governance", "https://forum.polkadot.network/posts.rss", "governance", True),
]

# 6. Newsletters (newsletter)
NEWSLETTER_FEEDS = [
    ("Bankless", "https://www.bankless.com/feed", "newsletter", True),
    ("The Defiant", "https://thedefiant.io/feed", "newsletter", True),
    ("Messari", "https://messari.io/feed", "newsletter", True),
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", "newsletter", True),
    ("Blockworks", "https://blockworks.co/feed", "newsletter", True),
    ("Week in Ethereum", "https://weekinethereum.substack.com/feed", "newsletter", True),
    ("Solana Weekly", "https://solana.com/news/rss.xml", "newsletter", True),
    ("BanklessDAO", "https://banklessdao.substack.com/feed", "newsletter", True),
    ("The Defiant Substack", "https://thedefiant.substack.com/feed", "newsletter", True),
    ("CryptoHayes", "https://cryptohayes.substack.com/feed", "newsletter", True),
    ("TokenInsight", "https://tokeninsight.substack.com/feed", "newsletter", True),
    ("Dose of DeFi", "https://doseofdefi.substack.com/feed", "newsletter", True),
    ("DeFi Education", "https://defieducation.substack.com/feed", "newsletter", True),
    ("Crypto Security", "https://cryptosecurity.substack.com/feed", "newsletter", True),
    ("Paragraph Bankless", "https://paragraph.xyz/@bankless/rss", "newsletter", True),
    ("Unchained", "https://unchainedcrypto.com/feed/", "newsletter", True),
    ("The Block", "https://www.theblock.co/rss.xml", "newsletter", True),
    ("Decrypt", "https://decrypt.co/feed", "newsletter", True),
    ("CoinTelegraph", "https://cointelegraph.com/rss", "newsletter", True),
    ("CryptoSlate", "https://cryptoslate.com/feed/", "newsletter", True),
    ("Blockworks Research", "https://blockworksresearch.com/feed", "newsletter", True),
]

# 7. Rekt / exploit / hack trackers (rekt)
REKT_FEEDS = [
    ("rekt.news", "https://rekt.news/feed/", "rekt", True),
    ("DeFiYield REKT Database", "https://defiyield.app/rekt-database/rss.xml", "rekt", True),
    ("SlowMist Hacked", "https://slowmist.medium.com/feed", "rekt", True),
    ("PeckShield Incidents", "https://peckshield.medium.com/feed", "rekt", True),
    ("Web3 is Going Great", "https://www.web3isgoinggreat.com/feed", "rekt", True),
    ("REKT Substack", "https://rekt.substack.com/feed", "rekt", True),
    ("CertiK Alert", "https://certik.medium.com/feed", "rekt", True),
    ("Immunefi", "https://immunefi.com/blog/feed", "rekt", True),
    ("Chainalysis", "https://chainalysis.com/blog/feed", "rekt", True),
    ("Elliptic", "https://www.elliptic.co/blog/rss.xml", "rekt", True),
    ("TRM Labs", "https://www.trmlabs.com/blog/rss.xml", "rekt", True),
    ("Halborn", "https://halborn.com/feed.xml", "rekt", True),
]

# Aggregate registry: (name, url, category, enabled)
RSS_FEEDS: list[tuple[str, str, str, bool]] = [
    *NEWS_FEEDS,
    *SECURITY_FEEDS,
    *DEFI_FEEDS,
    *GOVERNANCE_FEEDS,
    *NEWSLETTER_FEEDS,
    *REKT_FEEDS,
]

# Reddit feeds expressed in the same (name, url, category, enabled) shape so
# the generic fetcher can consume them uniformly.
REDDIT_RSS_FEEDS: list[tuple[str, str, str, bool]] = [(name, url, "reddit", True) for name, url in REDDIT_FEEDS]


# Convenience: enabled feeds grouped by category.
def enabled_feeds_by_category() -> dict[str, list[tuple[str, str, str, bool]]]:
    """Return {category: [(name, url, category, enabled), ...]} for enabled feeds."""
    grouped: dict[str, list[tuple[str, str, str, bool]]] = {}
    for feed in RSS_FEEDS + REDDIT_RSS_FEEDS:
        if not feed[3]:
            continue
        grouped.setdefault(feed[2], []).append(feed)
    return grouped


def fetch_reddit(db_r=None) -> Any:
    """Fetch Reddit RSS feeds. Returns list of articles."""
    results = []
    for source, url in REDDIT_FEEDS:
        try:
            resp = httpx.get(url, timeout=15, headers={"User-Agent": "RMI/3.0 NewsBot"})
            if resp.status_code == 429:
                logger.warning(f"  {source}: rate limited, skipping")
                continue
            root = SafeET.fromstring(resp.content)
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
                root = SafeET.fromstring(resp.content)
                for item in root.findall(".//item")[:10]:
                    title = (item.findtext("title", "") or "").strip()
                    desc = (item.findtext("description", "") or "").strip()
                    if not title:
                        continue
                    doc_id = "twitter:" + hashlib.sha256((username + title).encode()).hexdigest()[:16]
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
                logger.info(f"  @{username}: {len([r for r in results if r['source'] == username])} tweets")
            except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
                pass  # Try next instance
    return results


def fetch_rss_feeds(
    feeds: list[tuple[str, str, str, bool]] | None = None,
    per_feed: int = 10,
    db_r=None,
) -> list[dict[str, Any]]:
    """Fetch the unified RSS feed registry (200+ sources).

    Each feed is a ``(name, url, category, enabled)`` tuple. Disabled feeds
    are skipped. Returns a flat list of article dicts tagged with the feed
    category so downstream consumers can bucket by news/security/defi/
    governance/newsletter/rekt/reddit.

    Robust to individual feed failures: a 403/429/5xx or network error on one
    source is logged and skipped, never fatal.
    """
    if feeds is None:
        feeds = RSS_FEEDS + REDDIT_RSS_FEEDS

    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for name, url, category, enabled in feeds:
        if not enabled:
            continue
        # A source may be registered under several categories (e.g. a blog in
        # both ``news`` and ``newsletter``). Fetch each unique URL only once.
        if url in seen_urls:
            continue
        seen_urls.add(url)
        try:
            resp = httpx.get(url, timeout=15, headers={"User-Agent": "RMI/3.0 NewsBot"})
            if resp.status_code != 200:
                logger.warning(f"  {name}: HTTP {resp.status_code}, skipping")
                continue
            root = SafeET.fromstring(resp.content)
            items = root.findall(".//{http://www.w3.org/2005/Atom}entry")
            if not items:
                items = root.findall(".//item")
            for item in items[:per_feed]:
                title = (item.findtext("title", "") or "").strip()
                content = (
                    item.findtext("content", "")
                    or item.findtext("description", "")
                    or item.findtext("{http://www.w3.org/2005/Atom}summary", "")
                    or ""
                ).strip()
                link = item.findtext("link", "") or ""
                if not title or len(title) < 10:
                    continue
                doc_id = f"{category}:" + hashlib.sha256((name + title).encode()).hexdigest()[:16]
                results.append(
                    {
                        "id": doc_id,
                        "title": f"[{category}] {title}",
                        "content": content[:3000],
                        "url": link,
                        "source": name,
                        "category": category,
                        "sentiment": 0.0,
                        "tickers": [],
                        "published": "",
                        "ingested_at": time.time(),
                    }
                )
            logger.info(f"  {name}: {len([r for r in results if r['source'] == name])} articles")
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.warning(f"  {name}: {str(e)[:60]}")
    return results


def sync_nitter_sources_from_tracked(top_n: int = 100, sources_file: str | None = None) -> dict[str, Any]:
    """Add nitter RSS entries for the top-N tracked accounts into unified_news_sources.json.

    Ranked by account weight (registry ``tier`` when present, curated
    ``weight`` otherwise). Existing nitter entries are never duplicated and
    no handle with an existing ``https://nitter.net/{handle}/rss`` URL is
    re-added. Idempotent — safe to re-run once influential_accounts.json
    lands (it reads the merged account list from x_intel).

    Returns a summary dict with counts for logging/tests.
    """
    from app.databus import x_intel

    if sources_file is None:
        sources_file = str(Path(__file__).resolve().parent / "unified_news_sources.json")

    data = json.loads(Path(sources_file).read_text(encoding="utf-8"))
    sources = data.setdefault("sources", [])

    existing_urls = {str(s.get("url", "")).lower() for s in sources if isinstance(s, dict)}
    existing_ids = {str(s.get("id", "")).lower() for s in sources if isinstance(s, dict)}

    def _priority(acc: dict[str, Any]) -> int:
        tier = acc.get("tier")
        try:
            if tier is not None and int(tier) in (1, 2):
                return int(tier)
        except (TypeError, ValueError):
            pass
        weight = float(acc.get("weight") or 0)
        return 1 if weight >= 0.85 else 2 if weight >= 0.7 else 3

    def _tier_rank(acc: dict[str, Any]) -> int:
        try:
            return int(acc.get("tier") or 4)
        except (TypeError, ValueError):
            return 4

    # Primary sort: registry tier (1 first), then weight, then handle.
    ranked = sorted(
        x_intel.TRACKED_ACCOUNTS,
        key=lambda a: (_tier_rank(a), -float(a.get("weight") or 0), str(a.get("handle", "")).lower()),
    )

    added = 0
    skipped = 0
    for acc in ranked[:top_n]:
        handle = str(acc.get("handle", "")).strip().lstrip("@")
        if not handle:
            continue
        url = f"https://nitter.net/{handle}/rss"
        if url.lower() in existing_urls:
            skipped += 1
            continue
        prio = _priority(acc)
        slug = handle.lower().replace("/", "_")
        source_id = f"twitter-{slug}"
        if source_id.lower() in existing_ids:
            skipped += 1
            continue
        sources.append(
            {
                "id": source_id,
                "name": f"{acc.get('name') or handle} (X)",
                "url": url,
                "category": "social",
                "type": "nitter",
                "priority": prio,
                "quality_tier": "established" if prio == 1 else "independent",
                "language": "en",
                "requires_api_key": False,
                "verified": True,
                "needs_verification": False,
            }
        )
        existing_urls.add(url.lower())
        existing_ids.add(source_id.lower())
        added += 1

    data["total_sources"] = len(sources)
    data["updated"] = datetime.now(UTC).date().isoformat()
    note = data.setdefault("verification", {}).setdefault("note", "")
    data["verification"]["note"] = (
        note + f"; nitter sync from tracked accounts ({added} added, {skipped} already present) "
        f"{datetime.now(UTC).date().isoformat()}"
    ).strip("; ")

    Path(sources_file).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    logger.info(f"nitter sync: added={added} skipped={skipped} total_sources={len(sources)}")
    return {"added": added, "skipped": skipped, "total_sources": len(sources), "top_n": top_n}


def merge_into_redis(articles, prefix="rmi:news") -> Any | int:
    """Merge social articles into existing Redis news index."""
    try:
        from dotenv import load_dotenv

        load_dotenv("/app/.env", override=True)
        import json
        import os

        import redis

        r = redis.Redis(host="rmi-redis", port=6379, password=os.getenv("REDIS_PASSWORD"), decode_responses=True)
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
    rss = fetch_rss_feeds()
    merged = merge_into_redis(reddit + twitter + rss)
    logger.info(
        json.dumps(
            {
                "reddit": len(reddit),
                "twitter": len(twitter),
                "rss": len(rss),
                "rss_feeds": len(RSS_FEEDS),
                "merged_new": merged,
            }
        )
    )
