"""Sentiment pipeline - X/Twitter + Reddit crypto mentions → NLP scoring."""

import os
import re
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/sentiment", tags=["sentiment"])

SEARXNG = os.getenv("SEARXNG_URL", "http://localhost:8088")

POSITIVE_WORDS = {
    "bullish",
    "moon",
    "pump",
    "gem",
    "buy",
    "long",
    "green",
    "ATH",
    "breakout",
    "accumulation",
    "undervalued",
    "partnership",
    "listed",
    "launch",
    "mainnet",
}
NEGATIVE_WORDS = {
    "bearish",
    "dump",
    "rug",
    "scam",
    "sell",
    "short",
    "red",
    "crash",
    "hack",
    "exploit",
    "FUD",
    "dead",
    "delist",
    "bankrupt",
    "SEC",
}


def _score_text(text: str) -> dict:
    words = set(re.findall(r"\b\w+\b", text.lower()))
    pos = len(words & POSITIVE_WORDS)
    neg = len(words & NEGATIVE_WORDS)
    total = pos + neg
    if total == 0:
        return {"sentiment": "neutral", "score": 0.5, "positive": 0, "negative": 0, "total_mentions": 0}
    score = pos / total
    sentiment = "bullish" if score > 0.6 else "bearish" if score < 0.4 else "neutral"
    return {"sentiment": sentiment, "score": round(score, 2), "positive": pos, "negative": neg, "total_mentions": total}


async def _search_mentions(symbol: str, source: str = "twitter") -> list[str]:
    """Search for crypto mentions via SearXNG."""
    texts = []
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"{SEARXNG}/search",
                params={"q": f"${symbol} crypto {source}", "format": "json", "categories": "social media"},
            )
            if r.status_code == 200:
                results = r.json().get("results", [])
                texts = [item.get("content", "") or item.get("title", "") for item in results[:20]]
    except Exception:
        pass
    return texts


@router.get("/token/{symbol}")
async def token_sentiment(symbol: str):
    """Get sentiment for a token across social media."""
    texts = await _search_mentions(symbol)
    if not texts:
        return {"symbol": symbol, "sentiment": "unknown", "note": "No mentions found"}

    all_text = " ".join(texts)
    sentiment = _score_text(all_text)
    sentiment["symbol"] = symbol
    sentiment["timestamp"] = datetime.now(UTC).isoformat()
    sentiment["sources_scanned"] = len(texts)

    # Emoji representation
    emoji = "🟢" if sentiment["sentiment"] == "bullish" else "🔴" if sentiment["sentiment"] == "bearish" else "⚪"
    sentiment["emoji"] = emoji

    return sentiment


@router.get("/market")
async def market_sentiment():
    """Overall crypto market sentiment."""
    tickers = ["BTC", "ETH", "SOL"]
    results = {}
    for ticker in tickers:
        texts = await _search_mentions(ticker)
        results[ticker] = _score_text(" ".join(texts)) if texts else {"sentiment": "unknown"}

    scores = [r["score"] for r in results.values() if r.get("score")]
    avg_score = sum(scores) / len(scores) if scores else 0.5
    overall = "bullish" if avg_score > 0.55 else "bearish" if avg_score < 0.45 else "neutral"

    return {
        "overall": overall,
        "average_score": round(avg_score, 2),
        "breakdown": results,
        "emoji": "🟢" if overall == "bullish" else "🔴" if overall == "bearish" else "⚪",
    }


@router.get("/trending-signals/{symbol}")
async def sentiment_signal(symbol: str):
    """Quick sentiment signal for trading: BULLISH / BEARISH / NEUTRAL."""
    result = await token_sentiment(symbol)
    return {
        "symbol": symbol,
        "signal": result["sentiment"].upper(),
        "emoji": result.get("emoji", "⚪"),
        "score": result.get("score", 0.5),
    }
