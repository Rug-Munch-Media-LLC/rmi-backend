"""Frozen chain ORDER golden for the security provider-chain registry.

Deliberately PATCH-FREE: no mock-patching fixture and no module injection. Every
provider fetch function is a synthetic marker callable supplied through the
builder's own ``locals_dict`` argument, so the builders never import a real
provider and nothing is stubbed.

What lives here is the *shape* the facade must keep: the frozen chain
registration order, the order the builder assigns keys in, which chains read
their providers off the registry frame, and the marker locals stand-in. The
per-provider attribute golden lives in ``security_provider_golden``.

Captured from the pre-split 631-line ``security.py`` and frozen. Read by
``test_registry_security_chains.py`` (order) and
``test_registry_security_modules.py`` (per-module ownership + collisions).
"""

from __future__ import annotations

from typing import Any

MARKER = "SECURITY_MARKER"

# Every locals() name build_security_chains reads off the registry frame.
REQUIRED_LOCAL_KEYS = (
    "_birdeye_overview",
    "_dexscreener_holders",
    "_free_security_clickhouse",
    "_free_security_dexscreener",
    "_free_security_goplus",
    "_free_security_honeypot_is",
    "_free_security_rugcheck",
    "_lite_chain_indexer_attack_detection",
    "_lite_chain_indexer_deployer_fingerprint",
    "_lite_chain_indexer_deployer_history",
    "_lite_chain_indexer_deployer_reputation",
    "_lite_chain_indexer_holder_age",
    "_lite_chain_indexer_holder_clusters",
    "_lite_chain_indexer_holders",
    "_lite_chain_indexer_new_token_radar",
    "_lite_chain_indexer_pattern_search",
    "_lite_chain_indexer_volume_authenticity",
    "_lite_chain_indexer_whale_detect",
    "_passthrough_rag",
    "_passthrough_scanner",
    "_scraped_intel_provider",
    "_spl_metadata_decoder_provider",
    "_virustotal_url_scan",
)

# Frozen registration order of the pre-split build_security_chains.
#
# NOTE the pre-existing quirk this test now pins: ``volume_authenticity`` is
# registered TWICE — once by the optional app.databus.volume_authenticity
# import and once by the LOCAL ClickHouse chain-indexer provider. The second
# registration wins, so the optional one is dead weight. That behaviour is
# preserved verbatim by the split; deduplicating it is a separate change.
GOLDEN_CHAIN_ORDER = (
    "url_security_scan",
    "scanner",
    "scam_database",
    "spl_token_metadata",
    "volume_authenticity",
    "token_security",
    "token_launches",
    "token_security_free",
    "smart_money",
    "whale_alerts",
    "insider_detection",
    "liquidity_risk",
    "holder_health",
    "cross_chain_entity",
    "rug_patterns",
    "dev_reputation",
    "token_report",
    "lite_chain_indexer_patterns",
    "deployer_reputation",
    "deployer_fingerprint",
    "whale_detection",
    "holder_age",
    "holder_clusters",
    "attack_detection",
    "new_token_radar",
    "tier_comparison",
    "threat_check",
    "contract_scan",
    "sentinel_deep",
    "rugmap",
)

# Every chain name the builder ever ASSIGNS, including the shadowed optional
# ``volume_authenticity``. Dict insertion order — so the duplicate shows up.
GOLDEN_ASSIGNED_ORDER = (
    *GOLDEN_CHAIN_ORDER[:4],
    "volume_authenticity",
    "token_security",
    "token_launches",
    "token_security_free",
    *GOLDEN_CHAIN_ORDER[8:19],
    "volume_authenticity",
    *GOLDEN_CHAIN_ORDER[20:],
)

# Chains whose fetch functions come from the registry frame rather than an
# optional databus import — these are the only ones a fake locals_dict can pin.
LOCAL_FRAME_CHAINS = frozenset(
    {
        "url_security_scan",
        "scanner",
        "scam_database",
        "spl_token_metadata",
        "token_launches",
        "token_security_free",
        "lite_chain_indexer_patterns",
        "deployer_reputation",
        "deployer_fingerprint",
        "whale_detection",
        "holder_age",
        "holder_clusters",
        "attack_detection",
        "new_token_radar",
        "threat_check",
        "contract_scan",
        "sentinel_deep",
        "rugmap",
    }
)


def _locals_dict() -> dict[str, Any]:
    """Registry frame stand-in: every key maps to a marker callable."""

    def _make(key: str):
        def _fetch(*_args: Any, **_kwargs: Any) -> str:
            return f"{MARKER}:{key}"

        _fetch.__name__ = key.lstrip("_")
        return _fetch

    return {key: _make(key) for key in REQUIRED_LOCAL_KEYS}
