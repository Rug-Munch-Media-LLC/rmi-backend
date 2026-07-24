"""
AUTO-GENERATED (split from provider_chains.py) - DO NOT EDIT.

News, social, intelligence, and AI infrastructure chains:
- News & Market Data (6 chains)
- News Intelligence (7 chains)
- X/CT Intelligence (2 chains)
- Social Intelligence (8 chains)
- Model Registry (3 chains)
- RAG Ingestion (2 chains)

Source: app/domains/databus/_generated/provider_chains.py (original god file).
"""

from __future__ import annotations
from app.core.logging import get_logger
from app.databus.provider_core import Provider, ProviderChain, ProviderTier

logger = get_logger("databus.providers.chains.news_social")

def build_news_social_chains(chains: dict[str, ProviderChain]) -> None:
    """Build news, social, CT, model registry, and RAG ingestion chains."""

    # ── NEWS & MARKET DATA - Free APIs ──
    try:
        from app.databus.news_provider import (
            get_fear_greed,
            get_full_news_feed,
            get_market_brief,
            get_market_prices,
            get_prediction_markets,
            get_trending_coins,
        )

        chains["live_prices"] = ProviderChain(
            "live_prices",
            description="Live crypto prices - CoinGecko free tier, multi-coin",
            providers=[
                Provider(
                    "coingecko_prices",
                    ProviderTier.LOCAL,
                    get_market_prices,
                    weight=10.0,
                    rate_limit_rps=10.0,
                )
            ],
        )

        chains["trending_coins"] = ProviderChain(
            "trending_coins",
            description="Trending coins - CoinGecko search, top 10",
            providers=[
                Provider(
                    "coingecko_trending",
                    ProviderTier.LOCAL,
                    get_trending_coins,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        chains["fear_greed"] = ProviderChain(
            "fear_greed",
            description="Crypto Fear & Greed Index - Alternative.me, free, no key",
            providers=[
                Provider(
                    "fear_greed_index",
                    ProviderTier.LOCAL,
                    get_fear_greed,
                    weight=10.0,
                    rate_limit_rps=10.0,
                )
            ],
        )

        chains["prediction_markets"] = ProviderChain(
            "prediction_markets",
            description="Prediction market events - Polymarket, free, no key",
            providers=[
                Provider(
                    "polymarket_events",
                    ProviderTier.LOCAL,
                    get_prediction_markets,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        chains["market_brief"] = ProviderChain(
            "market_brief",
            description="One-call market overview: prices + fear/greed + trending + prediction markets",
            providers=[
                Provider(
                    "market_briefing",
                    ProviderTier.LOCAL,
                    get_market_brief,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        chains["full_news"] = ProviderChain(
            "full_news",
            description="Complete news feed: headlines + market data + fear/greed + polymarket predictions",
            providers=[
                Provider(
                    "full_news_feed",
                    ProviderTier.LOCAL,
                    get_full_news_feed,
                    weight=15.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        logger.info(
            "News & Market Data chains registered (6: prices, trending, fear_greed, "
            "prediction_markets, market_brief, full_news)"
        )
    except ImportError as e:
        logger.warning(f"News providers not available: {e}")

    # ── NEWS INTELLIGENCE ENGINE - multi-source, quality-scored, sentiment-tagged ──
    try:
        from app.databus.news_intel import (
            add_comment,
            add_reaction,
            aggregate_all_news,
            create_bb_post,
            get_academic_papers,
            get_reactions,
            get_social_feed,
            get_weekly_best,
        )

        chains["news_intel"] = ProviderChain(
            "news_intel",
            description="Complete news intelligence - 10+ sources, quality-scored, deduped, sentiment-tagged",
            providers=[
                Provider(
                    "news_engine",
                    ProviderTier.LOCAL,
                    aggregate_all_news,
                    weight=15.0,
                    rate_limit_rps=3.0,
                )
            ],
        )

        chains["weekly_best"] = ProviderChain(
            "weekly_best",
            description="Curated weekly best - highest quality crypto journalism",
            providers=[
                Provider(
                    "weekly_curator",
                    ProviderTier.LOCAL,
                    get_weekly_best,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        chains["academic_papers"] = ProviderChain(
            "academic_papers",
            description="Academic crypto/blockchain research papers from arXiv",
            providers=[
                Provider(
                    "arxiv_fetcher",
                    ProviderTier.LOCAL,
                    get_academic_papers,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        chains["social_feed"] = ProviderChain(
            "social_feed",
            description="Crypto social feed - X/Twitter + CryptoPanic sentiment",
            providers=[
                Provider(
                    "social_aggregator",
                    ProviderTier.LOCAL,
                    get_social_feed,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        chains["article_reactions"] = ProviderChain(
            "article_reactions",
            description="Article reactions - \U0001f525\U0001f402\U0001f43b\U0001f48e\U0001f9e0\U0001f921\U0001f680\u2620 with counts",
            providers=[
                Provider(
                    "react_article",
                    ProviderTier.LOCAL,
                    add_reaction,
                    weight=5.0,
                    rate_limit_rps=30.0,
                ),
                Provider(
                    "get_reactions",
                    ProviderTier.LOCAL,
                    get_reactions,
                    weight=5.0,
                    rate_limit_rps=60.0,
                ),
            ],
        )

        chains["article_comments"] = ProviderChain(
            "article_comments",
            description="Article comments - community discussion on any story",
            providers=[
                Provider(
                    "comment_article",
                    ProviderTier.LOCAL,
                    add_comment,
                    weight=5.0,
                    rate_limit_rps=20.0,
                )
            ],
        )

        chains["bb_post"] = ProviderChain(
            "bb_post",
            description="Convert article to Bulletin Board post for community engagement",
            providers=[
                Provider(
                    "create_bb_post",
                    ProviderTier.LOCAL,
                    create_bb_post,
                    weight=5.0,
                    rate_limit_rps=10.0,
                )
            ],
        )

        logger.info(
            "News Intelligence chains registered (7: news_intel, weekly_best, "
            "academic_papers, social_feed, reactions, comments, bb_post)"
        )
    except ImportError as e:
        logger.warning(f"News Intelligence not available: {e}")

    # ── X/CT INTELLIGENCE - Crypto Twitter Rundown ──
    try:
        from app.databus.x_intel import fetch_ct_rundown, track_ct_accounts

        chains["ct_rundown"] = ProviderChain(
            "ct_rundown",
            description="CT Rundown - top 20 Crypto Twitter stories, AI-summarized, category-diverse",
            providers=[
                Provider(
                    "ct_scanner",
                    ProviderTier.LOCAL,
                    fetch_ct_rundown,
                    weight=15.0,
                    rate_limit_rps=3.0,
                )
            ],
        )

        chains["ct_accounts"] = ProviderChain(
            "ct_accounts",
            description="Curated CT account list - 35+ top accounts across 5 tiers",
            providers=[
                Provider(
                    "ct_account_list",
                    ProviderTier.LOCAL,
                    track_ct_accounts,
                    weight=1.0,
                    rate_limit_rps=60.0,
                )
            ],
        )

        logger.info("X/CT Intelligence chains registered (2: ct_rundown, ct_accounts)")
    except ImportError as e:
        logger.warning(f"X/CT Intelligence not available: {e}")

    # ── SOCIAL INTELLIGENCE - KOL tracking, shill detection, Daily Intel ──
    try:
        from app.databus.daily_intel import generate_daily_intel
        from app.databus.social_intel import (
            detect_shill_campaigns,
            get_kol_leaderboard,
            get_kol_profile,
            get_shill_alerts,
            get_social_metrics,
            scan_scam_channels,
            track_kol_call,
        )

        chains["kol_track"] = ProviderChain(
            "kol_track",
            description="KOL call tracking - record and analyze influencer token calls",
            providers=[
                Provider(
                    "kol_call_tracker",
                    ProviderTier.LOCAL,
                    track_kol_call,
                    weight=5.0,
                    rate_limit_rps=20.0,
                )
            ],
        )

        chains["kol_profile"] = ProviderChain(
            "kol_profile",
            description="KOL performance profile - trust score, call history, win rate",
            providers=[
                Provider(
                    "kol_profiler",
                    ProviderTier.LOCAL,
                    get_kol_profile,
                    weight=5.0,
                    rate_limit_rps=30.0,
                )
            ],
        )

        chains["kol_leaderboard"] = ProviderChain(
            "kol_leaderboard",
            description="KOL leaderboard - ranked by trust score and accuracy",
            providers=[
                Provider(
                    "kol_board",
                    ProviderTier.LOCAL,
                    get_kol_leaderboard,
                    weight=5.0,
                    rate_limit_rps=10.0,
                )
            ],
        )

        chains["shill_detector"] = ProviderChain(
            "shill_detector",
            description="Shill campaign detection - coordinated promotion, paid content, pump-and-dump",
            providers=[
                Provider(
                    "shill_scanner",
                    ProviderTier.LOCAL,
                    detect_shill_campaigns,
                    weight=10.0,
                    rate_limit_rps=5.0,
                ),
                Provider(
                    "shill_alerts",
                    ProviderTier.LOCAL,
                    get_shill_alerts,
                    weight=5.0,
                    rate_limit_rps=20.0,
                ),
            ],
        )

        chains["scam_monitor"] = ProviderChain(
            "scam_monitor",
            description="Scam channel monitor - Telegram/Discord scam pattern detection",
            providers=[
                Provider(
                    "scam_scanner",
                    ProviderTier.LOCAL,
                    scan_scam_channels,
                    weight=5.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        chains["daily_intel"] = ProviderChain(
            "daily_intel",
            description="Daily Intelligence Briefing - OpenRouter free model research + writing, publish to X/Telegram/Ghost",
            providers=[
                Provider(
                    "intel_reporter",
                    ProviderTier.LOCAL,
                    generate_daily_intel,
                    weight=15.0,
                    rate_limit_rps=1.0,
                )
            ],
        )

        chains["social_metrics"] = ProviderChain(
            "social_metrics",
            description="Social metrics aggregator - trending topics, sentiment, KOL activity",
            providers=[
                Provider(
                    "social_aggregator",
                    ProviderTier.LOCAL,
                    get_social_metrics,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        logger.info(
            "Social Intelligence chains registered (8: kol_track, kol_profile, "
            "kol_leaderboard, shill_detector, scam_monitor, daily_intel, social_metrics)"
        )
    except ImportError as e:
        logger.warning(f"Social Intelligence not available: {e}")

    # ── MODEL REGISTRY - Smart free model routing, quality review ──
    try:
        from app.databus.model_registry import ai_call, get_usage_stats, review_content

        chains["ai_task"] = ProviderChain(
            "ai_task",
            description="AI task execution - smart routing across free models (research/writing/coding/review/fast)",
            providers=[
                Provider(
                    "ai_runner",
                    ProviderTier.LOCAL,
                    lambda **kw: ai_call(
                        kw.get("task_type", "fast"),
                        kw.get("system", ""),
                        kw.get("user", ""),
                        kw.get("max_tokens", 1000),
                        kw.get("temperature", 0.5),
                    ),
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        chains["content_review"] = ProviderChain(
            "content_review",
            description="Content quality review - checks for AI-slop, forbidden words, human voice",
            providers=[
                Provider(
                    "quality_reviewer",
                    ProviderTier.LOCAL,
                    review_content,
                    weight=5.0,
                    rate_limit_rps=10.0,
                )
            ],
        )

        chains["ai_usage"] = ProviderChain(
            "ai_usage",
            description="AI model usage statistics - track free tier consumption",
            providers=[
                Provider(
                    "usage_tracker",
                    ProviderTier.LOCAL,
                    get_usage_stats,
                    weight=1.0,
                    rate_limit_rps=60.0,
                )
            ],
        )

        logger.info("Model Registry chains registered (3: ai_task, content_review, ai_usage)")
    except ImportError as e:
        logger.warning(f"Model Registry not available: {e}")

    # ── RAG INGESTION - nightly indexing, health checks ──
    try:
        from app.databus.rag_ingestion import nightly_rag_index, rag_health_check

        chains["rag_nightly"] = ProviderChain(
            "rag_nightly",
            description="Nightly RAG indexing - embeds news, CT, market, social data into vector store",
            providers=[
                Provider(
                    "rag_indexer",
                    ProviderTier.LOCAL,
                    nightly_rag_index,
                    weight=10.0,
                    rate_limit_rps=1.0,
                )
            ],
        )

        chains["rag_health"] = ProviderChain(
            "rag_health",
            description="RAG system health - collection stats, doc counts, embedder status",
            providers=[
                Provider(
                    "rag_checker",
                    ProviderTier.LOCAL,
                    rag_health_check,
                    weight=5.0,
                    rate_limit_rps=30.0,
                )
            ],
        )

        logger.info("RAG Ingestion chains registered (2: rag_nightly, rag_health)")
    except ImportError as e:
        logger.warning(f"RAG Ingestion not available: {e}")
