"""
RMI News Network - World's Best Crypto News Aggregator

Architecture:
  RSS/API Sources → MCP News Pipeline → Sentiment Analysis → Categorization → Feed

Sources: 25+ trusted crypto news outlets, newsletters, blogs, and mirrors.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import feedparser
import httpx

from app.core.logging import get_logger
from app.feed_quality_pipeline import LRUCache

logger = get_logger("news_network")

# ═══════════════════════════════════════════════════════════════════════════
# 25+ NEWS SOURCES - Mainstream, independent, newsletters, blogs
# ═══════════════════════════════════════════════════════════════════════════

NEWS_SOURCES = [
    {
        "name": "CoinDesk",
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml",
        "tier": 1,
    },
    {"name": "CoinTelegraph", "url": "https://cointelegraph.com/rss", "tier": 1},
    {"name": "The Block", "url": "https://www.theblock.co/rss/", "tier": 1},
    {"name": "Decrypt", "url": "https://decrypt.co/feed", "tier": 1},
    {"name": "Blockworks", "url": "https://blockworks.co/feed", "tier": 1},
    {"name": "DL News", "url": "https://www.dlnews.com/feed", "tier": 1},
    {
        "name": "Bloomberg Crypto",
        "url": "https://feeds.bloomberg.com/markets/crypto.rss",
        "tier": 1,
    },
    {"name": "Forbes Crypto", "url": "https://www.forbes.com/digital-assets/feed/", "tier": 1},
    {
        "name": "CoinDesk Markets",
        "url": "https://www.coindesk.com/arc/outboundfeeds/markets/?outputType=xml",
        "tier": 1,
    },
    {"name": "CoinDesk Policy", "url": "https://www.coindesk.com/policy/feed/", "tier": 1},
    {
        "name": "Cointelegraph Bitcoin",
        "url": "https://cointelegraph.com/rss/tag/bitcoin",
        "tier": 1,
    },
    {
        "name": "Cointelegraph Ethereum",
        "url": "https://cointelegraph.com/rss/tag/ethereum",
        "tier": 1,
    },
    {"name": "Cointelegraph DeFi", "url": "https://cointelegraph.com/rss/tag/defi", "tier": 1},
    {
        "name": "Cointelegraph Regulation",
        "url": "https://cointelegraph.com/rss/tag/regulation",
        "tier": 1,
    },
    {"name": "The Block Policy", "url": "https://www.theblock.co/policy/feed", "tier": 1},
    {"name": "Bankless", "url": "https://www.bankless.com/feed", "tier": 2},
    {"name": "The Defiant", "url": "https://thedefiant.io/feed", "tier": 2},
    {"name": "CryptoSlate", "url": "https://cryptoslate.com/feed/", "tier": 2},
    {"name": "BeInCrypto", "url": "https://beincrypto.com/feed/", "tier": 2},
    {"name": "Crypto Briefing", "url": "https://cryptobriefing.com/feed/", "tier": 2},
    {"name": "AMB Crypto", "url": "https://ambcrypto.com/feed/", "tier": 2},
    {"name": "Bitcoin Magazine", "url": "https://bitcoinmagazine.com/feed", "tier": 2},
    {"name": "NewsBTC", "url": "https://www.newsbtc.com/feed/", "tier": 2},
    {"name": "CryptoPotato", "url": "https://cryptopotato.com/feed/", "tier": 2},
    {"name": "CryptoNews", "url": "https://cryptonews.com/news/feed/", "tier": 2},
    {"name": "U.Today", "url": "https://u.today/rss", "tier": 2},
    {"name": "CoinGape", "url": "https://coingape.com/feed/", "tier": 2},
    {"name": "CoinJournal", "url": "https://coinjournal.net/feed/", "tier": 2},
    {"name": "CoinBureau", "url": "https://www.coinbureau.com/feed/", "tier": 2},
    {"name": "Bitcoinist", "url": "https://bitcoinist.com/feed/", "tier": 2},
    {"name": "DailyCoin", "url": "https://dailycoin.com/feed/", "tier": 2},
    {"name": "Crypto Daily", "url": "https://cryptodaily.co.uk/feed/", "tier": 2},
    {"name": "CoinCodex", "url": "https://coincodex.com/feed/", "tier": 2},
    {"name": "Coinpedia", "url": "https://coinpedia.org/feed/", "tier": 2},
    {"name": "ZyCrypto", "url": "https://zycrypto.com/feed/", "tier": 2},
    {"name": "CoinSpeaker", "url": "https://www.coinspeaker.com/feed/", "tier": 2},
    {"name": "Altcoin Buzz", "url": "https://www.altcoinbuzz.io/feed/", "tier": 2},
    {"name": "CryptoGlobe", "url": "https://www.cryptoglobe.com/feed/", "tier": 2},
    {"name": "Crypto Economy", "url": "https://crypto-economy.com/feed/", "tier": 2},
    {"name": "Crypto News Flash", "url": "https://www.crypto-news-flash.com/feed/", "tier": 2},
    {"name": "TrustNodes", "url": "https://www.trustnodes.com/feed", "tier": 3},
    {"name": "Watcher Guru", "url": "https://watcher.guru/news/feed", "tier": 3},
    {"name": "Bitcoin News", "url": "https://news.bitcoin.com/feed/", "tier": 3},
    {"name": "Ethereum World News", "url": "https://en.ethereumworldnews.com/feed/", "tier": 3},
    {"name": "The Daily Hodl", "url": "https://dailyhodl.com/feed/", "tier": 3},
    {"name": "NullTX", "url": "https://nulltx.com/feed/", "tier": 3},
    {"name": "Crypto Briefing Pro", "url": "https://cryptobriefing.com/feed/", "tier": 3},
    {"name": "Coin Rivet", "url": "https://coinrivet.com/feed/", "tier": 3},
    {"name": "Crypto Mode", "url": "https://cryptomode.com/feed/", "tier": 3},
    {"name": "CoinRepublic", "url": "https://www.coinrepublic.com/feed/", "tier": 3},
    {"name": "Blockonomi", "url": "https://blockonomi.com/feed/", "tier": 3},
    {"name": "UseTheBitcoin", "url": "https://usethebitcoin.com/feed/", "tier": 3},
    {"name": "CryptoAdventure", "url": "https://cryptoadventure.com/feed/", "tier": 3},
    {"name": "The Coin Republic", "url": "https://www.thecoinrepublic.com/feed/", "tier": 3},
    {"name": "Bitcoin Exchange Guide", "url": "https://bitcoinexchangeguide.com/feed/", "tier": 3},
    {"name": "CryptoVibes", "url": "https://cryptovibes.com/feed/", "tier": 3},
    {"name": "CoinCu", "url": "https://coincu.com/feed/", "tier": 3},
    {"name": "Todayq", "url": "https://todayq.com/feed/", "tier": 3},
    {"name": "CoinChapter", "url": "https://coinchapter.com/feed/", "tier": 3},
    {"name": "Cryptopolitan", "url": "https://www.cryptopolitan.com/feed/", "tier": 3},
    {"name": "Rekt News", "url": "https://rekt.news/rss/", "tier": 4},
    {"name": "SlowMist", "url": "https://slowmist.medium.com/feed", "tier": 4},
    {"name": "PeckShield", "url": "https://peckshield.medium.com/feed", "tier": 4},
    {"name": "Chainalysis", "url": "https://blog.chainalysis.com/feed/", "tier": 4},
    {"name": "CertiK", "url": "https://www.certik.com/blog/feed", "tier": 4},
    {"name": "OpenZeppelin", "url": "https://blog.openzeppelin.com/feed/", "tier": 4},
    {"name": "Trail of Bits", "url": "https://blog.trailofbits.com/feed/", "tier": 4},
    {"name": "Immunefi", "url": "https://immunefi.medium.com/feed", "tier": 4},
    {"name": "BlockSec", "url": "https://blocksecteam.medium.com/feed", "tier": 4},
    {"name": "Halborn", "url": "https://www.halborn.com/blog/feed", "tier": 4},
    {"name": "Quantstamp", "url": "https://quantstamp.com/blog/feed", "tier": 4},
    {"name": "Consensys Diligence", "url": "https://consensys.io/diligence/blog/feed", "tier": 4},
    {"name": "Hexens", "url": "https://hexens.io/blog/feed", "tier": 4},
    {"name": "Zellic", "url": "https://www.zellic.io/blog/feed", "tier": 4},
    {"name": "Spearbit", "url": "https://spearbit.medium.com/feed", "tier": 4},
    {"name": "DefiLlama", "url": "https://defillama.com/rss", "tier": 5},
    {"name": "Messari", "url": "https://messari.io/feed", "tier": 5},
    {"name": "Nansen", "url": "https://www.nansen.ai/feed", "tier": 5},
    {"name": "Dune Analytics", "url": "https://dune.com/blog/rss.xml", "tier": 5},
    {"name": "Coin Metrics", "url": "https://coinmetrics.io/feed/", "tier": 5},
    {"name": "Glassnode", "url": "https://insights.glassnode.com/feed/", "tier": 5},
    {"name": "The Tie", "url": "https://www.thetie.is/blog/feed", "tier": 5},
    {"name": "Kaiko", "url": "https://blog.kaiko.com/feed", "tier": 5},
    {"name": "IntoTheBlock", "url": "https://blog.intotheblock.com/feed", "tier": 5},
    {"name": "LunarCrush", "url": "https://lunarcrush.com/blog/feed", "tier": 5},
    {"name": "Santiment", "url": "https://santiment.medium.com/feed", "tier": 5},
    {"name": "Arkham Intel", "url": "https://www.arkhamintelligence.com/blog/feed", "tier": 5},
    {"name": "Token Terminal", "url": "https://tokenterminal.com/blog/feed", "tier": 5},
    {"name": "DeFi Dad", "url": "https://defidad.medium.com/feed", "tier": 5},
    {"name": "Our Network", "url": "https://ournetwork.substack.com/feed", "tier": 5},
    {"name": "a16z Crypto", "url": "https://a16zcrypto.com/feed/", "tier": 6},
    {"name": "Paradigm", "url": "https://www.paradigm.xyz/feed", "tier": 6},
    {"name": "Pantera Capital", "url": "https://panteracapital.com/feed/", "tier": 6},
    {"name": "Multicoin Capital", "url": "https://multicoin.capital/feed/", "tier": 6},
    {"name": "Dragonfly", "url": "https://www.dragonfly.xyz/feed", "tier": 6},
    {"name": "Electric Capital", "url": "https://www.electriccapital.com/rss.xml", "tier": 6},
    {"name": "Variant Fund", "url": "https://variant.fund/feed/", "tier": 6},
    {"name": "1Confirmation", "url": "https://1confirmation.com/feed/", "tier": 6},
    {"name": "Delphi Digital", "url": "https://members.delphidigital.io/feed", "tier": 6},
    {"name": "Framework Ventures", "url": "https://framework.ventures/feed", "tier": 6},
    {"name": "Milk Road", "url": "https://www.milkroad.com/feed", "tier": 7},
    {"name": "The Pomp Letter", "url": "https://pomp.substack.com/feed", "tier": 7},
    {"name": "Bankless Weekly", "url": "https://newsletter.banklesshq.com/feed", "tier": 7},
    {"name": "Week in Ethereum", "url": "https://weekinethereum.substack.com/feed", "tier": 7},
    {"name": "Proof of Work", "url": "https://proofofwork.news/feed", "tier": 7},
    {"name": "EthHub Weekly", "url": "https://ethhub.substack.com/feed", "tier": 7},
    {"name": "CoinGecko Buzz", "url": "https://www.coingecko.com/en/rss/news", "tier": 7},
    {"name": "CoinMarketCap Blog", "url": "https://coinmarketcap.com/alexandria/feed", "tier": 7},
    {"name": "Bitcoin Optech", "url": "https://bitcoinops.org/feed.xml", "tier": 7},
    {"name": "Bitcoin Dev", "url": "https://bitcointechtalk.com/feed/", "tier": 7},
    {"name": "The Daily Gwei", "url": "https://thedailygwei.substack.com/feed", "tier": 7},
    {"name": "DeFi Weekly", "url": "https://defiweekly.substack.com/feed", "tier": 7},
    {"name": "Messari Unqualified", "url": "https://messari.substack.com/feed", "tier": 7},
    {"name": "Not Boring", "url": "https://www.notboring.co/feed", "tier": 7},
    {"name": "Nic Carter", "url": "https://niccarter.substack.com/feed", "tier": 7},
    {"name": "Lyn Alden", "url": "https://www.lynalden.com/feed/", "tier": 7},
    {"name": "Ryan Selkis", "url": "https://ryanselkis.substack.com/feed", "tier": 7},
    {"name": "Hayden Adams", "url": "https://hayden.substack.com/feed", "tier": 7},
    {"name": "Vitalik Buterin", "url": "https://vitalik.eth.limo/feed.xml", "tier": 7},
    {"name": "CoinBureau Newsletter", "url": "https://www.coinbureau.com/feed/", "tier": 7},
    {"name": "Coinspeaker Japan", "url": "https://www.coinspeaker.com/ja/feed/", "tier": 8},
    {"name": "Bitcoin.com News", "url": "https://news.bitcoin.com/feed/", "tier": 8},
    {"name": "Crypto.com News", "url": "https://crypto.com/rss/feed", "tier": 8},
    {"name": "Binance Blog", "url": "https://www.binance.com/en/feed", "tier": 8},
    {"name": "Coinbase Blog", "url": "https://blog.coinbase.com/feed", "tier": 8},
    {"name": "Kraken Blog", "url": "https://blog.kraken.com/feed", "tier": 8},
    {"name": "Gemini Blog", "url": "https://www.gemini.com/blog/feed", "tier": 8},
    {"name": "OKX Insights", "url": "https://www.okx.com/feed/insights", "tier": 8},
    {"name": "Bybit Learn", "url": "https://learn.bybit.com/feed/", "tier": 8},
    {"name": "BitMEX Research", "url": "https://blog.bitmex.com/feed/", "tier": 8},
    {"name": "Bitfinex Blog", "url": "https://blog.bitfinex.com/feed/", "tier": 8},
    {"name": "KuCoin Blog", "url": "https://www.kucoin.com/blog/feed", "tier": 8},
    {"name": "Gate.io Blog", "url": "https://www.gate.io/blog/feed", "tier": 8},
    {"name": "Bitstamp Blog", "url": "https://blog.bitstamp.net/feed/", "tier": 8},
    {"name": "Uniswap Blog", "url": "https://blog.uniswap.org/feed", "tier": 8},
    # --- TIER 9: AI & Tech News (for degenfeed) ---
    {"name": "TechCrunch", "url": "https://techcrunch.com/feed/", "tier": 9},
    {"name": "The Verge", "url": "https://www.theverge.com/rss/index.xml", "tier": 9},
    {"name": "Ars Technica", "url": "https://arstechnica.com/feed/", "tier": 9},
    {"name": "Wired", "url": "https://www.wired.com/feed/rss", "tier": 9},
    {"name": "VentureBeat", "url": "https://venturebeat.com/feed/", "tier": 9},
    {"name": "OpenAI Blog", "url": "https://openai.com/blog/rss.xml", "tier": 9},
    {"name": "Google AI", "url": "https://blog.google/innovation-and-ai/technology/ai/rss/", "tier": 9},
    {"name": "DeepMind", "url": "https://deepmind.google/blog/feed/", "tier": 9},
    {"name": "Hugging Face", "url": "https://huggingface.co/blog/feed.xml", "tier": 9},
    {"name": "AI News", "url": "https://www.artificialintelligence-news.com/feed/", "tier": 9},
    {"name": "CryptoBriefing", "url": "https://cryptobriefing.com/feed/", "tier": 2},
    {"name": "AMB Crypto", "url": "https://ambcrypto.com/feed/", "tier": 2},
    # --- TIER 10: Decentralized / Web3 Social (for degenfeed) ---
    {"name": "Paragraph/Bankless", "url": "https://paragraph.xyz/@bankless/rss", "tier": 10},
    {"name": "Web3 is Going Great", "url": "https://www.web3isgoinggreat.com/feed", "tier": 10},
    {"name": "NFT Now", "url": "https://nftnow.com/feed/", "tier": 10},
]
# Extended category detection with 12 categories
CATEGORY_KEYWORDS = {
    "Bitcoin": ["bitcoin", "btc", "satoshi", "halving", "ordinals", "lightning network", "taproot"],
    "Ethereum": ["ethereum", "eth", "vitalik", "layer 2", "l2", "staking", "merge", "eip", "erc"],
    "Solana": ["solana", "sol", "phantom", "jupiter", "raydium", "pump.fun", "bonk"],
    "DeFi": ["defi", "yield", "liquidity pool", "amm", "swap", "lending", "borrowing", "tvl"],
    "Regulation": [
        "sec",
        "regulation",
        "lawsuit",
        "compliance",
        "cftc",
        "doj",
        "legal",
        "court",
        "ban",
    ],
    "Security": [
        "hack",
        "exploit",
        "rug pull",
        "scam",
        "phishing",
        "vulnerability",
        "audit",
        "stolen",
    ],
    "Markets": ["price", "market", "bull", "bear", "rally", "crash", "dump", "pump", "trading"],
    "NFTs": ["nft", "collectible", "mint", "opensea", "blur", "magic eden", "pudgy"],
    "Memecoins": ["meme", "dogecoin", "shiba", "pepe", "bonk", "wojak", "cum"],
    "Adoption": ["adoption", "partnership", "enterprise", "institutional", "bank", "etf"],
    "Privacy": ["privacy", "zk", "zero knowledge", "mixer", "tornado", "monero", "zec"],
    "AI": [
        "ai",
        "artificial intelligence",
        "machine learning",
        "deep learning",
        "llm",
        "gpt",
        "openai",
        "chatgpt",
        "neural network",
        "transformer",
        "diffusion",
        "generative ai",
        "ai model",
        "hugging face",
        "copilot",
        "claude",
        "gemini",
        "large language model",
        "computer vision",
        "nlp",
        "robotics",
        "autonomous",
        "ai agent",
        "rag",
        "fine-tuning",
        "embedding",
        "vector database",
        "foundation model",
    ],
    "Tech": [
        "startup",
        "silicon valley",
        "venture capital",
        "funding round",
        "series a",
        "ipo",
        "acquisition",
        "unicorn",
        "saas",
        "cloud computing",
        "devops",
        "big tech",
        "apple",
        "google",
        "microsoft",
        "amazon",
        "meta",
        "nvidia",
        "tesla",
        "programming",
        "software",
        "cybersecurity",
    ],
    "Web3 Social": [
        "farcaster",
        "nostr",
        "decentralized social",
        "fediverse",
        "activitypub",
        "lens protocol",
        "mirror.xyz",
        "paragraph.xyz",
        "onchain social",
        "social graph",
        "identity",
        "ens",
        "basename",
    ],
}

SENTIMENT_DICT = {
    "surge": 0.8,
    "soar": 0.9,
    "rally": 0.7,
    "breakout": 0.8,
    "pump": 0.6,
    "bullish": 0.9,
    "gain": 0.5,
    "profit": 0.6,
    "growth": 0.6,
    "adoption": 0.7,
    "partnership": 0.6,
    "launch": 0.5,
    "mainnet": 0.6,
    "upgrade": 0.5,
    "record": 0.7,
    "crash": -0.9,
    "dump": -0.7,
    "hack": -0.95,
    "exploit": -0.95,
    "scam": -0.9,
    "rug pull": -0.95,
    "bearish": -0.9,
    "loss": -0.6,
    "decline": -0.5,
    "lawsuit": -0.7,
    "ban": -0.8,
    "crackdown": -0.7,
    "liquidation": -0.8,
}


@dataclass
class Article:
    id: str
    title: str
    source: str
    source_tier: int
    url: str
    summary: str = ""
    image: str = ""
    published: str = ""
    categories: list[str] = field(default_factory=list)
    sentiment_score: float = 0.0
    sentiment_label: str = "neutral"
    impact_level: str = "low"
    reading_time: int = 1
    votes_up: int = 0
    votes_down: int = 0
    comments: list[dict] = field(default_factory=list)
    bookmarks: int = 0


# Bounded LRU cache prevents OOM — holds at most 5000 articles
_db: dict[str, Article] = {}
_NEWS_CACHE = LRUCache(maxsize=5000)


def _article_is_seen(aid: str) -> bool:
    """Check if article already stored (uses bounded LRU cache)."""
    return _NEWS_CACHE.get(aid) is not None or aid in _db


def _store_article(aid: str, article: Article) -> None:
    """Store article in bounded cache."""
    # Use the LRU cache (bounded), fallback to dict for backward compat
    _NEWS_CACHE.set(aid, article, ttl=86400)  # 24h TTL
    _db[aid] = article
    # Enforce bound on _db too (prevent unbounded growth)
    if len(_db) > 10000:
        # Remove oldest 2000 entries
        excess = sorted(_db.keys(), key=lambda k: _db[k].published, reverse=True)[:8000]
        keep = set(excess)
        for k in list(_db.keys()):
            if k not in keep:
                del _db[k]


async def fetch_all(max_per_source: int = 8) -> list[Article]:
    """Fetch from all 30+ sources in parallel."""
    new_articles = []

    async def fetch_source(source) -> None:
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get(source["url"], follow_redirects=True)
                if r.status_code == 200:
                    feed = feedparser.parse(r.text)
                    for entry in feed.entries[:max_per_source]:
                        aid = hashlib.sha256((entry.link or entry.title).encode()).hexdigest()[:12]
                        if _article_is_seen(aid):
                            continue

                        summary = entry.get("summary", entry.get("description", ""))
                        summary = re.sub(r"<[^>]+>", "", summary)[:400]
                        title = entry.title or "Untitled"

                        article = Article(
                            id=aid,
                            title=title,
                            source=source["name"],
                            source_tier=source["tier"],
                            url=entry.link,
                            summary=summary,
                            image=entry.get("media_content", [{}])[0].get("url", ""),
                            published=entry.get("published", ""),
                        )

                        # Classify
                        text = (title + " " + summary).lower()
                        for cat, keywords in CATEGORY_KEYWORDS.items():
                            if any(kw in text for kw in keywords):
                                article.categories.append(cat)
                        if not article.categories:
                            article.categories = ["General"]

                        # Sentiment
                        score = 0.0
                        for word, weight in SENTIMENT_DICT.items():
                            if word in text:
                                score += weight
                        article.sentiment_score = round(score / max(abs(score), 1), 2) if score != 0 else 0
                        article.sentiment_label = (
                            "bullish"
                            if article.sentiment_score > 0.15
                            else "bearish"
                            if article.sentiment_score < -0.15
                            else "neutral"
                        )

                        # Reading time
                        words = len(text.split())
                        article.reading_time = max(1, round(words / 200))

                        # Impact level
                        article.impact_level = (
                            "high"
                            if abs(article.sentiment_score) > 0.5
                            or any(kw in text for kw in ["hack", "exploit", "crash", "surge", "breakout"])
                            else "medium"
                            if abs(article.sentiment_score) > 0.2
                            else "low"
                        )

                        _store_article(aid, article)
                        new_articles.append(article)
        except (ConnectionError, OSError, TimeoutError):
            pass

    await asyncio.gather(*[fetch_source(s) for s in NEWS_SOURCES])
    return new_articles


def get_feed(
    category: str | None = None,
    sentiment: str | None = None,
    tier: int | None = None,
    sort: str = "latest",
    limit: int = 50,
    offset: int = 0,
    impact: str | None = None,
    source: str | None = None,
) -> dict:
    """Get the news feed with all filters."""
    articles = list(_db.values())

    if category and category != "All":
        articles = [a for a in articles if category in a.categories]
    if sentiment:
        articles = [a for a in articles if a.sentiment_label == sentiment]
    if tier:
        articles = [a for a in articles if a.source_tier == tier]
    if impact:
        articles = [a for a in articles if a.impact_level == impact]
    if source:
        articles = [a for a in articles if a.source == source]

    if sort == "popular":
        articles.sort(key=lambda a: a.votes_up - a.votes_down, reverse=True)
    elif sort == "bullish":
        articles.sort(key=lambda a: a.sentiment_score, reverse=True)
    elif sort == "bearish":
        articles.sort(key=lambda a: a.sentiment_score)
    elif sort == "impact":
        impact_order = {"high": 3, "medium": 2, "low": 1}
        articles.sort(key=lambda a: impact_order.get(a.impact_level, 0), reverse=True)
    else:
        articles.sort(key=lambda a: a.published, reverse=True)

    total = len(articles)
    page = articles[offset : offset + limit]

    return {
        "articles": [
            {
                "id": a.id,
                "title": a.title,
                "source": a.source,
                "source_tier": a.source_tier,
                "url": a.url,
                "summary": a.summary,
                "image": a.image,
                "published": a.published,
                "categories": a.categories,
                "sentiment_score": a.sentiment_score,
                "sentiment_label": a.sentiment_label,
                "impact_level": a.impact_level,
                "reading_time": a.reading_time,
                "votes_up": a.votes_up,
                "votes_down": a.votes_down,
                "comment_count": len(a.comments),
                "bookmarks": a.bookmarks,
            }
            for a in page
        ],
        "total": total,
        "all_categories": sorted({c for a in articles for c in a.categories}),
        "all_sources": sorted({a.source for a in articles}),
        "stats": {
            "total_articles": len(_db),
            "sources_indexed": len({a.source for a in _db.values()}),
            "sentiment": {
                "bullish": sum(1 for a in _db.values() if a.sentiment_label == "bullish"),
                "bearish": sum(1 for a in _db.values() if a.sentiment_label == "bearish"),
                "neutral": sum(1 for a in _db.values() if a.sentiment_label == "neutral"),
            },
            "high_impact": sum(1 for a in _db.values() if a.impact_level == "high"),
            "latest_update": datetime.now(UTC).isoformat(),
        },
    }


def vote_article(article_id: str, direction: str) -> dict:
    a = _db.get(article_id)
    if not a:
        return {"error": "Not found"}
    if direction == "up":
        a.votes_up += 1
    elif direction == "down":
        a.votes_down += 1
    return {"id": article_id, "votes_up": a.votes_up, "votes_down": a.votes_down}


def add_comment(article_id: str, user: str, text: str) -> dict:
    a = _db.get(article_id)
    if not a:
        return {"error": "Not found"}
    comment = {
        "id": hashlib.sha256(f"{article_id}{time.time()}".encode()).hexdigest()[:8],
        "user": user[:50],
        "text": text[:500],
        "timestamp": datetime.now(UTC).isoformat(),
        "votes": 0,
    }
    a.comments.append(comment)
    return comment


def get_comments(article_id: str) -> list[dict]:
    a = _db.get(article_id)
    return sorted(a.comments, key=lambda c: c.get("votes", 0), reverse=True) if a else []


def bookmark(article_id: str) -> dict:
    a = _db.get(article_id)
    if a:
        a.bookmarks += 1
        return {"id": article_id, "bookmarks": a.bookmarks}
    return {"error": "Not found"}


def get_categories() -> list:
    cats = set()
    for a in _db.values():
        for c in a.categories:
            cats.add(c)
    icons = {
        "Bitcoin": "₿",
        "Ethereum": "Ξ",
        "Solana": "◎",
        "DeFi": "🏦",
        "Regulation": "⚖️",
        "Security": "🛡️",
        "Markets": "📊",
        "NFTs": "🎨",
        "AI": "🤖",
        "Memecoins": "🐸",
        "Adoption": "🚀",
        "Privacy": "🔐",
        "General": "📰",
    }
    return [{"name": c, "icon": icons.get(c, "📌")} for c in sorted(cats)]


def search_articles(query: str, limit: int = 20) -> list:
    q = query.lower()
    results = []
    for a in _db.values():
        if q in a.title.lower() or q in a.summary.lower() or q in a.source.lower():
            results.append(
                {
                    "id": a.id,
                    "title": a.title,
                    "source": a.source,
                    "url": a.url,
                    "summary": a.summary[:200],
                }
            )
    return results[:limit]
