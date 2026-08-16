"""
RMI AI Pipeline v3 - Full Production
=====================================
Redis caching, FastAPI endpoints, usage tracking, retry logic.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
import urllib.request
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger

logger = get_logger("rmi.ai_v3")
OLLAMA_KEY = os.getenv("OLLAMA_API_KEY", "")
OLLAMA_URL = "https://ollama.com/v1/chat/completions"
MODEL = "deepseek-v4-flash"

# ── Redis Cache (survives restarts) ──
REDIS_AVAILABLE = False
try:
    import redis

    _redis = redis.Redis(
        host=os.getenv("REDIS_HOST", "rmi-redis"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        password=os.getenv("REDIS_PASSWORD", ""),
        db=1,
        socket_connect_timeout=2,
    )
    _redis.ping()
    REDIS_AVAILABLE = True
except (ConnectionError, OSError):
    pass


def _cache_get(key: str) -> str | None:
    if REDIS_AVAILABLE:
        try:
            value = _redis.get(f"rmi:ai:{key}")
            if value is None:
                return None
            return value.decode() if isinstance(value, bytes) else value
        except (ConnectionError, OSError):
            pass
    return None


def _cache_set(key: str, value: str, ttl: int = 300) -> None:
    if REDIS_AVAILABLE:
        with contextlib.suppress(BaseException):
            _redis.setex(f"rmi:ai:{key}", ttl, value)


# ── Usage Tracking ──
_usage = {"total_calls": 0, "total_tokens": 0, "total_cost": 0.0}


def _track(prompt_tokens: int, completion_tokens: int, cost: float) -> None:
    _usage["total_calls"] += 1
    _usage["total_tokens"] += prompt_tokens + completion_tokens
    _usage["total_cost"] += cost


def usage_stats() -> dict[str, Any]:
    return {**_usage, "timestamp": datetime.now(UTC).isoformat()}


# ── Retry with Exponential Backoff ──
def _call_ollama(system: str, prompt: str, max_tokens: int = 250, temp: float = 0.3, cache_ttl: int = 300) -> str:
    cache_key = hashlib.sha256(f"{system[:60]}|{prompt[:120]}".encode()).hexdigest()
    cached = _cache_get(cache_key)
    if cached:
        val = cached.decode() if isinstance(cached, bytes) else cached
        if isinstance(val, str):
            return val

    for attempt in range(3):
        try:
            body = json.dumps(
                {
                    "model": MODEL,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": temp,
                }
            ).encode()
            req = urllib.request.Request(
                OLLAMA_URL,
                data=body,
                headers={
                    "Authorization": f"Bearer {OLLAMA_KEY}",
                    "Content-Type": "application/json",
                },
            )
            resp = urllib.request.urlopen(req, timeout=12)  # nosec B310 - OLLAMA_URL is a fixed https constant; body is JSON, no user-controlled URL
            data = json.loads(resp.read())
            result = data["choices"][0]["message"]["content"].strip()
            usage = data.get("usage", {})
            _track(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), 0.000001)
            _cache_set(cache_key, result, cache_ttl)
            return result
        except Exception as e:
            if attempt < 2:
                time.sleep(2**attempt)
            else:
                logger.warning(f"Ollama failed after 3 retries: {e}")
    return ""


# ── ALL 12 MODULES (Unified) ──


def explain_risks(scan: dict[str, Any]) -> str:
    s = scan.get("safety_score", 50)
    f = scan.get("risk_flags", [])
    g = scan.get("green_flags", [])
    n = scan.get("name", scan.get("symbol", "token"))
    r = _call_ollama(
        "Explain token risk to non-technical user. 3-4 sentences. Start with safety score. Use <b>bold</b>. End with DYOR.",
        f"Token:{n} Score:{s}/100 Risks:{', '.join(f[:5]) or 'none'} Green:{', '.join(g[:3]) or 'none'}",
        150,
        0.2,
        600,
    )
    return r or f"<b>Safety: {s}/100</b>. Risk flags: {', '.join(f[:3])}. Always DYOR."


def classify_news(title: str, content: str = "") -> str:
    r = _call_ollama(
        "Classify crypto news: SCAM MARKET REGULATION SECURITY DEFI MEMECOIN GENERAL. Reply ONE word.",
        f"{title} {content[:200]}",
        8,
        0.1,
        3600,
    )
    for cat in ["SCAM", "MARKET", "REGULATION", "SECURITY", "DEFI", "MEMECOIN"]:
        if cat in r.upper():
            return cat
    t = (title + content).lower()
    if any(w in t for w in ["hack", "exploit", "rug", "scam", "drain"]):
        return "SCAM"
    if any(w in t for w in ["price", "btc", "eth", "bull", "bear"]):
        return "MARKET"
    return "GENERAL"


def profile_wallet(tx: dict[str, Any]) -> str:
    return (
        _call_ollama(
            "Classify wallet persona: PERSONA|conf. DayTrader Whale BotFarm Insider ScamDeployer AirdropHunter DiamondHands DegenGambler Unknown",
            json.dumps(tx)[:1000],
            25,
        )
        or "Unknown|0"
    )


def enrich_rag(query: str, docs: str) -> str:
    return (
        _call_ollama("Reformat RAG chunks into 2-3 sentence answer.", f"Q:{query}\nD:{docs[:2000]}", 200) or docs[:400]
    )


def rank_alerts(alerts: list[Any]) -> list[Any]:
    s = "\n".join(f"{a.get('id', '?')}|{a.get('severity', '?')}|{str(a.get('title', ''))[:80]}" for a in alerts[:10])
    r = _call_ollama("Rank by urgency. Reply: id1,id2,id3...", s, 50)
    return [x.strip() for x in r.split(",") if x.strip()] if r else []


def briefing(data: dict[str, Any]) -> str:
    return (
        _call_ollama(
            "3-para crypto market briefing. P1:volume P2:risks P3:watch. <b>bold</b>. 250 words.",
            json.dumps(data)[:2000],
            350,
            0.5,
            1800,
        )
        or "Briefing unavailable."
    )


def post_mortem(incident: dict[str, Any]) -> str:
    return (
        _call_ollama(
            "Forensic post-mortem: What→How→RedFlags→Protection. <b>bold</b>. 200 words.",
            json.dumps(incident)[:1500],
            300,
            0.4,
            3600,
        )
        or "Autopsy unavailable."
    )


def analyze_submission(sub: dict[str, Any]) -> str:
    return (
        _call_ollama("Analyze suspicious token. Verdict+2-3 concerns.", json.dumps(sub)[:1500], 200)
        or "Analysis unavailable."
    )


def cross_chain(wallets: dict[str, Any]) -> str:
    return (
        _call_ollama(
            "Same entity across chains? MATCH|conf|reason or NO_MATCH|reason",
            json.dumps(wallets)[:1500],
            80,
        )
        or "Unknown"
    )


def blog_draft(topic: str, data: dict[str, Any]) -> str:
    return (
        _call_ollama(
            "Blog post: Title|Hook|Body|Takeaways|CTA. Markdown.",
            f"Topic:{topic}\n{json.dumps(data)[:2000]}",
            500,
            0.6,
            3600,
        )
        or f"# {topic}\n\nDraft unavailable."
    )


def social_post(incident: dict[str, Any]) -> str:
    return (
        _call_ollama("Tweet(<280)+Telegram(<500). Hook first.", json.dumps(incident)[:1000], 200, 0.7)
        or "Post unavailable."
    )


def compare_tokens(a: dict[str, Any], b: dict[str, Any]) -> str:
    return (
        _call_ollama(
            "Compare 2 tokens: SAFER name REASON SCORE_DIFF KEY_DIFFERENCES",
            f"A:{json.dumps(a)[:800]}\nB:{json.dumps(b)[:800]}",
            200,
        )
        or "Comparison unavailable."
    )
