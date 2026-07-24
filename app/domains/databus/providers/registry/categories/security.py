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

    try:
        from app.databus.rugcharts_intel import (
            analyze_liquidity_risk,
            check_holder_health,
            detect_insiders,
            detect_rug_patterns,
            get_dev_reputation,
            get_enhanced_token_report,
            get_smart_money,
            get_token_launches,
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

        chains["token_launches"] = ProviderChain(
            "token_launches",
            description="New token launch scanner",
            providers=[
                Provider(
                    "launch_scanner",
                    ProviderTier.LOCAL,
                    get_token_launches,
                    weight=10.0,
                    rate_limit_rps=5.0,
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

    return chains
