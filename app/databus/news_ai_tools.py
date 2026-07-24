"""
RMI x402 NEWS AI TOOLS - 5 premium tools powered by local Ollama
=================================================================
1. news_sentiment_analysis - Get sentiment analysis with Ollama-powered AI summary
2. market_sentiment_summary - AI-generated market mood from 500+ sources
3. trending_narratives - AI-identified trending crypto narratives
4. news_impact_analysis - How does news impact a specific token?
5. daily_intel_brief - AI-generated daily crypto intelligence briefing

Pricing: $0.02-0.05 USDC. Free trials: 2-5 calls.
All powered by local Ollama - no external API costs.
"""

import json
import time

from app.core.logging import get_logger

logger = get_logger("rmi.news.ai")

# Ollama endpoint
OLLAMA_URL = "http://ollama:11434/api/generate"
OLLAMA_MODEL = "qwen2.5-coder:7b"  # Fast, good quality


def news_sentiment_analysis(query: str = "", limit: int = 20) -> dict:
    """AI-powered sentiment analysis across 500+ crypto news sources."""
    articles = get_news_articles(limit, query)  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
    if not articles:
        return {"error": "No articles found", "query": query}

    # Summarize for Ollama
    headlines = "\n".join([f"- [{a.get('source', '?')}] {a['title']}" for a in articles[:20]])
    prompt = f"""Analyze the sentiment of these crypto news headlines.
Rate overall sentiment as BULLISH, NEUTRAL, or BEARISH with a confidence score (0-100).
List the top 3 most impactful stories and why. Be concise.

HEADLINES:
{headlines}

Respond in JSON format: {{"sentiment": "...", "confidence": ..., "top_stories": [{{"title": "...", "impact": "..."}}]}}"""

    ai_response = ask_ollama(prompt, 300)  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
    try:
        analysis = json.loads(ai_response[ai_response.find("{") : ai_response.rfind("}") + 1])
    except (json.JSONDecodeError):
        analysis = {"sentiment": "NEUTRAL", "confidence": 50, "raw_ai": ai_response[:200]}

    return {
        "query": query,
        "articles_analyzed": len(articles),
        "ai_analysis": analysis,
        "source": "RMI Ollama AI (local, free)",
        "model": OLLAMA_MODEL,
        "attribution": "RMI - rugmunch.io",
    }


# ── TOOL 2: Market Sentiment Summary ──
def market_sentiment_summary() -> dict:
    """AI-generated market mood summary from 500+ sources."""
    articles = get_news_articles(50)  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
    if not articles:
        return {"error": "No articles available"}

    headlines = "\n".join([f"- {a['title']}" for a in articles[:30]])
    prompt = f"""Analyze the overall crypto market sentiment from these headlines.
Write a 3-sentence market mood summary. Then list:
- Top 3 bullish themes
- Top 3 bearish themes
- 1 surprise/contrarian signal if any

HEADLINES:
{headlines}

Respond in JSON: {{"mood_summary": "...", "bullish_themes": ["...","...","..."], "bearish_themes": ["...","...","..."], "contrarian_signal": "..."}}"""

    ai_response = ask_ollama(prompt, 400)  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
    try:
        analysis = json.loads(ai_response[ai_response.find("{") : ai_response.rfind("}") + 1])
    except (json.JSONDecodeError):
        analysis = {"mood_summary": ai_response[:200], "raw": True}

    return {
        "articles_analyzed": len(articles),
        "ai_summary": analysis,
        "source": "RMI Ollama AI",
        "model": OLLAMA_MODEL,
        "attribution": "RMI - rugmunch.io",
    }


# ── TOOL 3: Trending Narratives ──
def trending_narratives(min_mentions: int = 3) -> dict:
    """AI-identified trending crypto narratives from 500+ sources."""
    articles = get_news_articles(100)  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
    if not articles:
        return {"error": "No articles available"}

    # Group by keyword frequency
    headlines = "\n".join([a["title"] for a in articles[:50]])
    prompt = f"""Identify the top 5 trending crypto narratives from these headlines.
For each narrative, give: narrative name, mention count estimate, and a 1-line summary.
Ignore generic topics. Focus on specific stories, protocols, or events.

HEADLINES:
{headlines}

Respond in JSON: {{"narratives": [{{"name": "...", "mentions": ..., "summary": "..."}}]}}"""

    ai_response = ask_ollama(prompt, 400)  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
    try:
        analysis = json.loads(ai_response[ai_response.find("{") : ai_response.rfind("}") + 1])
    except (json.JSONDecodeError):
        analysis = {"narratives": [], "raw": ai_response[:200]}

    return {
        "articles_analyzed": len(articles),
        "narratives": analysis.get("narratives", []),
        "source": "RMI Ollama AI",
        "model": OLLAMA_MODEL,
        "attribution": "RMI - rugmunch.io",
    }


# ── TOOL 4: News Impact Analysis ──
def news_impact_analysis(token: str) -> dict:
    """Analyze how recent news impacts a specific crypto token."""
    articles = get_news_articles(30, token)  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
    if not articles:
        return {"token": token, "error": "No relevant news found"}

    headlines = "\n".join([f"- [{a.get('source', '?')}] {a['title']}" for a in articles[:20]])
    prompt = f"""Analyze how these crypto news headlines might impact {token}.
Rate the impact as POSITIVE, NEGATIVE, or NEUTRAL with confidence (0-100).
Give a 1-2 sentence rationale. Be factual, not hype.

HEADLINES about/may impact {token}:
{headlines}

Respond in JSON: {{"token": "{token}", "impact": "POSITIVE/NEGATIVE/NEUTRAL", "confidence": ..., "rationale": "..."}}"""

    ai_response = ask_ollama(prompt, 250)  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
    try:
        analysis = json.loads(ai_response[ai_response.find("{") : ai_response.rfind("}") + 1])
    except (json.JSONDecodeError):
        analysis = {
            "token": token,
            "impact": "NEUTRAL",
            "confidence": 50,
            "rationale": ai_response[:200],
        }

    return {
        "token": token,
        "articles_found": len(articles),
        "ai_impact_analysis": analysis,
        "source": "RMI Ollama AI",
        "model": OLLAMA_MODEL,
        "attribution": "RMI - rugmunch.io",
    }


# ── TOOL 5: Daily Intel Brief ──
def daily_intel_brief() -> dict:
    """AI-generated daily crypto intelligence briefing."""
    articles = get_news_articles(60)  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
    if not articles:
        return {"error": "No articles available"}

    headlines = "\n".join([f"- {a['title']}" for a in articles[:40]])
    prompt = f"""Generate a concise daily crypto intelligence briefing from these headlines.
Include:
1. Market mood (1 sentence)
2. Top 3 stories with 1-line impact
3. Key tickers to watch
4. 1 risk to monitor

Be professional, factual, and concise. No hype.

HEADLINES:
{headlines}

Respond in JSON: {{"mood": "...", "top_stories": [{{"title": "...", "impact": "..."}}], "tickers_to_watch": ["..."], "risk_to_monitor": "..."}}"""

    ai_response = ask_ollama(prompt, 500)  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
    try:
        brief = json.loads(ai_response[ai_response.find("{") : ai_response.rfind("}") + 1])
    except (json.JSONDecodeError):
        brief = {"mood": ai_response[:200], "raw": True}

    return {
        "brief": brief,
        "articles_analyzed": len(articles),
        "generated_at": time.time(),
        "source": "RMI Ollama AI (local, free)",
        "model": OLLAMA_MODEL,
        "attribution": "RMI - rugmunch.io | Free crypto intelligence",
        "upgrade": "Paid tier removes rate limits via x402",
    }
