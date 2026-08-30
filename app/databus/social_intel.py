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

from __future__ import annotations

import hashlib
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger
from app.databus.vault import resolve_key

logger = get_logger("social_intel")

GROQ_KEY = resolve_key("GROQ_API_KEY")

# ═══════════════════════════════════════════════════════════════════════
# KOL PERFORMANCE TRACKER
# ═══════════════════════════════════════════════════════════════════════

KOL_DATABASE: dict[str, dict[str, Any]] = {}  # handle → {calls: [...], metrics: {...}}


def _canonical(handle: str) -> str:
    """Resolve an (possibly alias) handle to its canonical identity.

    Lazily imported from account_alias to keep module load light and avoid
    cycles; pass-through when the alias module is unavailable.
    """
    try:
        from app.databus.account_alias import canonical_handle

        return canonical_handle(handle)
    except (ImportError, AttributeError):
        return str(handle or "").lower().lstrip("@")


def _kol_key(handle: str) -> str:
    return f"kol:{_canonical(handle)}"


def get_tracked_accounts() -> list[dict[str, Any]]:
    """Influencer registry-backed tracked account list (curated CT + registry).

    Lazily imported from x_intel to keep module load light and avoid cycles.
    """
    from app.databus.x_intel import TRACKED_ACCOUNTS

    return list(TRACKED_ACCOUNTS)


def seed_kol_database_from_registry() -> int:
    """Seed KOL_DATABASE with tracked accounts so KOL tracking is registry-aware.

    Idempotent; returns number of accounts newly seeded.
    """
    from app.databus.x_intel import TRACKED_ACCOUNTS

    seeded = 0
    for acc in TRACKED_ACCOUNTS:
        handle = str(acc.get("handle", "")).lstrip("@")
        if not handle:
            continue
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
                    "tier": acc.get("tier"),
                    "category": acc.get("category"),
                    "farcaster": acc.get("farcaster"),
                },
            }
            seeded += 1
    if seeded:
        logger.info(f"Seeded {seeded} registry accounts into KOL database")
    return seeded


async def track_kol_call(
    handle: str,
    token: str,
    call_type: str = "buy",
    price_at_call: float = 0,
    chain: str = "solana",
    **kw,
) -> dict[str, Any]:
    """Record a KOL making a call on a token.

    call_type: buy, sell, shill, warning, analysis

    The handle is resolved to its canonical identity first so alias/renamed
    handles attribute to the same ``kol_id``; the original handle is kept on
    the call record as ``handle`` for provenance.
    """
    canonical = _canonical(handle)
    key = _kol_key(canonical)
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
        "canonical_handle": canonical,
        "token": token,
        "type": call_type,
        "price_at_call": price_at_call,
        "chain": chain,
        "timestamp": datetime.now(UTC).isoformat(),
        "id": hashlib.sha256(f"{canonical}{token}{time.time()}".encode()).hexdigest()[:8],
    }

    KOL_DATABASE[key]["calls"].append(call)
    m = KOL_DATABASE[key]["metrics"]
    m["total_calls"] += 1
    m[f"{call_type}_calls"] = m.get(f"{call_type}_calls", 0) + 1
    m["tokens_mentioned"].add(token)

    return {"status": "tracked", "call": call}


async def track_deleted_call(
    handle: str,
    token: str,
    call_type: str = "buy",
    chain: str = "solana",
    **kw,
) -> dict[str, Any]:
    """Record a KOL call that was later deleted from X.

    A deleted call is a suspicious signal — the KOL retracted a call after
    the fact. The deletion is stored in ``account_alias.DELETED_POSTS`` keyed
    by canonical handle + token, and the matching in-registry call is tagged
    ``deleted`` so shill detection and scoring can flag it.
    """
    from app.databus.account_alias import record_deleted_post

    canonical = _canonical(handle)
    rec = record_deleted_post(
        canonical,
        token,
        call_type=call_type,
        chain=chain,
        original_handle=handle,
        **kw,
    )

    key = _kol_key(canonical)
    data = KOL_DATABASE.get(key)
    if data:
        for call in data.get("calls", []):
            if (
                str(call.get("token", "")).lower() == str(token).lower()
                and call.get("type") == call_type
                and not call.get("deleted")
            ):
                call["deleted"] = True
                call["deleted_at"] = rec["timestamp"]
                break

    return {"status": "tracked_deleted", "deleted_post": rec}


async def get_kol_profile(handle: str, **kw) -> dict[str, Any]:
    """Get a KOL's performance profile - call history, success rate, risk score.

    The handle is resolved to its canonical identity so alias queries return
    the merged canonical record. Deleted calls (see :func:`track_deleted_call`)
    are surfaced as a suspicious signal that raises the risk level.
    """
    canonical = _canonical(handle)
    key = _kol_key(canonical)
    data = KOL_DATABASE.get(key, {"calls": [], "metrics": {}})
    m = data["metrics"]

    # Calculate risk score
    total = m.get("total_calls", 0)
    shills = m.get("shills", 0)
    warnings = m.get("warnings", 0)
    deleted = len([c for c in data["calls"] if c.get("deleted")])

    if total > 0:
        shill_ratio = shills / total
        warnings / total
        trust_score = max(0, 100 - shill_ratio * 60 - (1 - m.get("win_rate", 0)) * 40)
    else:
        trust_score = 50
    # Deleted calls are a suspicious retraction signal — discount trust.
    trust_score = max(0, trust_score - deleted * 5)

    metrics = {
        **{k: v for k, v in m.items() if k != "tokens_mentioned"},
        "tokens_mentioned": len(m.get("tokens_mentioned", set())),
        "deleted_calls": deleted,
    }

    return {
        "handle": canonical,
        "canonical_handle": canonical,
        "requested_handle": handle if handle != canonical else None,
        "metrics": metrics,
        "trust_score": round(trust_score, 1),
        "risk_level": "HIGH" if trust_score < 30 else "MEDIUM" if trust_score < 60 else "LOW",
        "recent_calls": data["calls"][-10:],
        "source": "kol_tracker",
    }


async def get_kol_leaderboard(limit: int = 20, **kw) -> dict[str, Any]:
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

DETECTED_CAMPAIGNS: list[dict[str, Any]] = []


async def detect_shill_campaigns(posts: list[dict[str, Any]] | None = None, **kw) -> dict[str, Any]:
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
                except (ConnectionError, OSError, TimeoutError):
                    pass

    # Deleted-post signal: KOLs retracting calls on a token after the fact is
    # a suspicious retraction pattern — flag it as a shill indicator.
    try:
        from app.databus.account_alias import get_deleted_posts

        deleted_by_token: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for rec in get_deleted_posts():
            tok = str(rec.get("token", ""))
            if tok:
                deleted_by_token[tok].append(rec)
        for token, recs in deleted_by_token.items():
            campaigns.append(
                {
                    "type": "deleted_posts",
                    "token": token,
                    "severity": "HIGH" if len(recs) >= 3 else "MEDIUM",
                    "kols_involved": sorted({str(r.get("handle", "")) for r in recs}),
                    "deleted_count": len(recs),
                    "detected_at": datetime.now(UTC).isoformat(),
                }
            )
    except ImportError:
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


async def get_shill_alerts(**kw) -> dict[str, Any]:
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


async def scan_scam_channels(**kw) -> dict[str, Any]:
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


async def generate_daily_intel(**kw) -> dict[str, Any]:
    """Generate a comprehensive Daily Intelligence Report using Groq AI.

    Combines: market data, fear/greed, news headlines, CT sentiment,
    prediction markets, on-chain activity into a single briefing.
    """
    # Gather all data sources
    try:
        from app.databus.news_provider import get_market_brief

        brief = await get_market_brief()
    except (AttributeError, ImportError):
        brief = {}

    if brief is None:
        brief = {}

    try:
        from app.databus.news_intel import aggregate_all_news

        news = await aggregate_all_news(limit=15)
    except (AttributeError, ImportError):
        news = {"articles": []}

    try:
        from app.databus.x_intel import fetch_ct_rundown

        ct = await fetch_ct_rundown(limit=10)
    except (AttributeError, ImportError):
        ct = {"rundown": []}

    if ct is None:
        ct = {}

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
        except (httpx.HTTPError, httpx.TimeoutException) as e:
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


async def get_social_metrics(**kw) -> dict[str, Any]:
    """Aggregate social metrics across platforms.

    Tracks: trending topics, sentiment shifts, KOL activity, meme velocity.
    """
    # Gather data
    try:
        from app.databus.news_intel import aggregate_all_news

        news = await aggregate_all_news(limit=50)
    except (AttributeError, ImportError):
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
            "registry_tracked": len(get_tracked_accounts()),
            "active_campaigns": len(DETECTED_CAMPAIGNS),
            "high_risk_signals": sum(1 for c in DETECTED_CAMPAIGNS if c.get("severity") == "CRITICAL"),
        },
        "source_breakdown": news.get("stats", {}).get("sources", {}),
        "source": "social_metrics",
    }
