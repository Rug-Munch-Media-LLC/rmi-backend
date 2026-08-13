"""
RugMunch Unified Wallet Scanner + Trending Engine
==================================================
Re-export shim — all implementation lives in app/unified_scanner/ package.

Import directly from app.unified_scanner for new code.
"""

from __future__ import annotations

from app.unified_scanner import (  # type: ignore[attr-defined]  # self-shadowing: package wins at runtime
    CHAINS,
    LAST_KNOWN_LAUNCHES,
    WalletRiskFactors,
    _cache_get,
    _cache_set,
    _calculate_basic_pnl,
    _check_hapi_risk,
    _check_honeydb_ip,
    _check_misttrack_light,
    _check_trm_sanctions_wallet,
    _close_data_flywheel,
    _fetch_etherscan_wallet,
    _fetch_helius_wallet,
    _fetch_moralis_wallet,
    _fetch_sentinel_labels,
    _fetch_wallet_memory,
    _get_helius_key,
    _helius_call,
    _resolve_entity_free,
    _scan_held_tokens,
    _trace_funding_source,
    get_new_launches,
    get_trending_tokens,
    scan_wallet,
)

__all__ = [
    "CHAINS",
    "LAST_KNOWN_LAUNCHES",
    "WalletRiskFactors",
    "_cache_get",
    "_cache_set",
    "_calculate_basic_pnl",
    "_check_hapi_risk",
    "_check_honeydb_ip",
    "_check_misttrack_light",
    "_check_trm_sanctions_wallet",
    "_close_data_flywheel",
    "_fetch_etherscan_wallet",
    "_fetch_helius_wallet",
    "_fetch_moralis_wallet",
    "_fetch_sentinel_labels",
    "_fetch_wallet_memory",
    "_get_helius_key",
    "_helius_call",
    "_resolve_entity_free",
    "_scan_held_tokens",
    "_trace_funding_source",
    "get_new_launches",
    "get_trending_tokens",
    "scan_wallet",
]
