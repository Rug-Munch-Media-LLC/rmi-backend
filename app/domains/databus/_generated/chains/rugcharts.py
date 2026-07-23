"""
AUTO-GENERATED (split from provider_chains.py) - DO NOT EDIT.

RugCharts Intelligence provider chains (10 premium features):
smart money, whale alerts, token launches, insider detection,
liquidity risk, holder health, cross-chain entity tracing,
rug pattern matching, developer reputation, token reports,
and tier comparison.

Source: app/domains/databus/_generated/provider_chains.py (original god file).
"""

from app.core.logging import get_logger
from app.databus.provider_core import Provider, ProviderChain, ProviderTier

logger = get_logger("databus.providers.chains.rugcharts")


def build_rugcharts_chains(chains: dict[str, ProviderChain]) -> None:
    """Build RugCharts Intelligence chains (10 premium features)."""
    # ── RUGCHARTS INTELLIGENCE - 10 Premium Features ──
    try:
        from app.databus.data_quality import enhanced_token_report, get_tier_comparison
        from app.databus.rugcharts_intel import (
            cross_chain_entity,
            developer_reputation,
            holder_health_score,
            insider_pattern_detector,
            liquidity_risk_monitor,
            rug_pattern_matcher,
            smart_money_feed,
            token_launch_scanner,
            whale_alert_stream,
        )

        chains["smart_money"] = ProviderChain(
            "smart_money",
            description="Smart money feed - what profitable wallets are buying right now",
            providers=[
                Provider(
                    "smart_money_tracker",
                    ProviderTier.LOCAL,
                    smart_money_feed,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        chains["whale_alerts"] = ProviderChain(
            "whale_alerts",
            description="Real-time whale transaction detection - large transfers across chains",
            providers=[
                Provider(
                    "whale_detector",
                    ProviderTier.LOCAL,
                    whale_alert_stream,
                    weight=10.0,
                    rate_limit_rps=10.0,
                )
            ],
        )

        chains["token_launches"] = ProviderChain(
            "token_launches",
            description="New token launch scanner with instant risk scoring (by age)",
            providers=[
                Provider(
                    "launch_scanner",
                    ProviderTier.LOCAL,
                    token_launch_scanner,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        chains["insider_detection"] = ProviderChain(
            "insider_detection",
            description="Pre-pump accumulation pattern detection - volume spikes before price moves",
            providers=[
                Provider(
                    "insider_detector",
                    ProviderTier.LOCAL,
                    insider_pattern_detector,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        chains["liquidity_risk"] = ProviderChain(
            "liquidity_risk",
            description="LP health monitor - concentration, lock status, removal risk",
            providers=[
                Provider(
                    "liquidity_monitor",
                    ProviderTier.LOCAL,
                    liquidity_risk_monitor,
                    weight=10.0,
                    rate_limit_rps=10.0,
                )
            ],
        )

        chains["holder_health"] = ProviderChain(
            "holder_health",
            description="Holder distribution analysis - Gini, concentration, decentralization score",
            providers=[
                Provider(
                    "holder_analyzer",
                    ProviderTier.LOCAL,
                    holder_health_score,
                    weight=10.0,
                    rate_limit_rps=10.0,
                )
            ],
        )

        chains["cross_chain_entity"] = ProviderChain(
            "cross_chain_entity",
            description="Cross-chain entity resolution via Arkham - trace wallets across all chains",
            providers=[
                Provider(
                    "entity_tracer",
                    ProviderTier.LOCAL,
                    cross_chain_entity,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        chains["rug_patterns"] = ProviderChain(
            "rug_patterns",
            description="Rug pull pattern matcher - similarity scoring against 10 known scam patterns",
            providers=[
                Provider(
                    "rug_matcher",
                    ProviderTier.LOCAL,
                    rug_pattern_matcher,
                    weight=10.0,
                    rate_limit_rps=10.0,
                )
            ],
        )

        chains["dev_reputation"] = ProviderChain(
            "dev_reputation",
            description="Developer reputation - deployer history, token count, entity resolution",
            providers=[
                Provider(
                    "dev_reputation",
                    ProviderTier.LOCAL,
                    developer_reputation,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        chains["token_report"] = ProviderChain(
            "token_report",
            description="ONE-CALL enhanced token report - smart verdicts, entity enrichment, trust adjustments, tier-aware",
            providers=[
                Provider(
                    "report_generator",
                    ProviderTier.LOCAL,
                    enhanced_token_report,
                    weight=15.0,
                    rate_limit_rps=3.0,
                )
            ],
        )

        chains["tier_comparison"] = ProviderChain(
            "tier_comparison",
            description="Competitive tier comparison - RugCharts vs DexScreener vs Nansen vs GMGN",
            providers=[
                Provider(
                    "tier_compare",
                    ProviderTier.LOCAL,
                    get_tier_comparison,
                    weight=1.0,
                    rate_limit_rps=60.0,
                )
            ],
        )

        logger.info("RugCharts Intelligence: 10 premium chains registered")
    except ImportError as e:
        logger.warning(f"RugCharts Intelligence not available: {e}")
