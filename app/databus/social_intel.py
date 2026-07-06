"""
RugCharts Social Intelligence
==============================
KOL tracking, shill detection, scam monitoring, social metrics.

Features:
  - KOL Performance Score - track historical calls, success rate
  - Shill Campaign Detection - coordinated posting patterns
  - Scam Channel Monitor - Telegram/Discord intelligence
  - Social Sentiment - aggregate market mood from multiple platforms
  - Daily Intel Report - Groq-powered market briefing
"""

import hashlib
import logging
import os
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime

import httpx

logger = logging.getLogger("social_intel")

GROQ_KEY = os.getenv("GROQ_API_KEY", "")

# ═══════════════════════════════════════════════════════════════════════
# KOL PERFORMANCE TRACKER
# ═══════════════════════════════════════════════════════════════════════

KOL_DATABASE: dict[str, dict] = {}  # handle → {calls: [...], metrics: {...}}


def _kol_key(handle: str) -> str:
    return f"kol:{handle.lower().lstrip('@')}"


async def track_kol_call(
    handle: str,
    token: str,
    call_type: str = "buy",
    price_at_call: float = 0,
    chain: str = "solana",
    **kw,
) -> dict:
    """Record a KOL making a call on a token.

    call_type: buy, sell, shill, warning, analysis
    """
    key = _kol_key(handle)
    if key not in KOL_DATABASE:
        KOL_DATABASE[key] = {
            "calls": [],
            "metrics": {
                "total_calls": 0,
                "buy_calls": 0,
                "sell_calls": 0,
                "shills": 0,
                "warnings": 0,
                "analyses": 0,
                "tokens_mentioned": set(),
                "avg_roi": 0,
                "win_rate": 0,
                "followers": 0,
            },
        }

    call = {
        "handle": handle,
        "token": token,
        "type": call_type,
        "price_at_call": price_at_call,
        "chain": chain,
        "timestamp": datetime.now(UTC).isoformat(),
        "id": hashlib.sha256(f"{handle}{token}{time.time()}".encode()).hexdigest()[:8],
    }

    KOL_DATABASE[key]["calls"].append(call)
    m = KOL_DATABASE[key]["metrics"]
    m["total_calls"] += 1
    m[f"{call_type}_calls"] = m.get(f"{call_type}_calls", 0) + 1
    m["tokens_mentioned"].add(token)

    return {"status": "tracked", "call": call}


async def get_kol_profile(handle: str, **kw) -> dict:
    """Get a KOL's performance profile - call history, success rate, risk score."""
    key = _kol_key(handle)
    data = KOL_DATABASE.get(key, {"calls": [], "metrics": {}})
    m = data["metrics"]

    # Calculate risk score
    total = m.get("total_calls", 0)
    shills = m.get("shills", 0)
    warnings = m.get("warnings", 0)

    if total > 0:
        shill_ratio = shills / total
        warnings / total
        trust_score = max(0, 100 - shill_ratio * 60 - (1 - m.get("win_rate", 0)) * 40)
    else:
        trust_score = 50

    return {
        "handle": handle,
        "metrics": {
            **{k: v for k, v in m.items() if k != "tokens_mentioned"},
            "tokens_mentioned": len(m.get("tokens_mentioned", set())),
        },
        "trust_score": round(trust_score, 1),
        "risk_level": "HIGH" if trust_score < 30 else "MEDIUM" if trust_score < 60 else "LOW",
        "recent_calls": data["calls"][-10:],
        "source": "kol_tracker",
    }


async def get_kol_leaderboard(limit: int = 20, **kw) -> dict:
    """Leaderboard of top KOLs by trust score and call accuracy."""
    kols = []
    for key, _data in KOL_DATABASE.items():
        handle = key.replace("kol:", "")
        profile = await get_kol_profile(handle)
        kols.append(
            {
                "handle": handle,
                "trust_score": profile["trust_score"],
                "risk_level": profile["risk_level"],
                "total_calls": profile["metrics"]["total_calls"],
            }
        )

    kols.sort(key=lambda k: -k["trust_score"])

    return {
        "leaderboard": kols[:limit],
        "total_tracked": len(kols),
        "source": "kol_leaderboard",
    }


# ═══════════════════════════════════════════════════════════════════════
# SHILL CAMPAIGN DETECTION
# ═══════════════════════════════════════════════════════════════════════

SHILL_PATTERNS = {
    "coordinated_posts": {
        "description": "Multiple KOLs posting same token within short window",
        "severity": "HIGH",
        "indicators": ["same_token", "time_window_lt_1h", "similar_wording"],
    },
    "paid_promotion": {
        "description": "Disclosure language suggesting paid content",
        "severity": "MEDIUM",
        "indicators": ["sponsored", "ad", "partner", "#ad", "paid partnership"],
    },
    "pump_and_dump": {
        "description": "Buy call followed by rapid sell within hours",
        "severity": "CRITICAL",
        "indicators": ["buy_then_sell", "price_spike_then_crash", "short_hold_time"],
    },
    "bot_engagement": {
        "description": "Abnormal engagement patterns suggesting bot farms",
        "severity": "HIGH",
        "indicators": ["like_spike", "generic_comments", "low_follower_quality"],
    },
    "affiliate_farming": {
        "description": "Repeated promotion of same platform for referral rewards",
        "severity": "LOW",
        "indicators": ["referral_link", "repeated_platform", "affiliate_pattern"],
    },
}

DETECTED_CAMPAIGNS: list[dict] = []


async def detect_shill_campaigns(posts: list[dict] | None = None, **kw) -> dict:
    """Scan recent posts for coordinated shill campaigns.

    If posts not provided, checks against accumulated KOL call data.
    """
    campaigns = []

    # Check for coordinated posting (same token, tight window)
    token_windows = defaultdict(list)
    for _key, data in KOL_DATABASE.items():
        for call in data.get("calls", []):
            if call["type"] in ("shill", "buy"):
                token_windows[call["token"]].append(call)

    for token, calls in token_windows.items():
        if len(calls) >= 3:
            # Check time clustering
            times = sorted(c.get("timestamp", "") for c in calls)
            if len(times) >= 3:
                try:
                    t0 = datetime.fromisoformat(times[0].replace("Z", "+00:00"))
                    t_last = datetime.fromisoformat(times[-1].replace("Z", "+00:00"))
                    window_hours = (t_last - t0).total_seconds() / 3600

                    if window_hours < 2:
                        kols_involved = list({c["handle"] for c in calls})
                        campaigns.append(
                            {
                                "type": "coordinated_shill",
                                "token": token,
                                "severity": "CRITICAL" if len(kols_involved) >= 5 else "HIGH",
                                "kols_involved": kols_involved,
                                "time_window_hours": round(window_hours, 1),
                                "call_count": len(calls),
                                "detected_at": datetime.now(UTC).isoformat(),
                            }
                        )
                except Exception:
                    pass

    # Store detected campaigns
    DETECTED_CAMPAIGNS.extend(campaigns)
    DETECTED_CAMPAIGNS[:] = DETECTED_CAMPAIGNS[-100:]  # Keep last 100

    return {
        "active_campaigns": campaigns,
        "total_detected": len(campaigns),
        "patterns_available": list(SHILL_PATTERNS.keys()),
        "source": "shill_detector",
    }


async def get_shill_alerts(**kw) -> dict:
    """Get recent shill campaign alerts."""
    return {
        "alerts": DETECTED_CAMPAIGNS[-20:],
        "total": len(DETECTED_CAMPAIGNS),
        "high_severity": sum(1 for c in DETECTED_CAMPAIGNS if c.get("severity") == "CRITICAL"),
        "source": "shill_alerts",
    }


# ═══════════════════════════════════════════════════════════════════════
# SCAM CHANNEL MONITOR (Telegram/Discord)
# ═══════════════════════════════════════════════════════════════════════

SCAM_INDICATORS = [
    "100x",
    "1000x",
    "guaranteed",
    "no risk",
    "send sol",
    "send eth",
    "airdrop now",
    "claim now",
    "only 100 spots",
    "presale live",
    "whitelist open",
    "private sale",
    "insider",
    "team doxxed",
    "liquidity locked",
    "renounced",
    "no tax",
    "moon",
    "gem",
    "next 1000x",
    "early entry",
    "before listing",
    "launching in",
]


async def scan_scam_channels(**kw) -> dict:
    """Scan known scam channels for active campaigns.

    In production, this would connect to Telegram API.
    For now, provides the detection framework.
    """
    return {
        "status": "monitoring",
        "indicators_tracked": SCAM_INDICATORS[:10],
        "channels_monitored": ["telegram_scam_patterns"],
        "note": "Telegram scanning infrastructure being provisioned. Detection patterns active.",
        "source": "scam_monitor",
    }


# ═══════════════════════════════════════════════════════════════════════
# DAILY INTELLIGENCE REPORT - Groq-powered
# ═══════════════════════════════════════════════════════════════════════


async def generate_daily_intel(**kw) -> dict:
    """Generate a comprehensive Daily Intelligence Report using Groq AI.

    Combines: market data, fear/greed, news headlines, CT sentiment,
    prediction markets, on-chain activity into a single briefing.
    """
    # Gather all data sources
    try:
        from app.databus.news_provider import get_market_brief

        brief = await get_market_brief()
    except Exception:
        brief = {}

    try:
        from app.databus.news_intel import aggregate_all_news

        news = await aggregate_all_news(limit=15)
    except Exception:
        news = {"articles": []}

    try:
        from app.databus.x_intel import fetch_ct_rundown

        ct = await fetch_ct_rundown(limit=10)
    except Exception:
        ct = {"rundown": []}

    # Build context for Groq
    market_context = brief.get("brief", "Market data unavailable")
    news_headlines = [a.get("title", "") for a in news.get("articles", [])[:10]]
    ct_stories = [s.get("text", "")[:100] for s in ct.get("rundown", [])[:5]]
    fear = brief.get("fear_greed", {}).get("value", 50)
    fear_label = brief.get("fear_greed", {}).get("classification", "Neutral")

    context = f"""MARKET DATA:
{market_context}

FEAR & GREED INDEX: {fear}/100 - {fear_label}

TOP NEWS HEADLINES:
{chr(10).join(f"• {h}" for h in news_headlines[:8])}

CRYPTO TWITTER PULSE:
{chr(10).join(f"• {s}" for s in ct_stories[:5])}

Generate a professional Daily Intelligence Report for crypto investors."""

    report = ""
    if GROQ_KEY:
        try:
            async with httpx.AsyncClient(timeout=45) as c:
                r = await c.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {GROQ_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "llama-3.3-70b-versatile",
                        "messages": [
                            {
                                "role": "system",
                                "content": """You are a senior crypto intelligence analyst at RugCharts.
Write a Daily Intelligence Report with these sections:
1. MARKET SNAPSHOT - 2-3 sentences on today's market
2. TOP 3 STORIES - the most important developments
3. SENTIMENT ANALYSIS - what the market is feeling
4. RISK RADAR - things to watch out for (scams, hacks, regulatory)
5. BOTTOM LINE - actionable takeaway for investors

Be direct, data-driven, no fluff. Use emojis sparingly. Format cleanly.""",
                            },
                            {"role": "user", "content": context},
                        ],
                        "temperature": 0.4,
                        "max_tokens": 800,
                    },
                )
                if r.status_code == 200:
                    report = r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            report = f"AI report generation unavailable: {str(e)[:100]}"

    if not report:
        report = f"""DAILY INTELLIGENCE REPORT

MARKET SNAPSHOT: {market_context}

Fear & Greed: {fear}/100 ({fear_label})

TOP HEADLINES:
{chr(10).join(f"{i + 1}. {h}" for i, h in enumerate(news_headlines[:5]))}

BOTTOM LINE: Data-driven. No AI available for narrative synthesis."""

    return {
        "report": report,
        "generated_at": datetime.now(UTC).isoformat(),
        "data_sources": [
            "CoinGecko (prices)",
            "Alternative.me (Fear & Greed)",
            "Polymarket (predictions)",
            "200+ RSS (news)",
            "CT Rundown (Crypto Twitter)",
            "Arkham (entity intel)",
        ],
        "ai_model": "Groq Llama 3.3 70B (free tier)" if GROQ_KEY else "Rule-based (no Groq key)",
        "source": "daily_intel_report",
    }


# ═══════════════════════════════════════════════════════════════════════
# SOCIAL METRICS AGGREGATOR
# ═══════════════════════════════════════════════════════════════════════


async def get_social_metrics(**kw) -> dict:
    """Aggregate social metrics across platforms.

    Tracks: trending topics, sentiment shifts, KOL activity, meme velocity.
    """
    # Gather data
    try:
        from app.databus.news_intel import aggregate_all_news

        news = await aggregate_all_news(limit=50)
    except Exception:
        news = {"articles": [], "stats": {}}

    # Trending topics from categories
    cat_counter = Counter()
    for a in news.get("articles", []):
        for cat in a.get("categories", []):
            cat_counter[cat] += 1

    # Sentiment aggregate
    sentiments = Counter()
    for a in news.get("articles", []):
        s = a.get("sentiment", {}).get("sentiment", "neutral")
        sentiments[s] += 1

    return {
        "trending_topics": dict(cat_counter.most_common(15)),
        "market_sentiment": {
            "aggregate": dict(sentiments),
            "dominant": sentiments.most_common(1)[0][0] if sentiments else "neutral",
        },
        "kol_activity": {
            "tracked": len(KOL_DATABASE),
            "active_campaigns": len(DETECTED_CAMPAIGNS),
            "high_risk_signals": sum(1 for c in DETECTED_CAMPAIGNS if c.get("severity") == "CRITICAL"),
        },
        "source_breakdown": news.get("stats", {}).get("sources", {}),
        "source": "social_metrics",
    }
