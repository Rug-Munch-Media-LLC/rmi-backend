#!/usr/bin/env python3
"""
RMI News Intelligence v3 - Industry Best
=========================================
AI-powered news pipeline: categorization, sentiment, trending, briefing.
Uses MiniMax ($20/mo flat) + Ollama Cloud.
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections import Counter
from datetime import UTC, datetime

from app.core.logging import get_logger

logger = get_logger("rmi.news_v3")
OLLAMA_KEY = os.getenv("OLLAMA_API_KEY", os.getenv("DEEPSEEK_API_KEY", ""))
OLLAMA_URL = "https://ollama.com/v1/chat/completions"

# Simple in-memory trending tracker
_trending_topics = Counter()
_breaking_alerts = []


def analyze_article(title: str, content: str = "") -> dict:
    """Full AI analysis of a news article."""
    text = f"{title} {content[:300]}"

    # Category (fast, cached)
    from app.ai_pipeline_v3 import classify_news

    category = classify_news(title, content)

    # Sentiment via MiniMax (batched - 1 call per article is fine at flat rate)
    sentiment = "neutral"
    try:
        k = os.getenv("OLLAMA_API_KEY", "")
        if not k:
            with open("/app/.env") as f:
                for line in f:
                    if line.startswith("OLLAMA_API_KEY"):
                        k = line.strip().split("=", 1)[1]
                        break
        if not k:
            return {"category": category, "sentiment": "neutral", "is_breaking": False}
        body = json.dumps(
            {
                "model": "deepseek-v4-flash",
                "messages": [
                    {
                        "role": "system",
                        "content": "Classify sentiment: BULLISH BEARISH NEUTRAL. Reply one word only.",
                    },
                    {"role": "user", "content": text[:400]},
                ],
                "max_tokens": 10,
                "temperature": 0.1,
            }
        ).encode()
        req = urllib.request.Request(
            OLLAMA_URL,
            data=body,
            headers={"Authorization": f"Bearer {k}", "Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=8)
        sentiment = json.loads(resp.read())["choices"][0]["message"]["content"].strip().upper()
        if "BULL" in sentiment:
            sentiment = "bullish"
        elif "BEAR" in sentiment:
            sentiment = "bearish"
        else:
            sentiment = "neutral"
    except (OSError, TimeoutError, json.JSONDecodeError, KeyError, IndexError) as e:
        logger.warning(f"Sentiment failed: {e}")

    # Track trending topics
    for word in title.lower().split():
        if len(word) > 4 and word not in (
            "after",
            "before",
            "while",
            "could",
            "would",
            "should",
            "their",
            "there",
            "these",
            "those",
            "about",
            "which",
        ):
            _trending_topics[word] += 1

    # Detect breaking news
    is_breaking = category == "SCAM" or any(
        w in text.lower() for w in ["hacked", "exploited", "drained", "rug pulled", "emergency"]
    )

    return {
        "category": category,
        "sentiment": sentiment,
        "is_breaking": is_breaking,
        "analyzed_at": datetime.now(UTC).isoformat(),
    }


def get_trending(limit: int = 10) -> list:
    """Get trending topics from recent article analysis."""
    return [{"topic": word, "count": count} for word, count in _trending_topics.most_common(limit)]


def get_breaking() -> list:
    """Get breaking news alerts."""
    return _breaking_alerts[-10:]


def daily_briefing() -> str:
    """Generate an AI-powered daily news briefing using Ollama Cloud."""
    trending = get_trending(5)
    topics = ", ".join(f"{t['topic']}({t['count']})" for t in trending)

    k = OLLAMA_KEY
    body = json.dumps(
        {
            "model": "deepseek-v4-flash",
            "messages": [
                {
                    "role": "system",
                    "content": "Write a 3-sentence daily crypto news briefing. Mention trending topics. Professional tone. Under 100 words.",
                },
                {"role": "user", "content": f"Trending topics: {topics}"},
            ],
            "max_tokens": 150,
            "temperature": 0.5,
        }
    ).encode()

    try:
        req = urllib.request.Request(
            OLLAMA_URL,
            data=body,
            headers={"Authorization": f"Bearer {k}", "Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read())["choices"][0]["message"]["content"].strip()
    except json.JSONDecodeError:
        return f"Daily briefing: {topics} are trending in crypto news today."


def news_search(query: str, articles: list, limit: int = 10) -> list:
    """Smart search across articles with relevance ranking."""
    results = []
    q_lower = query.lower()
    for a in articles:
        score = 0
        if q_lower in a.get("title", "").lower():
            score += 10
        if q_lower in a.get("content", "").lower():
            score += 5
        if q_lower in a.get("category", "").lower():
            score += 3
        if score > 0:
            a["relevance"] = score
            results.append(a)
    return sorted(results, key=lambda x: x.get("relevance", 0), reverse=True)[:limit]
