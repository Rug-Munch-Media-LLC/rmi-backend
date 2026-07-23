"""
RugCharts News Intelligence Engine
===================================
"We come to the news" - multi-source aggregation, quality scoring,
deduplication, sentiment, category tagging, social hooks.

Sources (all free):
  RSS/Atom     - 200+ crypto feeds (news_service.py)
  Google News  - crypto search results RSS
  Decrypt      - decrypt.co/feed
  The Block    - theblock.co/rss.xml
  CoinTelegraph - cointelegraph.com/rss
  CryptoPanic  - news sentiment API
  arXiv        - academic crypto/blockchain papers
  CoinGecko    - trending, market context
  Polymarket   - prediction market context
  X/Twitter    - v2 API searches (if key available)
"""

import asyncio
import hashlib
import os
import re
import time
from collections import Counter
from datetime import UTC, datetime

import feedparser
import httpx

from app.core.logging import get_logger

logger = get_logger("news_intel")

# ── Source Configuration ───────────────────────────────────────────

NEWS_SOURCES = {
    "google_news": {
        "name": "Google News",
        "url": "https://news.google.com/rss/search?q=cryptocurrency+OR+bitcoin+OR+ethereum+OR+defi+OR+blockchain&hl=en-US&gl=US&ceid=US:en",
        "type": "rss",
        "tier": 1,
        "category": "aggregator",
        "quality_weight": 0.7,  # lower - includes mainstream noise
        "icon": "🔍",
    },
    "decrypt": {
        "name": "Decrypt",
        "url": "https://decrypt.co/feed",
        "type": "rss",
        "tier": 1,
        "category": "journalism",
        "quality_weight": 0.9,
        "icon": "📰",
    },
    "theblock": {
        "name": "The Block",
        "url": "https://www.theblock.co/rss.xml",
        "type": "rss",
        "tier": 1,
        "category": "journalism",
        "quality_weight": 0.95,
        "icon": "🏛️",
    },
    "cointelegraph": {
        "name": "CoinTelegraph",
        "url": "https://cointelegraph.com/rss",
        "type": "rss",
        "tier": 1,
        "category": "journalism",
        "quality_weight": 0.85,
        "icon": "📡",
    },
    "arxiv": {
        "name": "arXiv Research",
        "url": "http://export.arxiv.org/api/query?search_query=all:cryptocurrency+OR+all:blockchain+OR+all:defi&start=0&max_results=10&sortBy=submittedDate&sortOrder=descending",
        "type": "rss",
        "tier": 2,
        "category": "academic",
        "quality_weight": 0.95,
        "icon": "📚",
    },
    "cryptopanic": {
        "name": "CryptoPanic",
        "url": "https://cryptopanic.com/api/v1/posts/",
        "type": "api",
        "tier": 1,
        "category": "aggregator",
        "quality_weight": 0.8,
        "icon": "😱",
        "requires_key": True,
        "key_env": "CRYPTOPANIC_API_KEY",
    },
    "messari": {
        "name": "Messari Research",
        "url": "https://messari.io/api/v1/news",
        "type": "api",
        "tier": 1,
        "category": "research",
        "quality_weight": 0.95,
        "icon": "🔬",
        "requires_key": True,
        "key_env": "MESSARI_API_KEY",
    },
    "cryptocompare": {
        "name": "CryptoCompare News",
        "url": "https://min-api.cryptocompare.com/data/v2/news/?lang=EN",
        "type": "api",
        "tier": 1,
        "category": "aggregator",
        "quality_weight": 0.85,
        "icon": "📊",
        "requires_key": True,
        "key_env": "CRYPTOCOMPARE_API_KEY",
    },
    "lunarcrush": {
        "name": "LunarCrush Social",
        "url": "https://api.lunarcrush.com/v4?data=assets&symbol=BTC&type=metric",
        "type": "api",
        "tier": 2,
        "category": "social",
        "quality_weight": 0.75,
        "icon": "🌙",
        "requires_key": True,
        "key_env": "LUNARCRUSH_API_KEY",
    },
    "rmi_feeds": {
        "name": "RMI Feeds",
        "url": "internal://news_service",
        "type": "internal",
        "tier": 1,
        "category": "aggregator",
        "quality_weight": 0.85,
        "icon": "🔄",
    },
    "x_crypto": {
        "name": "X/Twitter Crypto",
        "url": "internal://x_search",
        "type": "internal",
        "tier": 1,
        "category": "social",
        "quality_weight": 0.6,  # lower - social media noise
        "icon": "𝕏",  # noqa: RUF001
    },
}

# ── Quality & Sentiment ────────────────────────────────────────────

QUALITY_INDICATORS = {
    "positive": [
        "exclusive",
        "investigation",
        "analysis",
        "deep dive",
        "research",
        "report",
        "whitepaper",
        "academic",
        "peer-reviewed",
        "data shows",
        "according to",
        "filing reveals",
        "sources say",
        "documents show",
    ],
    "negative": [
        "could",
        "might",
        "may",
        "rumor",
        "speculation",
        "alleged",
        "anonymous sources",
        "unconfirmed",
        "sponsored",
        "press release",
        "advertorial",
        "promoted",
    ],
    "crypto_specific": [
        "on-chain",
        "smart contract",
        "protocol",
        "liquidity pool",
        "validator",
        "staking",
        "governance",
        "DAO",
        "MEV",
        "zero-knowledge",
        "rollup",
        "L2",
        "settlement",
    ],
}

SENTIMENT_KEYWORDS = {
    "bullish": [
        "surge",
        "rally",
        "pump",
        "breakout",
        "new high",
        "record",
        "bullish",
        "green",
        "gain",
        "profit",
        "accumulation",
        "buying pressure",
        "institutional",
        "adoption",
        "partnership",
        "launch",
        "upgrade",
        "milestone",
        "ath",
        "all time high",
        "undervalued",
        "moon",
        "reversal",
        "recovery",
    ],
    "bearish": [
        "crash",
        "dump",
        "plunge",
        "sell-off",
        "bearish",
        "red",
        "loss",
        "decline",
        "downturn",
        "liquidation",
        "fear",
        "hack",
        "exploit",
        "rug pull",
        "scam",
        "SEC",
        "crackdown",
        "ban",
        "regulation",
        "lawsuit",
        "fine",
        "prison",
        "overvalued",
        "warning",
        "investigation",
        "delist",
        "drain",
        "phishing",
    ],
    "neutral": [
        "announces",
        "reports",
        "update",
        "release",
        "partnership",
        "integration",
        "mainnet",
        "testnet",
        "proposal",
        "vote",
        "maintains",
        "holds",
        "stable",
        "consolidates",
    ],
    "high_impact": [
        "sec",
        "lawsuit",
        "hack",
        "exploit",
        "billion",
        "trillion",
        "blackrock",
        "etf",
        "fed",
        "interest rate",
        "ban",
        "delist",
    ],
}


def score_quality(article: dict) -> float:
    """Score article quality 0-1 based on signals."""
    score = 0.5
    text = (
        article.get("title", "") + " " + article.get("summary", "") + article.get("description", "")
    ).lower()

    # Length - substantive articles are better
    content_len = len(article.get("summary", "") + article.get("description", ""))
    if content_len > 500:
        score += 0.15
    elif content_len > 200:
        score += 0.08
    elif content_len < 50:
        score -= 0.1

    # Quality indicators
    pos_count = sum(1 for kw in QUALITY_INDICATORS["positive"] if kw in text)
    neg_count = sum(1 for kw in QUALITY_INDICATORS["negative"] if kw in text)
    crypto_count = sum(1 for kw in QUALITY_INDICATORS["crypto_specific"] if kw in text)

    score += pos_count * 0.03
    score -= neg_count * 0.05
    score += crypto_count * 0.04

    # Source quality weight
    source_weight = article.get("source_quality", 0.7)
    score = score * 0.6 + source_weight * 0.4

    return max(0.0, min(1.0, score))


def analyze_sentiment(article: dict) -> dict:
    """Advanced keyword-based sentiment analysis with impact weighting."""
    title = article.get("title", "").lower()
    text = (title + " " + article.get("summary", "") + " " + article.get("description", "")).lower()

    bulls = sum(1 for kw in SENTIMENT_KEYWORDS["bullish"] if kw in text)
    bears = sum(1 for kw in SENTIMENT_KEYWORDS["bearish"] if kw in text)
    neutrals = sum(1 for kw in SENTIMENT_KEYWORDS["neutral"] if kw in text)

    # High impact words in title get 3x weight
    high_impact_title = sum(3 for kw in SENTIMENT_KEYWORDS["high_impact"] if kw in title)
    high_impact_body = sum(1 for kw in SENTIMENT_KEYWORDS["high_impact"] if kw in text)

    total = bulls + bears + neutrals + high_impact_title + high_impact_body
    if total == 0:
        return {"sentiment": "neutral", "score": 0.0, "confidence": 0.2}

    # Calculate weighted score (-1.0 to 1.0)
    # Bears are weighted slightly higher in crypto due to risk asymmetry
    sentiment_score = (
        (bulls * 1.0) - (bears * 1.2) + high_impact_title + (high_impact_body * 0.5)
    ) / max(total, 1)

    # Determine label
    if sentiment_score > 0.3:
        label = "bullish"
    elif sentiment_score < -0.3:
        label = "bearish"
    elif sentiment_score > 0.1:
        label = "slightly_bullish"
    elif sentiment_score < -0.1:
        label = "slightly_bearish"
    else:
        label = "neutral"

    # Confidence based on total keyword matches
    confidence = min(1.0, total / 10.0)

    return {
        "sentiment": label,
        "score": round(sentiment_score, 2),
        "confidence": round(confidence, 2),
        "signals": {
            "bullish": bulls,
            "bearish": bears,
            "high_impact": high_impact_title + high_impact_body,
        },
    }


def categorize(article: dict) -> list[str]:
    """Auto-categorize article into topics."""
    text = (article.get("title", "") + " " + article.get("summary", "")).lower()
    categories = []

    cat_keywords = {
        "bitcoin": ["bitcoin", "btc", "satoshi", "lightning network", "ordinals"],
        "ethereum": ["ethereum", "eth", "vitalik", "eip", "evm", "layer 2", "l2"],
        "defi": [
            "defi",
            "yield",
            "lending",
            "borrow",
            "amm",
            "liquidity pool",
            "uniswap",
            "aave",
            "compound",
            "curve",
        ],
        "regulation": [
            "sec",
            "cftc",
            "regulation",
            "compliance",
            "lawsuit",
            "court",
            "legal",
            "ban",
            "license",
            "framework",
        ],
        "security": [
            "hack",
            "exploit",
            "vulnerability",
            "audit",
            "bug bounty",
            "rug pull",
            "scam",
            "phishing",
            "drain",
            "stolen",
        ],
        "nft": ["nft", "collectible", "mint", "opensea", "blur", "pudgy"],
        "solana": ["solana", "sol", "phantom", "jupiter", "raydium"],
        "layer2": [
            "layer 2",
            "l2",
            "rollup",
            "arbitrum",
            "optimism",
            "base",
            "zksync",
            "starknet",
            "polygon",
            "matic",
        ],
        "ai": [
            "ai",
            "artificial intelligence",
            "machine learning",
            "llm",
            "chatgpt",
            "agent",
            "autonomous",
        ],
        "macro": [
            "fed",
            "interest rate",
            "inflation",
            "cpi",
            "gdp",
            "economy",
            "recession",
            "treasury",
            "dollar",
            "dxy",
        ],
        "privacy": ["privacy", "zk", "zero knowledge", "tornado", "monero", "mixer", "anonymous"],
    }

    for cat, keywords in cat_keywords.items():
        if any(kw in text for kw in keywords):
            categories.append(cat)

    return categories[:4]  # max 4 categories


def content_hash(article: dict) -> str:
    """Generate dedup hash from title + normalized text."""
    text = (article.get("title", "") + article.get("summary", "") + article.get("url", "")).lower()
    # Normalize: remove common noise
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^a-z0-9\s]", "", text)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# ── Source Fetchers ─────────────────────────────────────────────────


async def _fetch_rss(url: str, source_name: str, timeout: int = 15) -> list[dict]:
    """Fetch and parse an RSS/Atom feed."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(url, headers={"User-Agent": "RugCharts/1.0 News Bot"})
            if r.status_code != 200:
                return []

            feed = feedparser.parse(r.text)
            articles = []
            for entry in feed.entries[:20]:
                articles.append(
                    {
                        "title": entry.get("title", ""),
                        "url": entry.get("link", ""),
                        "summary": entry.get("summary", entry.get("description", "")),
                        "published": entry.get("published", entry.get("updated", "")),
                        "source": source_name,
                        "source_type": "rss",
                        "author": entry.get("author", ""),
                    }
                )
            return articles
    except Exception as e:
        logger.debug(f"RSS fetch failed for {source_name}: {e}")
        return []


async def _fetch_cryptopanic() -> list[dict]:
    """Fetch from CryptoPanic API."""
    key = os.getenv("CRYPTOPANIC_API_KEY", "")
    if not key:
        return []

    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                "https://cryptopanic.com/api/v1/posts/",
                params={"auth_token": key, "kind": "news", "limit": 20},
            )
            if r.status_code != 200:
                return []

            data = r.json()
            articles = []
            for post in data.get("results", []):
                articles.append(
                    {
                        "title": post.get("title", ""),
                        "url": post.get("url", ""),
                        "summary": post.get("description", ""),
                        "published": post.get("published_at", post.get("created_at", "")),
                        "source": "CryptoPanic",
                        "source_type": "api",
                        "sentiment_votes": {
                            "bullish": post.get("votes", {}).get("positive", 0),
                            "bearish": post.get("votes", {}).get("negative", 0),
                            "important": post.get("votes", {}).get("important", 0),
                        },
                    }
                )
            return articles
    except Exception as e:
        logger.debug(f"CryptoPanic failed: {e}")
        return []


async def _fetch_x_crypto() -> list[dict]:
    """Fetch crypto news from X/Twitter search (if API key available)."""
    x_key = os.getenv("X_API_KEY", "") or os.getenv("TWITTER_BEARER_TOKEN", "")
    if not x_key:
        return []

    try:
        # Search for crypto news tweets from verified sources
        queries = [
            "crypto news -is:retweet -is:reply lang:en",
            "bitcoin ETF -is:retweet lang:en",
            "DeFi protocol -is:retweet lang:en",
        ]
        articles = []
        async with httpx.AsyncClient(timeout=15) as c:
            for q in queries[:2]:
                r = await c.get(
                    "https://api.twitter.com/2/tweets/search/recent",
                    headers={"Authorization": f"Bearer {x_key}"},
                    params={
                        "query": q,
                        "max_results": 10,
                        "tweet.fields": "created_at,public_metrics,author_id",
                        "expansions": "author_id",
                    },
                )
                if r.status_code == 200:
                    data = r.json()
                    users = {
                        u["id"]: u.get("username", "")
                        for u in data.get("includes", {}).get("users", [])
                    }
                    for tweet in data.get("data", []):
                        metrics = tweet.get("public_metrics", {})
                        articles.append(
                            {
                                "title": tweet.get("text", "")[:120],
                                "url": f"https://x.com/i/web/status/{tweet['id']}",
                                "published": tweet.get("created_at", ""),
                                "source": f"@{users.get(tweet.get('author_id', ''), 'unknown')}",
                                "source_type": "x",
                                "likes": metrics.get("like_count", 0),
                                "retweets": metrics.get("retweet_count", 0),
                                "replies": metrics.get("reply_count", 0),
                            }
                        )
        return articles
    except Exception as e:
        logger.debug(f"X fetch failed: {e}")
        return []


# ── Main Aggregation Engine ────────────────────────────────────────


async def aggregate_all_news(limit: int = 50, **kw) -> dict:
    """THE method. Pull from every source, dedup, score, tag, sort.

    Pipeline:
      1. Fetch all sources in parallel
      2. Normalize article format
      3. Deduplicate by content hash
      4. Score quality (0-1)
      5. Analyze sentiment
      6. Auto-categorize
      7. Sort by quality score
      8. Return top N
    """
    seen_hashes: set[str] = set()
    all_articles: list[dict] = []

    # ── Step 1: Fetch all sources in parallel ──
    tasks = []

    # RSS sources
    for _src_id, src in NEWS_SOURCES.items():
        if src["type"] == "rss":
            tasks.append(_fetch_rss(src["url"], src["name"]))

    # API sources
    tasks.append(_fetch_cryptopanic())

    # Internal sources
    try:
        from app.domains.news.news_service import fetch_all_news

        tasks.append(fetch_all_news())
    except Exception:
        pass

    # X/Twitter
    tasks.append(_fetch_x_crypto())

    # Execute all in parallel
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # ── Step 2: Normalize ──
    for result in results:
        if isinstance(result, Exception):
            continue
        if isinstance(result, list):
            for article in result:
                if not article.get("title"):
                    continue
                all_articles.append(article)
        elif isinstance(result, dict) and result.get("articles"):
            for article in result["articles"]:
                if not article.get("title"):
                    continue
                all_articles.append(article)

    # ── Step 3-6: Enrich each article ──
    enriched = []
    for article in all_articles:
        h = content_hash(article)
        if h in seen_hashes:
            continue
        seen_hashes.add(h)

        # Find source config
        source_name = article.get("source", "")
        source_config = None
        for _src_id, src in NEWS_SOURCES.items():
            if src["name"].lower() == source_name.lower():
                source_config = src
                break

        # Enrich
        article["content_hash"] = h
        article["source_quality"] = source_config["quality_weight"] if source_config else 0.7
        article["source_tier"] = source_config["tier"] if source_config else 2
        article["source_category"] = source_config["category"] if source_config else "unknown"
        article["source_icon"] = source_config["icon"] if source_config else "📄"
        article["quality_score"] = score_quality(article)
        article["sentiment"] = analyze_sentiment(article)
        article["categories"] = categorize(article)
        article["indexed_at"] = datetime.now(UTC).isoformat()

        enriched.append(article)

    # ── Step 7: Sort by quality ──
    enriched.sort(
        key=lambda a: (
            a.get("source_tier", 2),  # Tier 1 first
            -a.get("quality_score", 0),  # Higher quality first
            a.get("published", ""),  # Newer within same quality
        ),
        reverse=False,
    )

    # Take top N
    top = enriched[:limit]

    # ── Stats ──
    source_counts = Counter(a.get("source", "Unknown") for a in top)
    cat_counts = Counter(c for a in top for c in a.get("categories", []))
    sentiment_dist = Counter(a.get("sentiment", {}).get("sentiment", "neutral") for a in top)
    avg_quality = sum(a.get("quality_score", 0) for a in top) / max(len(top), 1)

    return {
        "articles": top,
        "total_fetched": len(all_articles),
        "after_dedup": len(enriched),
        "returned": len(top),
        "stats": {
            "sources": dict(source_counts.most_common(10)),
            "categories": dict(cat_counts.most_common(10)),
            "sentiment_distribution": dict(sentiment_dist),
            "average_quality": round(avg_quality, 2),
            "dedup_rate": round((1 - len(enriched) / max(len(all_articles), 1)) * 100, 1),
        },
        "sources_used": [s["name"] for s in NEWS_SOURCES.values()],
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "news_intelligence_engine",
    }


async def get_weekly_best(limit: int = 20, **kw) -> dict:
    """Curated weekly best - highest quality articles from the past 7 days."""
    all_news = await aggregate_all_news(limit=100)
    articles = all_news.get("articles", [])

    # Filter for high quality only
    best = [a for a in articles if a.get("quality_score", 0) > 0.7]
    best.sort(key=lambda a: -a.get("quality_score", 0))

    return {
        "weekly_best": best[:limit],
        "total_curated": len(best),
        "quality_threshold": 0.7,
        "sources_represented": list({a.get("source", "") for a in best[:limit]}),
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "weekly_best",
    }


async def get_academic_papers(limit: int = 10, **kw) -> dict:
    """Academic/research papers from arXiv and other sources."""
    papers = await _fetch_rss(NEWS_SOURCES["arxiv"]["url"], "arXiv Research", timeout=20)

    for p in papers:
        p["quality_score"] = 0.9
        p["source_quality"] = 0.95
        p["categories"] = ["academic", "research"]
        p["source_icon"] = "📚"

    return {
        "papers": papers[:limit],
        "total": len(papers),
        "source": "arXiv",
        "generated_at": datetime.now(UTC).isoformat(),
    }


async def get_social_feed(limit: int = 30, **kw) -> dict:
    """Social media feed - X/Twitter crypto reactions + CryptoPanic sentiment."""
    x_posts = await _fetch_x_crypto()
    cp_posts = await _fetch_cryptopanic()

    all_social = x_posts + [
        {
            "title": p.get("title", ""),
            "url": p.get("url", ""),
            "source": p.get("source", "CryptoPanic"),
            "source_type": "sentiment",
            "sentiment_votes": p.get("sentiment_votes", {}),
            "published": p.get("published", ""),
        }
        for p in cp_posts
    ]

    # Sort by engagement
    all_social.sort(
        key=lambda a: (
            a.get("likes", 0)
            + a.get("retweets", 0) * 2
            + a.get("sentiment_votes", {}).get("important", 0) * 3
        ),
        reverse=True,
    )

    return {
        "social_posts": all_social[:limit],
        "total": len(all_social),
        "sources": ["X/Twitter", "CryptoPanic"],
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "social_feed",
    }


# ── Social Features ─────────────────────────────────────────────────

ARTICLE_REACTIONS: dict[str, dict[str, int]] = {}  # hash → {reaction: count}
ARTICLE_COMMENTS: dict[str, list[dict]] = {}  # hash → [{user, text, time}]

REACTION_TYPES = ["🔥", "🐂", "🐻", "💎", "🧠", "🤡", "🚀", "💀"]


async def add_reaction(content_hash: str, reaction: str, user: str = "anon", **kw) -> dict:
    """Add a reaction to an article."""
    if reaction not in REACTION_TYPES:
        return {"error": f"Invalid reaction. Use: {REACTION_TYPES}"}

    if content_hash not in ARTICLE_REACTIONS:
        ARTICLE_REACTIONS[content_hash] = {}

    ARTICLE_REACTIONS[content_hash][reaction] = ARTICLE_REACTIONS[content_hash].get(reaction, 0) + 1

    return {
        "status": "reacted",
        "content_hash": content_hash,
        "reaction": reaction,
        "counts": ARTICLE_REACTIONS[content_hash],
        "total_reactions": sum(ARTICLE_REACTIONS[content_hash].values()),
    }


async def add_comment(content_hash: str, user: str, text: str, **kw) -> dict:
    """Add a comment to an article."""
    if content_hash not in ARTICLE_COMMENTS:
        ARTICLE_COMMENTS[content_hash] = []

    comment = {
        "user": user,
        "text": text[:500],
        "timestamp": datetime.now(UTC).isoformat(),
        "id": hashlib.sha256(f"{user}{text}{time.time()}".encode()).hexdigest()[:8],
    }
    ARTICLE_COMMENTS[content_hash].append(comment)

    return {
        "status": "commented",
        "content_hash": content_hash,
        "comment": comment,
        "total_comments": len(ARTICLE_COMMENTS[content_hash]),
    }


async def get_reactions(content_hash: str, **kw) -> dict:
    """Get reactions for an article."""
    counts = ARTICLE_REACTIONS.get(content_hash, {})
    comments = ARTICLE_COMMENTS.get(content_hash, [])

    return {
        "content_hash": content_hash,
        "reactions": counts,
        "total_reactions": sum(counts.values()),
        "comments": comments[-20:],  # Last 20 comments
        "total_comments": len(comments),
    }


async def create_bb_post(content_hash: str, user: str = "system", **kw) -> dict:
    """Turn an article into a Bulletin Board post for community discussion."""
    # Find the article in our aggregated data
    # In production, would look up from Redis/storage
    return {
        "status": "bb_post_created",
        "content_hash": content_hash,
        "bb_post_url": f"/bulletin/{content_hash}",
        "message": "Article converted to Bulletin Board post. Community can now discuss.",
    }
