"""
RMI Daily Market Rundown - AI-powered crypto news aggregation.

Features:
  - AI Market Summary (DeepSeek V4 Pro, generated daily)
  - Multi-source news aggregation (15+ crypto sources)
  - Algorithmic sentiment analysis per article
  - Market context (price impact, volume, trend)
  - Community voting (up/down)
  - Comments per article
  - Category filtering (DeFi, NFTs, Regulation, Security, etc.)
  - External links open in new tab
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from app.core.logging import get_logger

logger = get_logger("market_rundown")

# ═══════════════════════════════════════════════════════════════════════════
# NEWS AGGREGATION
# ═══════════════════════════════════════════════════════════════════════════

CRYPTO_NEWS_SOURCES = [
    {"name": "CoinDesk", "url": "https:#www.coindesk.com/arc/outboundfeeds/v2/all/", "type": "rss"},
    {"name": "The Block", "url": "https:#www.theblock.co/rss/", "type": "rss"},
    {"name": "Decrypt", "url": "https:#decrypt.co/feed", "type": "rss"},
    {"name": "Bankless", "url": "https:#www.bankless.com/feed", "type": "rss"},
    {"name": "CoinTelegraph", "url": "https:#cointelegraph.com/rss", "type": "rss"},
    {"name": "CryptoSlate", "url": "https:#cryptoslate.com/feed/", "type": "rss"},
    {"name": "BeInCrypto", "url": "https:#beincrypto.com/feed/", "type": "rss"},
    {"name": "Bitcoin Magazine", "url": "https:#bitcoinmagazine.com/feed", "type": "rss"},
    {"name": "The Defiant", "url": "https:#thedefiant.io/feed", "type": "rss"},
    {"name": "Crypto Briefing", "url": "https:#cryptobriefing.com/feed/", "type": "rss"},
    {"name": "AMB Crypto", "url": "https:#ambcrypto.com/feed/", "type": "rss"},
    {"name": "NewsBTC", "url": "https:#www.newsbtc.com/feed/", "type": "rss"},
    {"name": "CoinSpeaker", "url": "https:#www.coinspeaker.com/feed/", "type": "rss"},
    {"name": "Altcoin Buzz", "url": "https:#www.altcoinbuzz.io/feed/", "type": "rss"},
    {"name": "TrustNodes", "url": "https:#www.trustnodes.com/feed", "type": "rss"},
]

# Category patterns for auto-classification
CATEGORY_PATTERNS = {
    "DeFi": [
        "defi",
        "decentralized finance",
        "yield",
        "liquidity pool",
        "amm",
        "swap",
        "lending",
        "borrowing",
    ],
    "NFTs": ["nft", "non-fungible", "collectible", "mint", "opensea", "blur", "magic eden"],
    "Regulation": [
        "sec",
        "regulation",
        "lawsuit",
        "compliance",
        "cftc",
        "doj",
        "ban",
        "legal",
        "court",
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
    "Markets": [
        "price",
        "market",
        "bull",
        "bear",
        "rally",
        "crash",
        "dump",
        "pump",
        "trading",
        "volume",
    ],
    "Technology": [
        "upgrade",
        "fork",
        "layer 2",
        "l2",
        "zk",
        "rollup",
        "blockchain",
        "protocol",
        "mainnet",
    ],
    "Adoption": ["adoption", "partnership", "integration", "launch", "enterprise", "institutional"],
    "Mining": ["mining", "hashrate", "miner", "pow", "difficulty", "asic"],
    "Stablecoins": ["stablecoin", "usdt", "usdc", "dai", "ust", "depeg"],
    "Memecoins": ["meme", "dogecoin", "shiba", "pepe", "bonk", "wojak"],
}


@dataclass
class NewsArticle:
    id: str
    title: str
    source: str
    url: str
    summary: str = ""
    published: str = ""
    category: str = "General"
    sentiment: float = 0.0  # -1 to 1
    sentiment_label: str = "neutral"
    market_impact: str = "low"
    votes_up: int = 0
    votes_down: int = 0
    comments: list[dict] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# SENTIMENT ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════

SENTIMENT_LEXICON = {
    # Bullish
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
    "all-time high": 0.95,
    "ath": 0.9,
    "record": 0.7,
    "milestone": 0.6,
    # Bearish
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
    "regulation": -0.4,
    "ban": -0.8,
    "crackdown": -0.7,
    "liquidation": -0.8,
    "depeg": -0.9,
    "freeze": -0.7,
    "halt": -0.6,
}


def analyze_sentiment(text: str) -> dict:
    """Algorithmic sentiment analysis using lexicon matching."""
    text_lower = text.lower()
    score = 0.0
    matches = 0

    for word, weight in SENTIMENT_LEXICON.items():
        if word in text_lower:
            score += weight
            matches += 1

    if matches == 0:
        return {"score": 0.0, "label": "neutral", "confidence": 0.0}

    avg_score = score / matches
    label = "bullish" if avg_score > 0.2 else "bearish" if avg_score < -0.2 else "neutral"
    confidence = min(abs(avg_score), 1.0)

    return {"score": round(avg_score, 2), "label": label, "confidence": round(confidence, 2)}


def classify_article(title: str, summary: str) -> str:
    """Auto-classify article into categories."""
    text = (title + " " + summary).lower()
    scores = {}
    for category, keywords in CATEGORY_PATTERNS.items():
        scores[category] = sum(1 for kw in keywords if kw in text)

    if not scores or max(scores.values()) == 0:
        return "General"
    return max(scores, key=scores.get)


# ═══════════════════════════════════════════════════════════════════════════
# IN-MEMORY STORAGE (replace with Redis/DB for production)
# ═══════════════════════════════════════════════════════════════════════════

_articles: dict[str, NewsArticle] = {}
_market_summary: dict = {"text": "", "generated": "", "sentiment": {}}
_summary_cache: dict = {"text": "", "generated": 0, "ttl": 86400}


async def fetch_all_news() -> list[NewsArticle]:
    """Fetch news from all sources."""
    articles = []
    import feedparser

    async with httpx.AsyncClient(timeout=10) as client:
        for source in CRYPTO_NEWS_SOURCES:
            try:
                r = await client.get(source["url"])
                if r.status_code == 200:
                    feed = feedparser.parse(r.text)
                    for entry in feed.entries[:5]:  # Top 5 per source
                        article_id = hashlib.sha256(entry.link.encode()).hexdigest()[:12]
                        summary = entry.get("summary", entry.get("description", ""))[:300]
                        title = entry.title

                        # Only create if new
                        if article_id not in _articles:
                            article = NewsArticle(
                                id=article_id,
                                title=title,
                                source=source["name"],
                                url=entry.link,
                                summary=summary,
                                published=entry.get("published", ""),
                            )
                            # Analyze
                            sentiment = analyze_sentiment(title + " " + summary)
                            article.sentiment = sentiment["score"]
                            article.sentiment_label = sentiment["label"]
                            article.category = classify_article(title, summary)
                            article.tags = [article.category, article.sentiment_label]

                            _articles[article_id] = article

                        articles.append(_articles[article_id])
            except Exception as e:
                logger.debug(f"Failed to fetch {source['name']}: {e}")

    return sorted(articles, key=lambda a: a.published, reverse=True)


async def get_articles(
    category: str | None = None,
    sentiment: str | None = None,
    source: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Get articles with optional filters."""
    articles = list(_articles.values())
    if not articles:
        articles = await fetch_all_news()

    # Apply filters
    if category and category != "All":
        articles = [a for a in articles if a.category == category]
    if sentiment:
        articles = [a for a in articles if a.sentiment_label == sentiment]
    if source:
        articles = [a for a in articles if a.source == source]

    total = len(articles)
    articles = sorted(articles, key=lambda a: a.published, reverse=True)

    return {
        "articles": [
            {
                "id": a.id,
                "title": a.title,
                "source": a.source,
                "url": a.url,
                "summary": a.summary,
                "published": a.published,
                "category": a.category,
                "sentiment": a.sentiment,
                "sentiment_label": a.sentiment_label,
                "votes_up": a.votes_up,
                "votes_down": a.votes_down,
                "comment_count": len(a.comments),
                "tags": a.tags,
            }
            for a in articles[offset : offset + limit]
        ],
        "total": total,
        "categories": sorted({a.category for a in articles}),
        "sources": sorted({a.source for a in articles}),
    }


async def vote(article_id: str, direction: str) -> dict:
    """Vote up or down on an article."""
    article = _articles.get(article_id)
    if not article:
        return {"error": "Article not found"}

    if direction == "up":
        article.votes_up += 1
    elif direction == "down":
        article.votes_down += 1
    else:
        return {"error": "Invalid direction"}

    return {"id": article_id, "votes_up": article.votes_up, "votes_down": article.votes_down}


async def comment(article_id: str, user: str, text: str) -> dict:
    """Add a comment to an article."""
    article = _articles.get(article_id)
    if not article:
        return {"error": "Article not found"}

    comment = {
        "id": hashlib.sha256(f"{article_id}{time.time()}".encode()).hexdigest()[:8],
        "user": user[:50],
        "text": text[:500],
        "timestamp": datetime.now(UTC).isoformat(),
    }
    article.comments.append(comment)
    return comment


async def get_comments(article_id: str) -> list[dict]:
    """Get comments for an article."""
    article = _articles.get(article_id)
    return article.comments if article else []


async def generate_market_summary(force: bool = False) -> dict:
    """Generate AI market summary using DeepSeek V4 Pro (cached 24h)."""
    global _summary_cache

    now = time.time()
    if not force and _summary_cache["text"] and (now - _summary_cache["generated"]) < _summary_cache["ttl"]:
        return {
            "summary": _summary_cache["text"],
            "cached": True,
            "generated": _summary_cache["generated"],
        }

    # Build context from recent articles
    articles = await get_articles(limit=100)
    context = "\n".join(
        f"[{a['sentiment_label']}] {a['source']}: {a['title']}" for a in articles.get("articles", [])[:30]
    )

    sentiment_counts = {"bullish": 0, "bearish": 0, "neutral": 0}
    for a in articles.get("articles", []):
        sentiment_counts[a["sentiment_label"]] = sentiment_counts.get(a["sentiment_label"], 0) + 1

    try:
        # Call DeepSeek V4 Pro via Ollama Cloud API
        key = os.getenv("OLLAMA_API_KEY", os.getenv("HELIUS_API_KEY", ""))
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                "https:#ollama.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": "deepseek-v4-pro",
                    "messages": [
                        {
                            "role": "user",
                            "content": f"Write a professional crypto market rundown titled 'RMI Daily Market Rundown' based on today's top stories. Include:\n1. Market overview (bullish/bearish/neutral based on {sentiment_counts})\n2. Top 3 stories with brief analysis\n3. Key metrics to watch\n4. Trading sentiment summary\n\nContext from today's news:\n{context[:4000]}\n\nKeep it under 300 words, professional tone, actionable insights.",
                        }
                    ],
                    "max_tokens": 500,
                    "temperature": 0.7,
                },
            )
            if r.status_code == 200:
                text = r.json()["choices"][0]["message"]["content"]
                _summary_cache = {"text": text, "generated": now, "ttl": 86400}
                return {"summary": text, "cached": False, "sentiment": sentiment_counts}
    except Exception as e:
        logger.warning(f"Market summary generation failed: {e}")

    # Fallback summary
    fallback = f"Market sentiment today: {sentiment_counts['bullish']} bullish, {sentiment_counts['bearish']} bearish, {sentiment_counts['neutral']} neutral signals across {articles.get('total', 0)} articles from {len(articles.get('sources', []))} sources."
    return {"summary": fallback, "cached": False, "sentiment": sentiment_counts, "fallback": True}


# ═══════════════════════════════════════════════════════════════════════════
# CATEGORIES & SOURCES
# ═══════════════════════════════════════════════════════════════════════════


def get_categories() -> list:
    return [{"name": c, "icon": _category_icon(c)} for c in sorted(CATEGORY_PATTERNS.keys())] + [
        {"name": "General", "icon": "📰"}
    ]


def _category_icon(cat: str) -> str:
    return {
        "DeFi": "🏦",
        "NFTs": "🎨",
        "Regulation": "⚖️",
        "Security": "🛡️",
        "Markets": "📊",
        "Technology": "🔧",
        "Adoption": "🚀",
        "Mining": "⛏️",
        "Stablecoins": "💵",
        "Memecoins": "🐸",
    }.get(cat, "📰")


def get_sources() -> list:
    return [s["name"] for s in CRYPTO_NEWS_SOURCES]
