"""News sentiment analysis engine — RugBrain RAG ``news_v2`` sentiment for tokens.

Queries the RugBrain RAG ``news_v2`` collection (via ``HybridRetriever``) for
articles mentioning a token, runs lightweight keyword sentiment on each, and
aggregates into ``BULLISH`` / ``BEARISH`` / ``NEUTRAL`` / ``NO_DATA`` with a
0-100 media-attention (trending) score.

Architecture:
  1. ``HybridRetriever.retrieve(query=f"{name} {symbol} news",
     collection="news_v2", top_k=10)``
  2. ``analyze_sentiment(text)`` — keyword lexicon (``BULLISH_WORDS`` /
     ``BEARISH_WORDS``)
  3. ``compute_trending_score(articles)`` — recency-weighted media attention
     0-100
  4. Aggregate per-article labels → overall sentiment + confidence

Fail-open: any retrieval failure or empty result set degrades to
``NO_DATA`` with ``article_count: 0`` — this engine never raises into the
scan (news sentiment is best-effort enrichment, not a scan blocker).

Usage:
    from app.domains.scanner.engines.news_sentiment import analyze_token_news_sentiment

    result = await analyze_token_news_sentiment("PEPE", "Pepe", chain="ethereum")
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger
from app.rag.embeddings import RAGUnavailableError

logger = get_logger(__name__)

# ── Keyword lexicons (spec C-news) ───────────────────────────────────
BULLISH_WORDS = {
    "surge",
    "rally",
    "breakout",
    "bullish",
    "gain",
    "pump",
    "moon",
    "adoption",
    "partnership",
    "launch",
    "upgrade",
    "mainnet",
    "listing",
}

BEARISH_WORDS = {
    "crash",
    "dump",
    "hack",
    "exploit",
    "vulnerability",
    "SEC",
    "lawsuit",
    "ban",
    "delist",
    "rugpull",
    "scam",
    "fall",
    "drop",
    "decline",
    "bearish",
    "correction",
}

# ── Config ───────────────────────────────────────────────────────────
NEWS_COLLECTION = "news_v2"
MAX_ARTICLES = 10
HIGH_MEDIA_ATTENTION_THRESHOLD = 70

# Recency windows for trending score (hours)
_RECENT_HOURS = 24
_OLDER_HOURS = 72

# Per-article trending points
_TRENDING_RECENT = 15
_TRENDING_OLDER = 5
_TRENDING_OLD = 1
_TRENDING_CAP = 100


def analyze_sentiment(text: str) -> tuple[str, float]:
    """Simple keyword-based sentiment. Returns (label, confidence)."""
    if not text:
        return ("NEUTRAL", 0.3)
    lower = text.lower()
    bullish = sum(1 for w in BULLISH_WORDS if w in lower)
    bearish = sum(1 for w in BEARISH_WORDS if w in lower)
    if bullish > bearish * 2:
        return ("BULLISH", 0.7)
    elif bearish > bullish * 2:
        return ("BEARISH", 0.7)
    elif bullish > 0 or bearish > 0:
        return ("NEUTRAL", 0.5)
    return ("NEUTRAL", 0.3)


def _parse_published_at(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp (with optional ``Z`` suffix) to aware UTC.

    Returns ``None`` for missing or unparseable values — callers treat that
    as "very old / undated" (conservative trending score).
    """
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except (ValueError, TypeError, OSError):
        return None


def compute_trending_score(articles: list[dict[str, Any]]) -> int:
    """How much media attention is this token getting? 0-100.

    Recent articles (<24h): +15 each
    Older articles (24-72h): +5 each
    Very old / undated (>72h or missing): +1 each
    Capped at 100.

    Args:
        articles: List of article dicts with an optional ``published_at``
            key (ISO-8601 string).

    Returns:
        int 0-100.
    """
    score = 0
    now = datetime.now(UTC)
    for article in articles:
        published = _parse_published_at(article.get("published_at") if isinstance(article, dict) else None)
        if published is None:
            score += _TRENDING_OLD
            continue
        age_hours = max(0.0, (now - published).total_seconds() / 3600.0)
        if age_hours < _RECENT_HOURS:
            score += _TRENDING_RECENT
        elif age_hours < _OLDER_HOURS:
            score += _TRENDING_OLDER
        else:
            score += _TRENDING_OLD
    return min(_TRENDING_CAP, score)


def _aggregate_sentiment(labels: list[tuple[str, float]]) -> tuple[str, float]:
    """Aggregate per-article ``(label, confidence)`` into overall sentiment.

    Majority label wins; ties fall to ``NEUTRAL``. Confidence blends the
    agreement ratio and article count, capped at 0.95. Returns ``NO_DATA``
    with 0.0 confidence for an empty list.
    """
    total = len(labels)
    if total == 0:
        return ("NO_DATA", 0.0)
    pos = sum(1 for label, _ in labels if label == "BULLISH")
    neg = sum(1 for label, _ in labels if label == "BEARISH")
    neu = total - pos - neg
    if pos > neg and pos >= neu:
        label = "BULLISH"
    elif neg > pos and neg >= neu:
        label = "BEARISH"
    else:
        label = "NEUTRAL"
    agreement = max(pos, neg, neu) / total
    count_bonus = min(0.15, total * 0.05)
    confidence = round(min(0.95, 0.35 + agreement * 0.5 + count_bonus), 2)
    return (label, confidence)


def _title_from_content(content: str) -> str:
    """Extract the title line from RAG news content (``Title: ...``)."""
    if not content:
        return ""
    for line in content.splitlines():
        line = line.strip()
        if line.lower().startswith("title:"):
            return line[len("title:") :].strip()
    return (content.splitlines()[0] if content.splitlines() else "")[:160]


def _no_data_result(token_symbol: str, token_name: str, chain: str, reason: str) -> dict[str, Any]:
    """Standard NO_DATA envelope (fail-open degradation)."""
    return {
        "check": "news_sentiment",
        "result": "no_data",
        "source": f"rag:{NEWS_COLLECTION}",
        "token_symbol": token_symbol,
        "token_name": token_name,
        "chain": chain,
        "sentiment": "NO_DATA",
        "confidence": 0.0,
        "article_count": 0,
        "recent_articles": [],
        "sentiment_breakdown": {"positive": 0, "negative": 0, "neutral": 0},
        "trending_score": 0,
        "reason": reason,
    }


async def analyze_token_news_sentiment(
    token_symbol: str,
    token_name: str,
    chain: str = "ethereum",
) -> dict[str, Any]:
    """Analyze recent news sentiment for a token.

    Queries RugBrain RAG ``news_v2`` for articles mentioning this token.
    Computes sentiment: ``BULLISH`` / ``BEARISH`` / ``NEUTRAL`` / ``NO_DATA``.

    Args:
        token_symbol: Token symbol (e.g. ``"PEPE"``).
        token_name: Token name (e.g. ``"Pepe"``).
        chain: Chain identifier (informational — news is chain-agnostic).

    Returns:
        Dict with the sentiment payload and an engine envelope::

            {
                "check": "news_sentiment",
                "result": "scanned" | "no_data",
                "source": "rag:news_v2",
                "token_symbol": "...",
                "token_name": "...",
                "chain": "ethereum",
                "sentiment": "BULLISH" | "BEARISH" | "NEUTRAL" | "NO_DATA",
                "confidence": 0.0-0.95,
                "article_count": int,
                "recent_articles": [
                    {"title", "url", "published_at", "sentiment", "relevance"}
                ],
                "sentiment_breakdown": {"positive": n, "negative": n, "neutral": n},
                "trending_score": 0-100,
                "flags": ["HIGH_MEDIA_ATTENTION"],  # only when trending > 70
            }

    Fail-open: retrieval errors or empty results return ``NO_DATA`` with
    ``article_count: 0`` (never raises into the scan).
    """
    if not token_symbol and not token_name:
        return _no_data_result(token_symbol, token_name, chain, reason="no_token_identity")

    query = f"{token_name} {token_symbol} news".strip()
    try:
        from app.rag.retrieval import HybridRetriever

        retriever = HybridRetriever()
        hits = await retriever.retrieve(
            query=query,
            collection=NEWS_COLLECTION,
            top_k=MAX_ARTICLES,
            rerank=False,
        )
    except (
        RAGUnavailableError,
        httpx.HTTPError,
        httpx.TimeoutException,
        ConnectionError,
        OSError,
        TimeoutError,
        RuntimeError,
        ValueError,
        KeyError,
        TypeError,
        ImportError,
    ) as exc:
        logger.debug("news_sentiment_retrieve_failed: %s", exc)
        return _no_data_result(token_symbol, token_name, chain, reason="retrieval_unavailable")

    if not hits:
        return _no_data_result(token_symbol, token_name, chain, reason="no_articles")

    articles: list[dict[str, Any]] = []
    labels: list[tuple[str, float]] = []
    for h in hits:
        meta = h.metadata or {}
        title = str(meta.get("title") or "") or _title_from_content(h.content or "")
        url = str(meta.get("source_url") or "")
        published_at = str(meta.get("published_at") or "")
        text = f"{title} {h.content or ''}".strip()
        label, conf = analyze_sentiment(text)
        labels.append((label, conf))
        articles.append(
            {
                "title": title[:160],
                "url": url,
                "published_at": published_at,
                "sentiment": label,
                "relevance": round(float(h.final_score or h.score or 0.0), 3),
            }
        )

    sentiment, confidence = _aggregate_sentiment(labels)
    breakdown = {
        "positive": sum(1 for label, _ in labels if label == "BULLISH"),
        "negative": sum(1 for label, _ in labels if label == "BEARISH"),
        "neutral": sum(1 for label, _ in labels if label == "NEUTRAL"),
    }
    trending = compute_trending_score(articles)

    result: dict[str, Any] = {
        "check": "news_sentiment",
        "result": "scanned",
        "source": f"rag:{NEWS_COLLECTION}",
        "token_symbol": token_symbol,
        "token_name": token_name,
        "chain": chain,
        "sentiment": sentiment,
        "confidence": confidence,
        "article_count": len(articles),
        "recent_articles": articles[:MAX_ARTICLES],
        "sentiment_breakdown": breakdown,
        "trending_score": trending,
    }
    if trending > HIGH_MEDIA_ATTENTION_THRESHOLD:
        result["flags"] = ["HIGH_MEDIA_ATTENTION"]

    logger.debug(
        "news_sentiment_computed",
        token=f"{token_symbol or token_name}",
        articles=len(articles),
        sentiment=sentiment,
        trending=trending,
    )
    return result
