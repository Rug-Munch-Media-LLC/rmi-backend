"""
Social provider chains.
"""

from __future__ import annotations

from ...base import Provider, ProviderChain, ProviderTier


def build_social_chains(locals_dict: dict) -> dict[str, ProviderChain]:
    """Build social provider chains."""
    chains: dict[str, ProviderChain] = {}
    _g = locals_dict
    # ── Conditional imports for optional databus modules ──
    try:
        from app.databus.news_intel import (
            add_reaction,
            aggregate_all_news,
            fetch_news_intel,
            get_academic_papers,
            get_article_comments,
            get_article_reactions,
            get_social_feed,
            get_weekly_best,
            post_bb_article,
        )
    except ImportError:
        get_weekly_best = get_academic_papers = get_social_feed = None
    try:
        from app.databus.x_intel import fetch_ct_rundown, track_ct_accounts
    except ImportError:
        fetch_ct_rundown = track_ct_accounts = None
    try:
        from app.databus.daily_intel import generate_daily_intel
    except ImportError:
        generate_daily_intel = None
    try:
        from app.databus.social_intel import (
            detect_shill_patterns,
            get_kol_leaderboard,
            get_kol_profile,
            get_social_metrics,
            monitor_scam_campaigns,
            track_kol,
        )
    except ImportError:
        get_kol_profile = get_kol_leaderboard = None
        get_social_metrics = None

    chains["news"] = ProviderChain(
        data_type="news",
        description="Real-time crypto news from 30+ sources + Messari + CoinDesk institutional feeds",
        providers=[
            Provider("rmi_news", ProviderTier.LOCAL, _g["_passthrough_news"], weight=10.0),
            Provider(
                "messari_news",
                ProviderTier.FREEMIUM,
                _g["_messari_news"],
                weight=8.0,
                rate_limit_rps=2.0,
                requires_key=True,
                key_env="MESSARI_API_KEY",
                monthly_quota=10000,
            ),
            Provider(
                "coindesk_news",
                ProviderTier.FREEMIUM,
                _g["_coindesk_news"],
                weight=8.0,
                rate_limit_rps=2.0,
                requires_key=True,
                key_env="COINDESK_API_KEY",
                monthly_quota=10000,
            ),
        ],
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
        description="Article reactions - 🔥🐂🐻💎🧠🤡🚀💀 with counts",
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

    return chains
