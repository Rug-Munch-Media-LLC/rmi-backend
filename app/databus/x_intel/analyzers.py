"""Analysis, scoring, and summarization for the CT intelligence pipeline."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger
from app.databus.x_intel.fetchers import (
    _cookie_scrape_x,
    _web_scrape_x,
    _xurl_search,
    _xurl_user_timeline,
)
from app.databus.x_intel.helpers import ALL_CT, CT_ACCOUNTS, GROQ_KEY

logger = get_logger("x_intel")


async def _grok_summarize(posts: list[dict[str, Any]]) -> str:
    """Use Groq (free tier) to generate a CT Rundown summary."""
    if not GROQ_KEY or not posts:
        return ""

    try:
        post_texts = "\n---\n".join(f"@{p.get('author', '?')}: {p.get('text', '')[:200]}" for p in posts[:20])

        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a crypto news analyst. Summarize the top stories from Crypto Twitter in 5-7 bullet points. Focus on: breaking news, market-moving events, important protocol updates, security incidents, and regulatory developments. Be concise. Format: '• Story (source: @handle)'",
                        },
                        {
                            "role": "user",
                            "content": f"Here are today's Crypto Twitter posts. Give me the rundown:\n\n{post_texts}",
                        },
                    ],
                    "temperature": 0.3,
                    "max_tokens": 500,
                },
            )
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug(f"Groq summary failed: {e}")

    # Fallback: simple keyword extraction
    return _simple_rundown(posts)


def _simple_rundown(posts: list[dict[str, Any]]) -> str:
    """Fallback: extract top stories by keyword frequency."""
    words = Counter()
    for p in posts:
        text = p.get("text", "").lower()
        for word in text.split():
            if len(word) > 4 and word not in ("https", "that", "this", "with", "from", "have"):
                words[word] += 1

    top_words = [w for w, _ in words.most_common(20) if w not in ("about", "would", "could", "their", "there")]

    return "Top themes: " + ", ".join(top_words[:8])


# ── CT Rundown Algorithm ───────────────────────────────────────────


def score_ct_post(post: dict[str, Any], account: dict[str, Any]) -> float:
    """Score a CT post for the rundown. Higher = more important.

    Factors:
      - Account weight (curated: 0.6-1.0)
      - Engagement: likes * 1 + retweets * 3 + replies * 2
      - Recency: newer posts score higher
      - Content signals: links, threads, breaking keywords
    """
    score = account.get("weight", 0.7) * 10

    # Engagement
    likes = post.get("likes", 0) or 0
    retweets = post.get("retweets", 0) or 0
    replies = post.get("replies", 0) or 0
    engagement = likes + retweets * 3 + replies * 2

    # Log-scale to prevent single viral post from dominating
    if engagement > 0:
        score += min(20, (engagement**0.5) * 0.5)

    # Recency bonus (newer = better)
    created = post.get("created_at", "")
    if created:
        try:
            age_hours = (
                datetime.now(UTC) - datetime.fromisoformat(created.replace("Z", "+00:00"))
            ).total_seconds() / 3600
            score += max(0, 5 - age_hours * 0.5)  # 5 points now, 0 after 10 hours
        except (ConnectionError, OSError, TimeoutError) as e:
            logger.warning(f"score_ct_post failed: {e}")

    # Content signals
    text = post.get("text", "")
    if "BREAKING" in text.upper() or "🚨" in text:
        score += 5
    if "http" in text:  # Has link = more substance
        score += 2
    if text.count("\n") > 3:  # Thread-style
        score += 2

    # Diversity bonus: slightly penalize if same category already represented
    # (applied at selection time, not here)

    return score


async def fetch_ct_rundown(limit: int = 30, **kw) -> dict[str, Any] | None:
    """THE method. Fetch, score, and rank CT posts for the daily rundown.

    Strategy:
      1. Try xurl OAuth for top 10 handles (fast, reliable)
      2. Try cookie scrape for remaining (if available)
      3. Score all posts by engagement + account weight + signals
      4. Select top 20 with category diversity
      5. Generate Groq AI summary
    """
    all_posts = []
    errors = []
    source_method = "none"

    # ── Method 1: xurl OAuth ──
    try:
        # Fetch from top handles first
        priority_handles = [a["handle"] for a in ALL_CT[:10]]
        for handle in priority_handles:
            posts = await _xurl_user_timeline(handle, count=5)
            if posts:
                all_posts.extend(posts)
                if not source_method or source_method == "none":
                    source_method = "xurl_oauth"
    except Exception as e:
        errors.append(f"xurl: {e}")

    # ── Method 2: Cookie scraping ──
    if source_method == "none" or len(all_posts) < 10:
        try:
            cookie_posts = await _cookie_scrape_x([a["handle"] for a in ALL_CT])
            if cookie_posts:
                all_posts.extend(cookie_posts)
                source_method = source_method or "cookie_scrape"
        except Exception as e:
            errors.append(f"cookies: {e}")

    # ── Method 3: Web scraping fallback (DuckDuckGo + embed) ──
    if len(all_posts) < 5:
        try:
            scrape_posts = await _web_scrape_x([a["handle"] for a in ALL_CT], count_per_handle=3)
            if scrape_posts:
                all_posts.extend(scrape_posts)
                source_method = source_method or "web_scrape"
        except Exception as e:
            errors.append(f"web_scrape: {e}")

    # ── Method 4: xurl search (broad) ──
    if len(all_posts) < 5:
        try:
            search_posts = await _xurl_search("crypto OR bitcoin OR ethereum OR defi", count=30)
            if search_posts:
                all_posts.extend(
                    [
                        {
                            "text": p.get("text", ""),
                            "author": p.get("author_id", "unknown"),
                            "likes": p.get("public_metrics", {}).get("like_count", 0),
                            "retweets": p.get("public_metrics", {}).get("retweet_count", 0),
                            "url": f"https://x.com/i/web/status/{p.get('id', '')}",
                        }
                        for p in search_posts
                    ]
                )
                source_method = source_method or "xurl_search"
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            errors.append(f"search: {e}")

    if not all_posts:
        # ── Fallback: return trending tokens + market sentiment ──
        fallback_stories = []
        try:
            from app.databus.news_provider import get_fear_greed, get_trending_coins

            trending = await get_trending_coins()
            fear = await get_fear_greed()

            if trending and trending.get("trending"):
                for i, coin in enumerate(trending["trending"][:10]):
                    fallback_stories.append(
                        {
                            "rank": i + 1,
                            "text": f"Trending: {coin.get('name', '')} ({coin.get('symbol', '')}) — "
                            f"Market Cap Rank #{coin.get('market_cap_rank', 'N/A')}, "
                            f"CoinGecko score: {coin.get('score', 'N/A')}",
                            "author_handle": "coingecko",
                            "author_name": "CoinGecko Trending",
                            "category": "market",
                            "ct_score": 5.0,
                            "engagement": {"likes": 0, "retweets": 0, "replies": 0},
                            "url": "https://www.coingecko.com/",
                            "time": datetime.now(UTC).isoformat(),
                        }
                    )

            if fear:
                fallback_stories.append(
                    {
                        "rank": len(fallback_stories) + 1,
                        "text": f"Fear & Greed Index: {fear['value']}/100 — {fear['classification']}",
                        "author_handle": "alternative_me",
                        "author_name": "Crypto Fear & Greed Index",
                        "category": "market",
                        "ct_score": 4.0,
                        "engagement": {"likes": 0, "retweets": 0, "replies": 0},
                        "url": "https://alternative.me/crypto/fear-and-greed-index/",
                        "time": datetime.now(UTC).isoformat(),
                    }
                )
        except Exception:  # noqa: S110 — intentional best-effort
            pass

        return {
            "rundown": fallback_stories,
            "total_fetched": 0,
            "total_selected": len(fallback_stories),
            "ai_summary": (
                "X/Twitter scraping is currently unavailable. Showing CoinGecko trending coins "
                "and market sentiment instead."
            )
            if fallback_stories
            else "No data available at this time.",
            "source_method": "fallback_trending",
            "categories_represented": ["market"],
            "accounts_tracked": 0,
            "generated_at": datetime.now(UTC).isoformat(),
            "source": "ct_rundown",
            "note": "X/Twitter unavailable — showing market data",
            "methods_tried": ["xurl_oauth", "cookie_scrape", "web_scrape", "xurl_search"],
            "errors": errors,
        }

    # ── Score posts ──
    from app.databus.account_alias import canonical_handle

    account_map = {a["handle"].lower(): a for a in ALL_CT}

    scored = []
    for post in all_posts:
        author = (post.get("author", "") or "").lower().lstrip("@")
        # Resolve renamed/alias handles to their canonical identity so a
        # renamed KOL's posts attribute to the same tracked account.
        canonical = canonical_handle(author)
        account = account_map.get(
            canonical,
            account_map.get(author, {"handle": canonical, "name": canonical, "category": "unknown", "weight": 0.5}),
        )
        post["account"] = account
        post["canonical_handle"] = canonical
        post["ct_score"] = score_ct_post(post, account)
        scored.append(post)

    # ── Select top 20 with category diversity ──
    scored.sort(key=lambda p: -p["ct_score"])
    selected = []
    categories_used = set()

    for post in scored:
        cat = post["account"].get("category", "unknown")
        # Allow max 3 from same category, max 5 total from same handle
        same_cat = len([s for s in selected if s["account"].get("category") == cat])
        same_handle = len([s for s in selected if s["account"].get("handle") == post["account"].get("handle")])

        if same_cat < 3 and same_handle < 3:
            selected.append(post)
            categories_used.add(cat)

        if len(selected) >= 20:
            break

    # ── Generate AI summary ──
    summary = await _grok_summarize(selected[:20]) if GROQ_KEY else _simple_rundown(selected[:20])

    # ── Build result ──
    top_stories = []
    for i, post in enumerate(selected[:limit]):
        top_stories.append(
            {
                "rank": i + 1,
                "text": (post.get("text", "") or "")[:280],
                "author_handle": post["account"].get("handle", ""),
                "author_name": post["account"].get("name", ""),
                "category": post["account"].get("category", ""),
                "ct_score": round(post["ct_score"], 1),
                "engagement": {
                    "likes": post.get("likes", 0) or 0,
                    "retweets": post.get("retweets", 0) or 0,
                    "replies": post.get("replies", 0) or 0,
                },
                "url": post.get("url", ""),
                "time": post.get("created_at", ""),
            }
        )

    return {
        "rundown": top_stories,
        "total_fetched": len(all_posts),
        "total_selected": len(selected),
        "ai_summary": summary,
        "source_method": source_method,
        "categories_represented": sorted(categories_used),
        "accounts_tracked": len(ALL_CT),
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "ct_rundown",
    }


async def track_ct_accounts(**kw) -> dict[str, Any]:
    """Return the curated CT account list with stats."""
    return {
        "accounts": ALL_CT,
        "total": len(ALL_CT),
        "by_category": {cat: [a["handle"] for a in accts] for cat, accts in CT_ACCOUNTS.items()},
        "tiers": list(CT_ACCOUNTS.keys()),
    }
