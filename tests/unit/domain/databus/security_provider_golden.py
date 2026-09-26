"""Frozen per-provider ATTRIBUTE golden for the security provider chains.

Split from ``security_chain_golden`` so each golden module stays single
responsibility: that one owns chain ORDER, this one owns the provider
attributes (name, tier, weight, is_local, requires_key, key_env, quota) the
circuit breaker, rate limiter and tier routing key on.

Same PATCH-FREE contract as the order golden — see
``security_chain_golden`` for why. Captured from the pre-split 631-line
``security.py``; a split that moves, renames or reweights any provider fails
``test_registry_security_chains.py::test_provider_attributes_are_unchanged``.
"""

from __future__ import annotations

# (provider name, tier, weight, is_local, requires_key, key_env, quota) for
# every provider in every chain.
GOLDEN_PROVIDERS: dict[str, tuple[tuple[str, str, float, bool, bool, str, int], ...]] = {
    "url_security_scan": (("virustotal_scan", "freemium", 10.0, False, True, "VIRUSTOTAL_API_KEY", 15000),),
    "scanner": (("sentinel_scanner", "local", 10.0, False, False, "", 0),),
    "scam_database": (("local_rag_scam_db", "local", 10.0, False, False, "", 0),),
    "spl_token_metadata": (("spl_metadata_decoder", "free_api", 10.0, False, False, "", 0),),
    "volume_authenticity": (("lite_chain_indexer_volume_authenticity", "local", 20.0, True, False, "", 0),),
    "token_security": (
        ("security_scanner", "local", 10.0, False, False, "", 0),
        ("check_matrix", "local", 1.0, False, False, "", 0),
    ),
    "token_launches": (("lite_chain_indexer_deployer", "local", 20.0, True, False, "", 0),),
    "token_security_free": (
        ("free_security_clickhouse", "local", 20.0, True, False, "", 0),
        ("free_security_goplus", "free_api", 10.0, False, False, "", 0),
        ("free_security_honeypot_is", "free_api", 8.0, False, False, "", 0),
        ("free_security_rugcheck", "free_api", 6.0, False, False, "", 0),
        ("free_security_dexscreener", "free_api", 4.0, False, False, "", 0),
    ),
    "smart_money": (("smart_money_tracker", "local", 10.0, False, False, "", 0),),
    "whale_alerts": (("whale_detector", "local", 10.0, False, False, "", 0),),
    "insider_detection": (("insider_detector", "local", 10.0, False, False, "", 0),),
    "liquidity_risk": (("liquidity_monitor", "local", 10.0, False, False, "", 0),),
    "holder_health": (("holder_analyzer", "local", 10.0, False, False, "", 0),),
    "cross_chain_entity": (("entity_tracer", "local", 10.0, False, False, "", 0),),
    "rug_patterns": (("rug_matcher", "local", 10.0, False, False, "", 0),),
    "dev_reputation": (("dev_reputation_api", "local", 10.0, False, False, "", 0),),
    "token_report": (("report_generator", "local", 15.0, False, False, "", 0),),
    "lite_chain_indexer_patterns": (("lite_chain_indexer_pattern_search", "local", 15.0, True, False, "", 0),),
    "deployer_reputation": (("lite_chain_indexer_deployer_reputation", "local", 20.0, True, False, "", 0),),
    "deployer_fingerprint": (("lite_chain_indexer_deployer_fingerprint", "local", 20.0, True, False, "", 0),),
    "whale_detection": (("lite_chain_indexer_whale_detect", "local", 20.0, True, False, "", 0),),
    "holder_age": (("lite_chain_indexer_holder_age", "local", 20.0, True, False, "", 0),),
    "holder_clusters": (("lite_chain_indexer_holder_clusters", "local", 20.0, True, False, "", 0),),
    "attack_detection": (("lite_chain_indexer_attack_detection", "local", 20.0, True, False, "", 0),),
    "new_token_radar": (("lite_chain_indexer_new_token_radar", "local", 20.0, True, False, "", 0),),
    "tier_comparison": (("tier_compare", "local", 1.0, False, False, "", 0),),
    "threat_check": (
        ("scraped_intel_local", "local", 15.0, True, False, "", 0),
        ("sentinel_scanner", "local", 10.0, False, False, "", 0),
        ("local_rag", "local", 5.0, False, False, "", 0),
    ),
    "contract_scan": (
        ("sentinel_scanner", "local", 10.0, False, False, "", 0),
        ("scraped_intel_local", "local", 8.0, True, False, "", 0),
        ("local_rag", "local", 3.0, False, False, "", 0),
    ),
    "sentinel_deep": (
        ("sentinel_scanner", "local", 10.0, False, False, "", 0),
        ("scraped_intel_local", "local", 8.0, True, False, "", 0),
        ("local_rag", "local", 5.0, False, False, "", 0),
    ),
    "rugmap": (
        ("lite_chain_indexer_holders", "local", 20.0, True, False, "", 0),
        ("dexscreener_holders", "free_api", 8.0, False, False, "", 0),
        ("birdeye_overview_api", "freemium", 5.0, False, True, "BIRDEYE_API_KEY", 50000),
    ),
}
