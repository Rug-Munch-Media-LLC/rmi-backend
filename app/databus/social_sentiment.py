"""Social Sentiment Metrics Engine (LunarCrush-style).

Computes per-token and per-account social metrics from ingested social
posts (X/nitter RSS, Farcaster, Reddit):

    1. ``mention_count`` over 1h / 24h / 7d windows
    2. ``sentiment_score`` in [-1, 1], weighted by account influence/tier
    3. influencer activity — top accounts mentioning a token
    4. trending ranking — mention velocity x sentiment x influence
    5. ``social_volume`` + ``social_dominance`` (share-of-voice)

Output paths:
  - Redis: ``social:sentiment:token:{TOKEN}``,
    ``social:sentiment:account:{ACCOUNT}``,
    ``social:sentiment:trending`` (zset).
  - RAG: per-token docs embedded into the ``social_sentiment`` collection
    via the existing ``_embed_batch`` pipeline (same path as
    ``rag_ingestion.nightly_rag_index``).
  - Queryable: ``get_social_sentiment`` / ``get_social_trending`` /
    ``get_account_social``.

Post schema (normalized, superset of what the ingestors emit):

    {
        "id": str,
        "text" | "content" | "title": str,      # post body
        "timestamp" | "published" | "ingested_at": str|int|float,
        "platform" | "source_type": "x"|"farcaster"|"reddit"|...,
        "source" | "author" | "username" | "handle": str,
        "followers" | "author_followers" | "follower_count": int,
        "likes" | "retweets" | "replies": int,               # optional
        "tickers": list[str],                                # optional pre-extracted
    }

NOTE: a Farcaster ingestion module does not exist in this repo yet
(Neynar keys are configured in ``app.config`` but nothing writes posts).
``_load_farcaster_posts`` reads ``rmi:farcaster:post:*`` keys when present
and otherwise returns [] — wire the future ingestor to that key pattern.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger

logger = get_logger("social_sentiment")

# ── Windows & weighting constants ────────────────────────────────────

WINDOWS: dict[str, int] = {"1h": 3600, "24h": 86400, "7d": 604800}

# Account tiers by follower count — LunarCrush-style influence buckets.
TIER_WEIGHTS: dict[str, float] = {"mega": 5.0, "macro": 3.0, "micro": 1.5, "retail": 1.0}

# Recency half-life (hours) for social_volume decay.
_VOLUME_HALF_LIFE_H = 24.0

# TTL for cached metrics in Redis (seconds).
_CACHE_TTL = 12 * 3600

# Small curated symbol list for hashtag-style token mentions. Cashtags
# ($SOL) always work; #-tags only match against this list to avoid
# capturing ordinary hashtags. Post ingestors may pass explicit ``tickers``.
KNOWN_TOKENS: frozenset[str] = frozenset(
    {
        "SOL",
        "BTC",
        "ETH",
        "BNB",
        "XRP",
        "ADA",
        "DOGE",
        "SHIB",
        "PEPE",
        "WIF",
        "BONK",
        "JUP",
        "PYTH",
        "JTO",
        "TNSR",
        "WEN",
        "MEW",
        "POPCAT",
        "GIGA",
        "FWOG",
        "LINK",
        "AVAX",
        "DOT",
        "MATIC",
        "ARB",
        "OP",
        "SUI",
        "APT",
        "NEAR",
        "ATOM",
        "UNI",
        "AAVE",
        "LDO",
        "ENA",
        "ONDO",
        "TON",
        "NOT",
        "ORDI",
        "SATS",
        "PENDLE",
    }
)

_CASHTAG_RE = re.compile(r"\$([A-Za-z0-9]{2,12})\b")
_HASHTAG_RE = re.compile(r"#([A-Za-z0-9]{2,12})\b")

# Bare-symbol matching: real ingestors (nitter/reddit) rarely use cashtags,
# so we also match known symbols as standalone words — minus tokens that are
# common English/web words (would flood counts with false positives).
BARE_MATCH_STOPWORDS: frozenset[str] = frozenset(
    {"ONE", "NOT", "OP", "DOT", "LINK", "NEAR", "APE", "TON", "ATOM", "ADA", "MOON", "GEM", "BANANA"}
)
_BARE_TOKENS = sorted(KNOWN_TOKENS - BARE_MATCH_STOPWORDS)
_BARE_RE = re.compile(r"\b(" + "|".join(_BARE_TOKENS) + r")\b", re.IGNORECASE)


# ── Low-level helpers ────────────────────────────────────────────────


def _parse_ts(value: Any) -> float:
    """Parse a timestamp into epoch seconds. Returns 0.0 when unparseable."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        if value > 1e11:  # unix milliseconds
            return value / 1000.0
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        if s.isdigit() and len(s) >= 9:
            try:
                return float(s)
            except ValueError:
                pass
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
        try:
            return float(s)
        except ValueError:
            pass
    logger.debug("Unparseable timestamp %r, using 0", value)
    return 0.0


def _text_of(post: dict[str, Any]) -> str:
    """Best-effort post body from any of the ingestors' field names."""
    for key in ("text", "content", "description", "title", "summary"):
        val = post.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _author_of(post: dict[str, Any]) -> str:
    """Normalize the account handle from various ingestors' field names."""
    for key in ("author", "username", "handle", "source", "screen_name"):
        val = post.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip().lstrip("@").lower()
    return "unknown"


def _platform_of(post: dict[str, Any]) -> str:
    """Map platform/source_type into a canonical platform label."""
    raw = str(post.get("platform") or post.get("source_type") or post.get("network") or "").lower()
    if any(k in raw for k in ("twitter", "x", "nitter", "tweet")):
        return "x"
    if "farcaster" in raw or "warpcast" in raw or "neynar" in raw:
        return "farcaster"
    if "reddit" in raw:
        return "reddit"
    return raw or "unknown"


def _followers_of(post: dict[str, Any]) -> float:
    """Account follower count, incl. nested ``public_metrics`` dicts."""
    for key in ("followers", "author_followers", "follower_count", "followers_count"):
        val = post.get(key)
        if isinstance(val, (int, float)):
            return float(val)
    nested = post.get("public_metrics")
    if isinstance(nested, dict):
        val = nested.get("followers_count", nested.get("followers", 0))
        if isinstance(val, (int, float)):
            return float(val)
    return 0.0


def _engagement_of(post: dict[str, Any]) -> float:
    """Weighted engagement score (likes + 2x retweets + replies)."""
    likes = float(post.get("likes") or post.get("like_count") or 0)
    retweets = float(post.get("retweets") or post.get("retweet_count") or 0)
    replies = float(post.get("replies") or post.get("reply_count") or 0)
    return likes + 2.0 * retweets + replies


def _tier_of(followers: float) -> str:
    if followers >= 1_000_000:
        return "mega"
    if followers >= 100_000:
        return "macro"
    if followers >= 10_000:
        return "micro"
    return "retail"


def _influence_of(followers: float) -> float:
    """Log-scaled influence, 0..~7.7 for 0..50M followers."""
    return math.log10(followers + 1.0)


def _post_weight(post: dict[str, Any], followers: float) -> float:
    """Composite post weight = tier x log-influence x engagement factor."""
    tier_w = TIER_WEIGHTS[_tier_of(followers)]
    influence = _influence_of(followers)
    engagement = 1.0 + 0.1 * math.log10(1.0 + _engagement_of(post))
    return max(0.5, tier_w * influence) * engagement


def _extract_tokens(post: dict[str, Any]) -> list[str]:
    """Extract token symbols from a post (cashtags + explicit tickers + known #tags)."""
    tokens: list[str] = []
    raw = post.get("tickers")
    if isinstance(raw, list):
        tokens.extend(str(t).strip().upper().lstrip("$#") for t in raw if isinstance(t, str) and t.strip())
    text = _text_of(post)
    tokens.extend(_CASHTAG_RE.findall(text))
    for tag in _HASHTAG_RE.findall(text):
        if tag.upper() in KNOWN_TOKENS:
            tokens.append(tag)
    tokens.extend(_BARE_RE.findall(text))
    seen: set[str] = set()
    result: list[str] = []
    for t in (x.upper() for x in tokens):
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result


def _post_sentiment(post: dict[str, Any]) -> float:
    """Sentiment score [-1, 1] via the shared keyword analyzer."""
    text = _text_of(post)
    if not text:
        return 0.0
    try:
        from app.databus.news_intel import analyze_sentiment

        result = analyze_sentiment({"title": text[:120], "summary": text[120:620], "description": text})
        return max(-1.0, min(1.0, float(result.get("score") or 0.0)))
    except (AttributeError, ImportError, TypeError, ValueError):  # pragma: no cover - defensive
        logger.debug("sentiment analyzer unavailable for post %s", post.get("id", ""))
        return 0.0


def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


# ── Core computation (pure, deterministic for tests) ─────────────────


def compute_social_sentiment(posts: list[dict[str, Any]], *, now: float | None = None) -> dict[str, Any]:
    """Compute per-token + per-account social metrics from posts.

    Pure function — no Redis/RAG side effects. ``now`` defaults to
    ``time.time()``; pass an explicit epoch to make results deterministic.
    """
    if now is None:
        now = datetime.now(UTC).timestamp()
    generated_at = datetime.fromtimestamp(now, tz=UTC).isoformat()

    # Normalize posts once.
    normalized: list[dict[str, Any]] = []
    for post in posts:
        ts = _parse_ts(post.get("timestamp") or post.get("published") or post.get("ingested_at"))
        if ts <= 0 or now - ts > WINDOWS["7d"]:
            continue  # skip unparseable / outside 7d lookback
        followers = _followers_of(post)
        normalized.append(
            {
                "id": str(post.get("id") or hashlib.sha256(_text_of(post).encode()).hexdigest()[:12]),
                "ts": ts,
                "age_h": max(0.0, (now - ts) / 3600.0),
                "text": _text_of(post),
                "author": _author_of(post),
                "platform": _platform_of(post),
                "followers": followers,
                "weight": _post_weight(post, followers),
                "tier": _tier_of(followers),
                "sentiment": _post_sentiment(post),
                "tokens": _extract_tokens(post),
            }
        )

    # ── Per-token metrics ────────────────────────────────────────────
    token_posts: dict[str, list[dict[str, Any]]] = {}
    for p in normalized:
        for tok in p["tokens"]:
            token_posts.setdefault(tok, []).append(p)

    tokens: dict[str, Any] = {}
    total_volume = 0.0
    for tok, tp in token_posts.items():
        counts = dict.fromkeys(WINDOWS, 0)
        weighted_sent_sum = 0.0
        weight_sum = 0.0
        volume = 0.0
        platform_counts: dict[str, int] = {}
        account_mentions: dict[str, list[dict[str, Any]]] = {}
        for p in tp:
            for w, secs in WINDOWS.items():
                if p["age_h"] * 3600 <= secs:
                    counts[w] += 1
            volume += p["weight"] * math.exp(-p["age_h"] / _VOLUME_HALF_LIFE_H)
            weighted_sent_sum += p["sentiment"] * p["weight"]
            weight_sum += p["weight"]
            platform_counts[p["platform"]] = platform_counts.get(p["platform"], 0) + 1
            account_mentions.setdefault(p["author"], []).append(p)
        avg_sentiment = weighted_sent_sum / weight_sum if weight_sum > 0 else 0.0
        total_volume += volume

        # Influencer activity: accounts ranked by weighted mentions.
        influencers: list[dict[str, Any]] = []
        for acct, ap in account_mentions.items():
            acct_weight = sum(a["weight"] for a in ap)
            acct_sent = sum(a["sentiment"] * a["weight"] for a in ap) / max(acct_weight, 1e-9)
            influencers.append(
                {
                    "account": acct,
                    "platforms": sorted({a["platform"] for a in ap}),
                    "tier": max((a["tier"] for a in ap), key=lambda t: TIER_WEIGHTS[t]),
                    "followers": max(a["followers"] for a in ap),
                    "mentions": len(ap),
                    "weighted_mentions": round(acct_weight, 3),
                    "avg_sentiment": round(_clamp(acct_sent), 3),
                }
            )
        influencers.sort(key=lambda i: (-i["weighted_mentions"], -i["followers"]))

        tokens[tok] = {
            "token": tok,
            "mention_count": counts,
            "mention_count_24h": counts["24h"],
            "sentiment_score": round(_clamp(avg_sentiment), 3),
            "sentiment_label": _label(avg_sentiment),
            "weighted_sentiment_sum": round(weighted_sent_sum, 3),
            "post_weight_sum": round(weight_sum, 3),
            "social_volume": round(volume, 3),
            "platform_breakdown": platform_counts,
            "influencers": influencers[:10],
            "post_count": len(tp),
        }

    # ── Social dominance + trending ──────────────────────────────────
    for _tok, m in tokens.items():
        m["social_dominance"] = round(m["social_volume"] / total_volume, 4) if total_volume > 0 else 0.0

    max_1h = max((m["mention_count"]["1h"] for m in tokens.values()), default=0)
    max_influence = max((sum(i["weighted_mentions"] for i in m["influencers"]) for m in tokens.values()), default=0)
    trending: list[dict[str, Any]] = []
    for _tok, m in tokens.items():
        velocity = m["mention_count"]["1h"] / max(1, max_1h)
        sentiment_bias = 0.5 + 0.5 * abs(m["sentiment_score"])  # magnitude 0.5..1
        influence_norm = sum(i["weighted_mentions"] for i in m["influencers"]) / max(1, max_influence)
        trend_score = velocity * sentiment_bias * (0.5 + 0.5 * influence_norm)
        m["trend_score"] = round(trend_score, 4)
        trending.append({"token": tok, "trend_score": m["trend_score"], **m})
    trending.sort(key=lambda t: -t["trend_score"])
    for rank, t in enumerate(trending, start=1):
        t["trending_rank"] = rank

    # ── Per-account metrics ──────────────────────────────────────────
    account_posts: dict[str, list[dict[str, Any]]] = {}
    for p in normalized:
        account_posts.setdefault(p["author"], []).append(p)

    accounts: dict[str, Any] = {}
    for acct, ap in account_posts.items():
        counts = dict.fromkeys(WINDOWS, 0)
        sent_sum = 0.0
        w_sum = 0.0
        token_focus: dict[str, int] = {}
        for p in ap:
            for w, secs in WINDOWS.items():
                if p["age_h"] * 3600 <= secs:
                    counts[w] += 1
            sent_sum += p["sentiment"] * p["weight"]
            w_sum += p["weight"]
            for tok in p["tokens"]:
                token_focus[tok] = token_focus.get(tok, 0) + 1
        accounts[acct] = {
            "account": acct,
            "platforms": sorted({p["platform"] for p in ap}),
            "tier": max((p["tier"] for p in ap), key=lambda t: TIER_WEIGHTS[t]),
            "followers": max(p["followers"] for p in ap),
            "mention_count": counts,
            "sentiment_score": round(_clamp(sent_sum / w_sum if w_sum > 0 else 0.0), 3),
            "activity_score": round(sum(p["weight"] for p in ap), 3),
            "token_focus": dict(sorted(token_focus.items(), key=lambda kv: -kv[1])[:10]),
        }

    return {
        "metrics": {"tokens": tokens, "accounts": accounts, "trending": trending},
        "total_posts": len(normalized),
        "tokens_tracked": len(tokens),
        "accounts_tracked": len(accounts),
        "generated_at": generated_at,
        "source": "social_sentiment_engine",
    }


def _label(score: float) -> str:
    if score > 0.3:
        return "bullish"
    if score < -0.3:
        return "bearish"
    if score > 0.1:
        return "slightly_bullish"
    if score < -0.1:
        return "slightly_bearish"
    return "neutral"


# ── Post loading (Redis / ingestors) ────────────────────────────────


def load_ingested_posts(limit: int = 2000) -> list[dict[str, Any]]:
    """Load ingested social posts from Redis.

    Reads the ``rmi:news:social:index`` zset written by
    ``social_feeds.merge_into_redis`` (X/nitter + Reddit) plus any
    ``rmi:farcaster:post:*`` keys. Returns [] when Redis is unavailable.
    """
    posts: list[dict[str, Any]] = []
    try:
        from app.core.redis import get_redis

        r = get_redis()
        if r is None:
            return []
        ids = [str(i) for i in r.zrevrange("rmi:news:social:index", 0, limit - 1)]
        for pid in ids:
            raw = r.get(f"rmi:news:article:{pid}")
            if raw:
                try:
                    posts.append(json.loads(raw))
                except (TypeError, ValueError):
                    continue
        posts.extend(_load_farcaster_posts(r, limit=limit - len(posts)))
        return posts
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("load_ingested_posts failed: %s", str(e)[:120])
        return []


def _load_farcaster_posts(r: Any, limit: int = 500) -> list[dict[str, Any]]:
    """Best-effort load of Farcaster posts from Redis keys.

    [REVIEW] No Farcaster ingestion module exists in this repo yet —
    Neynar keys are configured in ``app.config`` but nothing writes
    ``rmi:farcaster:post:*``. When the ingestor lands, it should emit
    posts with ``platform: "farcaster"`` and this function picks them up.
    """
    posts: list[dict[str, Any]] = []
    try:
        keys = list(r.scan_iter("rmi:farcaster:post:*", count=500))
        for key in keys[:limit]:
            raw = r.get(key)
            if raw:
                try:
                    posts.append(json.loads(raw))
                except (TypeError, ValueError):
                    continue
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("farcaster load failed: %s", str(e)[:80])
    return posts


# ── Storage (Redis + RAG) ───────────────────────────────────────────


def store_social_sentiment(results: dict[str, Any]) -> dict[str, Any]:
    """Persist computed metrics to Redis with a 12h TTL."""
    stored = {"tokens": 0, "accounts": 0, "trending": 0}
    try:
        from app.core.redis import get_redis

        r = get_redis()
        if r is None:
            return stored
        metrics = results.get("metrics", {})
        pipe = r.pipeline(transaction=False)
        for tok, m in metrics.get("tokens", {}).items():
            pipe.set(f"social:sentiment:token:{tok}", json.dumps(m, default=str), ex=_CACHE_TTL)
            stored["tokens"] += 1
        for acct, m in metrics.get("accounts", {}).items():
            pipe.set(f"social:sentiment:account:{acct}", json.dumps(m, default=str), ex=_CACHE_TTL)
            stored["accounts"] += 1
        for t in metrics.get("trending", []):
            pipe.zadd("social:sentiment:trending", {t["token"]: t["trend_score"]})
        pipe.expire("social:sentiment:trending", _CACHE_TTL)
        pipe.execute()
        return stored
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("store_social_sentiment failed: %s", str(e)[:120])
        return stored


async def index_social_sentiment_to_rag(limit_tokens: int = 50) -> dict[str, Any]:
    """Embed per-token sentiment metrics into the RAG ``social_sentiment`` collection.

    Mirrors ``rag_ingestion.nightly_rag_index`` — reuses the shared
    ``_embed_batch`` pipeline (BGE-M3 embeddings + Redis doc store + ANN).
    """
    posts = load_ingested_posts()
    results = compute_social_sentiment(posts)
    metrics = results.get("metrics", {})
    docs: list[dict[str, Any]] = []
    for rank, t in enumerate(metrics.get("trending", [])[:limit_tokens], start=1):
        doc_id = f"social_sentiment:{t['token']}:{datetime.now(UTC).strftime('%Y%m%d')}"
        text = (
            f"Token {t['token']} social sentiment: {t['sentiment_label']} "
            f"(score {t['sentiment_score']:.2f}). Mentions 1h/24h/7d: "
            f"{t['mention_count']['1h']}/{t['mention_count']['24h']}/{t['mention_count']['7d']}. "
            f"Social volume {t['social_volume']}, dominance {t['social_dominance']:.2%}, "
            f"trending rank #{t['trending_rank']}. Top accounts: "
            + ", ".join(i["account"] for i in t.get("influencers", [])[:5])
            + ". "
            + json.dumps(t, default=str)[:800]
        )
        docs.append(
            {
                "id": doc_id,
                "text": text[:2000],
                "metadata": {
                    "token": t["token"],
                    "sentiment_score": t["sentiment_score"],
                    "sentiment_label": t["sentiment_label"],
                    "mentions_24h": t["mention_count"]["24h"],
                    "social_volume": t["social_volume"],
                    "social_dominance": t["social_dominance"],
                    "trending_rank": rank,
                    "date": datetime.now(UTC).isoformat(),
                    "source": "social_sentiment_engine",
                },
            }
        )
    try:
        from app.databus.rag_ingestion import _embed_batch

        ingested = await _embed_batch(docs, "social_sentiment")
    except (ImportError, AttributeError) as e:  # pragma: no cover - defensive
        logger.warning("RAG embed unavailable: %s", str(e)[:80])
        ingested = 0
    return {
        "status": "ok",
        "collection": "social_sentiment",
        "posts_analyzed": results["total_posts"],
        "tokens_analyzed": results["tokens_tracked"],
        "docs_built": len(docs),
        "ingested": ingested,
        "generated_at": results["generated_at"],
        "source": "social_sentiment_engine",
    }


# ── Queryable results ───────────────────────────────────────────────


def get_social_sentiment(token: str, window: str = "24h") -> dict[str, Any]:
    """Queryable per-token sentiment metrics.

    Returns the cached result when fresh, otherwise recomputes from
    ingested posts (and stores).
    """
    token = token.strip().upper().lstrip("$#")
    window = window if window in WINDOWS else "24h"
    try:
        from app.core.redis import get_redis

        r = get_redis()
        if r is not None:
            cached = r.get(f"social:sentiment:token:{token}")
            if cached:
                data = json.loads(cached)
                return {
                    "token": token,
                    "window": window,
                    "mention_count": data.get("mention_count", {}).get(window, 0),
                    "sentiment_score": data.get("sentiment_score", 0.0),
                    "sentiment_label": data.get("sentiment_label", "neutral"),
                    "social_volume": data.get("social_volume", 0.0),
                    "social_dominance": data.get("social_dominance", 0.0),
                    "trend_score": data.get("trend_score", 0.0),
                    "influencers": data.get("influencers", [])[:10],
                    "cached": True,
                    "source": "social_sentiment_engine",
                }
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("get_social_sentiment cache read failed: %s", str(e)[:80])

    results = compute_social_sentiment(load_ingested_posts())
    m = results.get("metrics", {}).get("tokens", {}).get(token)
    if m is None:
        return {"token": token, "window": window, "found": False, "source": "social_sentiment_engine"}
    store_social_sentiment(results)
    return {
        "token": token,
        "window": window,
        "mention_count": m["mention_count"].get(window, 0),
        "sentiment_score": m["sentiment_score"],
        "sentiment_label": m["sentiment_label"],
        "social_volume": m["social_volume"],
        "social_dominance": m["social_dominance"],
        "trend_score": m["trend_score"],
        "influencers": m["influencers"][:10],
        "found": True,
        "cached": False,
        "source": "social_sentiment_engine",
    }


def get_social_trending(limit: int = 20, window: str = "24h") -> dict[str, Any]:
    """Queryable trending ranking (velocity x sentiment x influence)."""
    limit = max(1, min(100, int(limit)))
    try:
        from app.core.redis import get_redis

        r = get_redis()
        if r is not None:
            cached = r.zrevrange("social:sentiment:trending", 0, limit - 1)
            if cached:
                return {
                    "trending": [{"token": str(t), "window": window} for t in cached],
                    "count": len(cached),
                    "cached": True,
                    "source": "social_sentiment_engine",
                }
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("get_social_trending cache read failed: %s", str(e)[:80])

    results = compute_social_sentiment(load_ingested_posts())
    store_social_sentiment(results)
    trending = [
        {"token": t["token"], "trend_score": t["trend_score"], "window": window}
        for t in results.get("metrics", {}).get("trending", [])[:limit]
    ]
    return {"trending": trending, "count": len(trending), "cached": False, "source": "social_sentiment_engine"}


def get_account_social(account: str) -> dict[str, Any]:
    """Queryable per-account metrics (influence/tier, sentiment, token focus)."""
    account = account.strip().lstrip("@").lower()
    try:
        from app.core.redis import get_redis

        r = get_redis()
        if r is not None:
            cached = r.get(f"social:sentiment:account:{account}")
            if cached:
                return {**json.loads(cached), "cached": True, "source": "social_sentiment_engine"}
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("get_account_social cache read failed: %s", str(e)[:80])

    results = compute_social_sentiment(load_ingested_posts())
    m = results.get("metrics", {}).get("accounts", {}).get(account)
    if m is None:
        return {"account": account, "found": False, "source": "social_sentiment_engine"}
    store_social_sentiment(results)
    return {**m, "found": True, "cached": False, "source": "social_sentiment_engine"}
