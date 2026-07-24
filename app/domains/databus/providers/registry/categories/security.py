"""
Security provider chains.
"""

from ..base import Provider, ProviderChain, ProviderTier


def build_security_chains(locals_dict: dict) -> dict[str, ProviderChain]:
    """Build security provider chains."""
    chains: dict[str, ProviderChain] = {}
    _g = locals_dict

    chains["url_security_scan"] = ProviderChain(
        data_type="url_security_scan",
        description="Phishing and malware scan for token websites (VirusTotal free tier)",
        providers=[
            Provider(
                "virustotal_scan",
                ProviderTier.FREEMIUM,
                _virustotal_url_scan,
                weight=10.0,
                rate_limit_rps=0.5,
                requires_key=True,
                key_env="VIRUSTOTAL_API_KEY",
                monthly_quota=15000,
            ),
        ],
    )


    chains["scanner"] = ProviderChain(
        data_type="scanner",
        description="Token security scan via SENTINEL (rug pull, honeypot, risk score)",
        providers=[
            Provider(
                "sentinel_scanner",
                ProviderTier.LOCAL,
                _passthrough_scanner,
                weight=10.0,
                rate_limit_rps=2.0,
            ),
        ],
    )


    chains["spl_token_metadata"] = ProviderChain(
        data_type="spl_token_metadata",
        description="Raw SPL token metadata decoder: mint authority, freeze authority, decimals, supply, and Token-2022 extensions",
        providers=[
            Provider(
                "spl_metadata_decoder",
                ProviderTier.FREE_API,
                _spl_metadata_decoder_provider,
                weight=10.0,
                rate_limit_rps=3.0,
            ),
        ],
    )


    chains["bundle_detect"] = ProviderChain(
            "bundle_detect",
            description="Coordinated wallet bundle detection (RugMaps-style cluster analysis)",
            providers=[_premium_provider("bundle_scanner", detect_bundles)],
        )

    chains["cluster_map"] = ProviderChain(
            "cluster_map",
            description="Full wallet cluster mapping - funders, recipients, counterparties (graph-ready)",
            providers=[_premium_provider("cluster_mapper", map_clusters, rps=1.0)],
        )

    chains["dev_finder"] = ProviderChain(
            "dev_finder",
            description="Find developer/creator wallets behind tokens (deployer→funder→team)",
            providers=[_premium_provider("dev_finder_scanner", find_dev_wallets)],
        )

    chains["sniper_detect"] = ProviderChain(
            "sniper_detect",
            description="Sniper detection - first-block buyers with fast dump patterns",
            providers=[_premium_provider("sniper_scanner", detect_snipers)],
        )

    chains["bot_farm_detect"] = ProviderChain(
            "bot_farm_detect",
            description="Bot farm detection - identical behavior patterns across wallets",
            providers=[_premium_provider("bot_farm_scanner", detect_bot_farms)],
        )

    chains["copy_trade_detect"] = ProviderChain(
            "copy_trade_detect",
            description="Copy trading pattern detection - wallets mirroring trades with delay",
            providers=[_premium_provider("copy_trade_scanner", detect_copy_trading)],
        )

    chains["insider_detect"] = ProviderChain(
            "insider_detect",
            description="Insider trading signals - large buys before major announcements",
            providers=[_premium_provider("insider_scanner", detect_insider_signals)],
        )

    chains["wash_trade_detect"] = ProviderChain(
            "wash_trade_detect",
            description="Wash trading detection - circular transactions, self-trading",
            providers=[_premium_provider("wash_trade_scanner", detect_wash_trading)],
        )

    chains["mev_detect"] = ProviderChain(
            "mev_detect",
            description="MEV sandwich attack detection - frontrun/backrun patterns",
            providers=[_premium_provider("mev_scanner", detect_mev_sandwich)],
        )

    chains["fresh_wallet_analysis"] = ProviderChain(
            "fresh_wallet_analysis",
            description="Fresh wallet concentration analysis - high new-wallet % = rug risk",
            providers=[_premium_provider("fresh_wallet_scanner", detect_fresh_wallets, rps=2.0)],
        )

    chains["volume_authenticity"] = ProviderChain(
            "volume_authenticity",
            description="Fake volume detection - 4-layer analysis (statistical, graph, heuristic, ML) with bootstrap CI",
            providers=[
                Provider(
                    "volume_auth_scorer",
                    ProviderTier.LOCAL,
                    analyze_volume_authenticity,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

    chains["token_security"] = ProviderChain(
            "token_security",
            description="37+ security checks - GoPlus, honeypot, contract, liquidity, holders, deployer, tokenomics, rug pull",
            providers=[
                Provider(
                    "security_scanner",
                    ProviderTier.LOCAL,
                    run_full_scan,
                    weight=10.0,
                    rate_limit_rps=10.0,
                ),
                Provider(
                    "check_matrix",
                    ProviderTier.LOCAL,
                    get_check_matrix_endpoint,
                    weight=1.0,
                    rate_limit_rps=60.0,
                ),
            ],
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

    return chains
