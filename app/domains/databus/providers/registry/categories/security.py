"""
Security provider chains.
"""

from __future__ import annotations

from ...base import Provider, ProviderChain, ProviderTier


def build_security_chains(locals_dict: dict) -> dict[str, ProviderChain]:
    """Build security provider chains.

    NOTE: Premium scanner, volume_authenticity, token_security, and
    rugcharts_intel chains are registered in _generated/chains/premium.py
    (via ``build_premium_chains``).  This function only registers the core
    security chains.  The rest are wrapped in try/except for optional
    module availability.
    """
    chains: dict[str, ProviderChain] = {}
    _g = locals_dict

    # ── Core security chains (always available) ──
    chains["url_security_scan"] = ProviderChain(
        data_type="url_security_scan",
        description="Phishing and malware scan for token websites (VirusTotal free tier)",
        providers=[
            Provider(
                "virustotal_scan",
                ProviderTier.FREEMIUM,
                _g["_virustotal_url_scan"],
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
                _g["_passthrough_scanner"],
                weight=10.0,
                rate_limit_rps=2.0,
            ),
        ],
    )

    chains["spl_token_metadata"] = ProviderChain(
        data_type="spl_token_metadata",
        description="Raw SPL token metadata decoder",
        providers=[
            Provider(
                "spl_metadata_decoder",
                ProviderTier.FREE_API,
                _g["_spl_metadata_decoder_provider"],
                weight=10.0,
                rate_limit_rps=3.0,
            ),
        ],
    )

    # ── Optional security chains (silent skip if unavailable) ──
    # These are duplicated in _generated/chains/premium.py; kept here
    # for backward compat until the registry is fully deduplicated.
    try:
        from app.databus.volume_authenticity import analyze_volume_authenticity

        chains["volume_authenticity"] = ProviderChain(
            "volume_authenticity",
            description="Fake volume detection",
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
    except ImportError:
        pass

    try:
        from app.databus.token_security import get_check_matrix_endpoint, run_full_scan

        chains["token_security"] = ProviderChain(
            "token_security",
            description="37+ security checks",
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
    except ImportError:
        pass

    # ── token_launches — always available via chain-indexer ClickHouse ──
    chains["token_launches"] = ProviderChain(
        "token_launches",
        description="New token launch detection via chain-indexer ClickHouse deployer_events",
        providers=[
            Provider(
                "chain_indexer_deployer",
                ProviderTier.LOCAL,
                _g["_chain_indexer_deployer_history"],
                weight=20.0,
                is_local=True,
                description="ClickHouse deployer_events — new token launches from indexed chains",
            ),
        ],
    )

    try:
        from app.databus.rugcharts_intel import (
            analyze_liquidity_risk,
            check_holder_health,
            detect_insiders,
            detect_rug_patterns,
            get_dev_reputation,
            get_enhanced_token_report,
            get_smart_money,
            get_token_launches,  # noqa: F401  # availability probe for rugcharts_intel
            get_whale_alerts,
            track_cross_chain_entity,
        )

        chains["smart_money"] = ProviderChain(
            "smart_money",
            description="Smart money feed",
            providers=[
                Provider(
                    "smart_money_tracker",
                    ProviderTier.LOCAL,
                    get_smart_money,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        chains["whale_alerts"] = ProviderChain(
            "whale_alerts",
            description="Real-time whale transaction detection",
            providers=[
                Provider(
                    "whale_detector",
                    ProviderTier.LOCAL,
                    get_whale_alerts,
                    weight=10.0,
                    rate_limit_rps=10.0,
                )
            ],
        )

        chains["insider_detection"] = ProviderChain(
            "insider_detection",
            description="Pre-pump accumulation pattern detection",
            providers=[
                Provider(
                    "insider_detector",
                    ProviderTier.LOCAL,
                    detect_insiders,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        chains["liquidity_risk"] = ProviderChain(
            "liquidity_risk",
            description="LP health monitor",
            providers=[
                Provider(
                    "liquidity_monitor",
                    ProviderTier.LOCAL,
                    analyze_liquidity_risk,
                    weight=10.0,
                    rate_limit_rps=10.0,
                )
            ],
        )

        chains["holder_health"] = ProviderChain(
            "holder_health",
            description="Holder distribution analysis",
            providers=[
                Provider(
                    "holder_analyzer",
                    ProviderTier.LOCAL,
                    check_holder_health,
                    weight=10.0,
                    rate_limit_rps=10.0,
                )
            ],
        )

        chains["cross_chain_entity"] = ProviderChain(
            "cross_chain_entity",
            description="Cross-chain entity resolution",
            providers=[
                Provider(
                    "entity_tracer",
                    ProviderTier.LOCAL,
                    track_cross_chain_entity,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        chains["rug_patterns"] = ProviderChain(
            "rug_patterns",
            description="Rug pull pattern matcher",
            providers=[
                Provider(
                    "rug_matcher",
                    ProviderTier.LOCAL,
                    detect_rug_patterns,
                    weight=10.0,
                    rate_limit_rps=10.0,
                )
            ],
        )

        chains["dev_reputation"] = ProviderChain(
            "dev_reputation",
            description="Developer reputation",
            providers=[
                Provider(
                    "dev_reputation_api",
                    ProviderTier.LOCAL,
                    get_dev_reputation,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        chains["token_report"] = ProviderChain(
            "token_report",
            description="Enhanced token report",
            providers=[
                Provider(
                    "report_generator",
                    ProviderTier.LOCAL,
                    get_enhanced_token_report,
                    weight=15.0,
                    rate_limit_rps=3.0,
                )
            ],
        )
    except ImportError:
        pass

    # ── chain_indexer_patterns — on-chain behavioral scam patterns ──
    chains["chain_indexer_patterns"] = ProviderChain(
        "chain_indexer_patterns",
        description="On-chain behavioral scam patterns via chain-indexer ClickHouse (holder concentration, pump-and-dump, deployer clusters, mixer interactions)",
        providers=[
            Provider(
                "chain_indexer_pattern_search",
                ProviderTier.LOCAL,
                _g["_chain_indexer_pattern_search"],
                weight=15.0,
                is_local=True,
                description="ClickHouse-powered pattern detection for deployer clusters, holder concentration, pump-and-dump, and mixer interactions",
            ),
        ],
    )

    try:
        from app.databus.data_quality import get_tier_comparison

        chains["tier_comparison"] = ProviderChain(
            "tier_comparison",
            description="Competitive tier comparison",
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
    except ImportError:
        pass

    # ── Threat intelligence check (known scams, malicious patterns, risk) ──
    chains["threat_check"] = ProviderChain(
        data_type="threat_check",
        description="Threat intelligence check — scraped intel → SENTINEL scan → RAG known scams",
        providers=[
            Provider(
                "scraped_intel_local",
                ProviderTier.LOCAL,
                _g["_scraped_intel_provider"],
                weight=15.0,
                is_local=True,
                description="Scraped scam reports / threat intel from pryscraper Postgres",
            ),
            Provider(
                "sentinel_scanner",
                ProviderTier.LOCAL,
                _g["_passthrough_scanner"],
                weight=10.0,
                rate_limit_rps=2.0,
            ),
            Provider(
                "local_rag",
                ProviderTier.LOCAL,
                _g["_passthrough_rag"],
                weight=5.0,
            ),
        ],
    )

    # ── Smart contract deep scan (Slither + Mythril) ──
    chains["contract_scan"] = ProviderChain(
        data_type="contract_scan",
        description="Deep contract audit — SENTINEL scan → scraped intel → RAG",
        providers=[
            Provider(
                "sentinel_scanner",
                ProviderTier.LOCAL,
                _g["_passthrough_scanner"],
                weight=10.0,
                rate_limit_rps=2.0,
            ),
            Provider(
                "scraped_intel_local",
                ProviderTier.LOCAL,
                _g["_scraped_intel_provider"],
                weight=8.0,
                is_local=True,
                description="Scraped scam reports / threat intel from pryscraper Postgres",
            ),
            Provider(
                "local_rag",
                ProviderTier.LOCAL,
                _g["_passthrough_rag"],
                weight=3.0,
            ),
        ],
    )

    # ── Deep SENTINEL scan + threat feeds + contract analysis ──
    chains["sentinel_deep"] = ProviderChain(
        data_type="sentinel_deep",
        description="Full threat scan — SENTINEL deep scan → scraped intel → RAG known scams",
        providers=[
            Provider(
                "sentinel_scanner",
                ProviderTier.LOCAL,
                _g["_passthrough_scanner"],
                weight=10.0,
                rate_limit_rps=2.0,
            ),
            Provider(
                "scraped_intel_local",
                ProviderTier.LOCAL,
                _g["_scraped_intel_provider"],
                weight=8.0,
                is_local=True,
                description="Scraped scam reports / threat intel from pryscraper Postgres",
            ),
            Provider(
                "local_rag",
                ProviderTier.LOCAL,
                _g["_passthrough_rag"],
                weight=5.0,
            ),
        ],
    )

    # ── Holder concentration map (RugMap) ──
    chains["rugmap"] = ProviderChain(
        data_type="rugmap",
        description="Holder concentration map — ClickHouse holders → DexScreener holders → Birdeye",
        providers=[
            Provider(
                "chain_indexer_holders",
                ProviderTier.LOCAL,
                _g["_chain_indexer_holders"],
                weight=20.0,
                is_local=True,
                description="ClickHouse evm_transfers aggregation — holder distribution from transfer activity",
            ),
            Provider(
                "dexscreener_holders",
                ProviderTier.FREE_API,
                _g["_dexscreener_holders"],
                weight=8.0,
                rate_limit_rps=2.0,
            ),
            Provider(
                "birdeye_overview_api",
                ProviderTier.FREEMIUM,
                _g["_birdeye_overview"],
                weight=5.0,
                rate_limit_rps=2.0,
                requires_key=True,
                key_env="BIRDEYE_API_KEY",
                monthly_quota=50000,
            ),
        ],
    )

    return chains
