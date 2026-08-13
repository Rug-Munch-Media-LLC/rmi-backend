"""
Security provider chains.
"""

from __future__ import annotations

from typing import Any

from ...base import Provider, ProviderChain, ProviderTier


def build_security_chains(locals_dict: dict[str, Any]) -> dict[str, ProviderChain]:
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

    # ── Scam Database Lookup ──
    chains["scam_database"] = ProviderChain(
        data_type="scam_database",
        description="Known scam database lookup — RAG search against scam_intel + known_scams collections",
        providers=[
            Provider(
                "local_rag_scam_db",
                ProviderTier.LOCAL,
                _g["_passthrough_rag"],
                weight=10.0,
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
                "lite_chain_indexer_deployer",
                ProviderTier.LOCAL,
                _g["_lite_chain_indexer_deployer_history"],
                weight=20.0,
                is_local=True,
                description="ClickHouse deployer_events — new token launches from indexed chains",
            ),
        ],
    )

    # ── token_security_free — free multi-tier fallback (Arkham-quality replacement) ──
    # Replaces paid/Goplus-only security checks with a stacked free API chain:
    #   Tier 1: own ClickHouse (local, always first)
    #   Tier 2: GoPlus API (free, no key)
    #   Tier 3: Honeypot.is API (free, no key)
    #   Tier 4: RugCheck.xyz (Solana only, free)
    #   Tier 5: DexScreener pair heuristics (free, no key)
    # Circuit breaker integration: ProviderChain.fetch() records failures per
    # provider name and skips open breakers, so rate-limited/failed tiers fall
    # through to the next automatically.
    chains["token_security_free"] = ProviderChain(
        "token_security_free",
        description="Free token security fallback: ClickHouse → GoPlus → Honeypot.is → RugCheck → DexScreener heuristics",
        providers=[
            Provider(
                "free_security_clickhouse",
                ProviderTier.LOCAL,
                _g["_free_security_clickhouse"],
                weight=20.0,
                is_local=True,
                description="Own ClickHouse security analysis (evm_transfers + deployer_events) — zero cost",
            ),
            Provider(
                "free_security_goplus",
                ProviderTier.FREE_API,
                _g["_free_security_goplus"],
                weight=10.0,
                rate_limit_rps=10.0,
                description="GoPlus Security API — honeypot, mint, tax, owner checks (21+ chains, free)",
            ),
            Provider(
                "free_security_honeypot_is",
                ProviderTier.FREE_API,
                _g["_free_security_honeypot_is"],
                weight=8.0,
                rate_limit_rps=2.0,
                description="Honeypot.is — simulated buy/sell, honeypot flag (EVM chains, free)",
            ),
            Provider(
                "free_security_rugcheck",
                ProviderTier.FREE_API,
                _g["_free_security_rugcheck"],
                weight=6.0,
                rate_limit_rps=2.0,
                description="RugCheck.xyz — Solana token risk report (free)",
            ),
            Provider(
                "free_security_dexscreener",
                ProviderTier.FREE_API,
                _g["_free_security_dexscreener"],
                weight=4.0,
                rate_limit_rps=2.0,
                description="DexScreener pair heuristics — buy/sell asymmetry, liquidity depth (free)",
            ),
        ],
    )

    try:
        from app.databus.rugcharts_intel import (
            cross_chain_entity as track_cross_chain_entity,
            developer_reputation as get_dev_reputation,
            holder_health_score as check_holder_health,
            insider_pattern_detector as detect_insiders,
            instant_token_report as get_enhanced_token_report,
            liquidity_risk_monitor as analyze_liquidity_risk,
            rug_pattern_matcher as detect_rug_patterns,
            smart_money_feed as get_smart_money,
            token_launch_scanner,  # noqa: F401 — availability probe for rugcharts_intel
            whale_alert_stream as get_whale_alerts,
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

    # ── lite_chain_indexer_patterns — on-chain behavioral scam patterns ──
    chains["lite_chain_indexer_patterns"] = ProviderChain(
        "lite_chain_indexer_patterns",
        description="On-chain behavioral scam patterns via chain-indexer ClickHouse (holder concentration, pump-and-dump, deployer clusters, mixer interactions)",
        providers=[
            Provider(
                "lite_chain_indexer_pattern_search",
                ProviderTier.LOCAL,
                _g["_lite_chain_indexer_pattern_search"],
                weight=15.0,
                is_local=True,
                description="ClickHouse-powered pattern detection for deployer clusters, holder concentration, pump-and-dump, and mixer interactions",
            ),
        ],
    )

    # ── Own-data Phase 4 providers (ClickHouse, LOCAL tier, PREFER_OWN_DATA) ──
    chains["deployer_reputation"] = ProviderChain(
        "deployer_reputation",
        description="Deployer reputation (0-100) from chain-indexer ClickHouse deployer_events (rugpull/honeypot proxies, lifespan, velocity)",
        providers=[
            Provider(
                "lite_chain_indexer_deployer_reputation",
                ProviderTier.LOCAL,
                _g["_lite_chain_indexer_deployer_reputation"],
                weight=20.0,
                is_local=True,
                description="ClickHouse deployer_events — total tokens deployed, rugpull rate, honeypot rate, average token lifespan, velocity",
            ),
        ],
    )

    # ── C7 — deployer behavioral fingerprint (risk buckets, funding analysis) ──
    chains["deployer_fingerprint"] = ProviderChain(
        "deployer_fingerprint",
        description="Deployer behavioral fingerprint (C7 v1) from chain-indexer ClickHouse — risk buckets (NEW/REPEAT_CLEAN/REPEAT_RUG/FARM/PROLIFIC), survival rate, median lifespan, funder analysis",
        providers=[
            Provider(
                "lite_chain_indexer_deployer_fingerprint",
                ProviderTier.LOCAL,
                _g["_lite_chain_indexer_deployer_fingerprint"],
                weight=20.0,
                is_local=True,
                description="ClickHouse deployer_events + dex_events + evm_transfers + wallet_labels — deployment stats, 7-day survival rate, median lifespan, funder profile (CEX/fresh/multiple), evidence",
            ),
        ],
    )

    chains["whale_detection"] = ProviderChain(
        "whale_detection",
        description="Whale wallet detection from chain-indexer ClickHouse dex_events (large flows, accumulating vs dumping)",
        providers=[
            Provider(
                "lite_chain_indexer_whale_detect",
                ProviderTier.LOCAL,
                _g["_lite_chain_indexer_whale_detect"],
                weight=20.0,
                is_local=True,
                description="ClickHouse dex_events — large transfers >$10K, whale wallets, concentration score, behavior flags",
            ),
        ],
    )

    chains["volume_authenticity"] = ProviderChain(
        "volume_authenticity",
        description="Wash-trading / volume authenticity from chain-indexer ClickHouse (dex_events vs evm_transfers)",
        providers=[
            Provider(
                "lite_chain_indexer_volume_authenticity",
                ProviderTier.LOCAL,
                _g["_lite_chain_indexer_volume_authenticity"],
                weight=20.0,
                is_local=True,
                description="ClickHouse dex_events + evm_transfers — DEX vs transfer volume, wash patterns, authentic score",
            ),
        ],
    )

    chains["holder_age"] = ProviderChain(
        "holder_age",
        description="Holder age distribution from chain-indexer ClickHouse evm_transfers (fresh wallet farm / sudden influx / stable)",
        providers=[
            Provider(
                "lite_chain_indexer_holder_age",
                ProviderTier.LOCAL,
                _g["_lite_chain_indexer_holder_age"],
                weight=20.0,
                is_local=True,
                description="ClickHouse evm_transfers — first-seen per holder, age buckets, fresh-wallet-farm detection",
            ),
        ],
    )

    # ── C2 — BubbleMaps-style holder clustering (LOCAL tier, ClickHouse-only) ──
    chains["holder_clusters"] = ProviderChain(
        "holder_clusters",
        description="BubbleMaps-style holder clustering — balances from evm_transfers deltas, shared-funder / same-block-buy / bundle cluster passes, wallet_labels entity tags",
        providers=[
            Provider(
                "lite_chain_indexer_holder_clusters",
                ProviderTier.LOCAL,
                _g["_lite_chain_indexer_holder_clusters"],
                weight=20.0,
                is_local=True,
                description="ClickHouse evm_transfers + dex_events + wallet_labels + deployer_events — holder concentration metrics and cluster map (C2)",
            ),
        ],
    )

    # ── C6 — time-based attack detection (sandwich, pump & dump, flash loans) ──
    chains["attack_detection"] = ProviderChain(
        "attack_detection",
        description="Time-based attack detection from chain-indexer ClickHouse dex_events — sandwich attacks, pump & dumps, and flash loans (zero external APIs)",
        providers=[
            Provider(
                "lite_chain_indexer_attack_detection",
                ProviderTier.LOCAL,
                _g["_lite_chain_indexer_attack_detection"],
                weight=20.0,
                is_local=True,
                description="ClickHouse dex_events — same-block sandwich triads, 24h pump & dump volume/price patterns, same-tx flash-loan repay patterns",
            ),
        ],
    )

    # ── C3 — new token radar / early warning (LOCAL tier, ClickHouse-only) ──
    chains["new_token_radar"] = ProviderChain(
        "new_token_radar",
        description="New token discovery + early warning from chain-indexer ClickHouse deployer_events — fast scan (honeypot + deployer fingerprint + liquidity), CRITICAL/HIGH/WARNING/INFO scoring, Telegram alerts",
        providers=[
            Provider(
                "lite_chain_indexer_new_token_radar",
                ProviderTier.LOCAL,
                _g["_lite_chain_indexer_new_token_radar"],
                weight=20.0,
                is_local=True,
                description="ClickHouse deployer_events — poll new deployments, auto-scan each pair, score risk, alert CRITICAL/HIGH via alert_hooks",
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
                "lite_chain_indexer_holders",
                ProviderTier.LOCAL,
                _g["_lite_chain_indexer_holders"],
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
