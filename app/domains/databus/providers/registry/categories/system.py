"""
System provider chains.
"""

from __future__ import annotations

from typing import Any

from ...base import Provider, ProviderChain, ProviderTier


def build_system_chains(locals_dict: dict[str, Any]) -> dict[str, ProviderChain]:
    """Build system provider chains."""
    chains: dict[str, ProviderChain] = {}
    _g = locals_dict
    # ── Conditional imports for optional databus modules ──
    try:
        from app.databus.webhooks import handle_webhook
    except ImportError:
        handle_webhook = None  # type: ignore[assignment]  # optional module fallback
    try:
        from app.databus.model_registry import ai_call, get_usage_stats, review_content
    except ImportError:
        ai_call = get_usage_stats = review_content = None  # type: ignore[assignment]  # optional module fallback
    try:
        from app.databus.rag_ingestion import (
            check_news_staleness,
            index_databus_news,
            nightly_rag_index,
            rag_health_check,
        )
    except ImportError:
        check_news_staleness = index_databus_news = None  # type: ignore[assignment]  # optional module fallback
        nightly_rag_index = rag_health_check = None  # type: ignore[assignment]  # optional module fallback

    chains["alerts"] = ProviderChain(
        data_type="alerts",
        description="Active threat alerts from scanner pipeline",
        providers=[
            Provider("rmi_alerts", ProviderTier.LOCAL, _g["_passthrough_alerts"], weight=10.0),
        ],
    )

    chains["dev_activity"] = ProviderChain(
        data_type="dev_activity",
        description="GitHub developer activity and social volume (Santiment free tier)",
        providers=[
            Provider(
                "santiment_dev",
                ProviderTier.FREEMIUM,
                _g["_santiment_dev_activity"],
                weight=10.0,
                rate_limit_rps=1.0,
                requires_key=True,
                key_env="SANTIMENT_API_KEY",
                monthly_quota=3000,
            ),
        ],
    )

    chains["mcp_bridge"] = ProviderChain(
        data_type="mcp_bridge",
        description="Universal MCP bridge - calls any local/remote MCP server tool",
        providers=[
            Provider("mcp_bridge", ProviderTier.LOCAL, _g["_mcp_bridge"], weight=10.0),
        ],
    )

    chains["webhook_handler"] = ProviderChain(
        "webhook_handler",
        description="Intelligent webhook receiver - Arkham, Helius, Moralis, Alchemy + custom",
        providers=[
            Provider(
                "webhook_processor",
                ProviderTier.LOCAL,
                lambda **kw: handle_webhook(
                    kw.get("service", ""),
                    kw.get("payload", {}),
                    kw.get("headers", {}),
                    kw.get("raw_body", b""),
                ),
                weight=10.0,
            )
        ],
    )

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

    chains["rag_news_realtime"] = ProviderChain(
        "rag_news_realtime",
        description="Realtime DataBus news → RAG news_v2 ingestion (freshest wired source; normalized published_at timestamps)",
        providers=[
            Provider(
                "databus_news_ingest",
                ProviderTier.LOCAL,
                index_databus_news,
                weight=10.0,
                rate_limit_rps=1.0,
            )
        ],
    )

    chains["news_staleness"] = ProviderChain(
        "news_staleness",
        description="News ingestion staleness check - flags multi-day lag in news_v2, reports freshest live source",
        providers=[
            Provider(
                "news_staleness_check",
                ProviderTier.LOCAL,
                check_news_staleness,
                weight=5.0,
                rate_limit_rps=30.0,
            )
        ],
    )

    chains["hyperliquid"] = ProviderChain(
        data_type="hyperliquid",
        description="Hyperliquid perp markets and funding rates",
        providers=[
            Provider("rmi_hyperliquid", ProviderTier.LOCAL, _g["_passthrough_hyperliquid"], weight=10.0),
        ],
    )

    chains["hyperliquid_action"] = ProviderChain(
        data_type="hyperliquid_action",
        description="Hyperliquid gain/loss porn: top movers, funding squeezes, highest volume",
        providers=[
            Provider(
                "rmi_hyperliquid_action",
                ProviderTier.LOCAL,
                _g["_passthrough_hyperliquid_action"],
                weight=10.0,
            ),
        ],
    )

    chains["insider_wallets"] = ProviderChain(
        data_type="insider_wallets",
        description="Likely insider trader wallets to watch (Premium)",
        providers=[
            Provider(
                "rmi_insider_wallets",
                ProviderTier.LOCAL,
                _g["_passthrough_insider_wallets"],
                weight=10.0,
            ),
        ],
    )

    chains["prediction_signals"] = ProviderChain(
        data_type="prediction_signals",
        description="Prediction market intelligence signals (Polymarket, Kalshi, etc.)",
        providers=[
            Provider(
                "rmi_prediction_signals",
                ProviderTier.LOCAL,
                _g["_passthrough_prediction_signals"],
                weight=10.0,
            ),
        ],
    )

    # ── Full News Feed ──
    try:
        from app.databus.news_provider import get_full_news_feed
    except ImportError:
        get_full_news_feed = None  # type: ignore[assignment]  # optional module fallback

    if get_full_news_feed is not None:
        chains["full_news"] = ProviderChain(
            data_type="full_news",
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

    return chains
