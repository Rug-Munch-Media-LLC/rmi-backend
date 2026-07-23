"""
Unified Wallet Scanner + Trending Engine
=========================================
Split from unified_scanner.py into focused modules.

Backward-compatible re-exports.
"""

from app.unified_scanner.models import CHAINS, WalletRiskFactors
from app.unified_scanner.cache import _cache_get, _cache_set
from app.unified_scanner.helius import (
    _get_helius_key,
    _helius_call,
    _fetch_helius_wallet,
    _fetch_moralis_wallet,
    _fetch_etherscan_wallet,
)
from app.unified_scanner.providers import (
    _fetch_sentinel_labels,
    _fetch_wallet_memory,
    _scan_held_tokens,
    _check_hapi_risk,
    _check_misttrack_light,
    _check_honeydb_ip,
    _check_trm_sanctions_wallet,
    _trace_funding_source,
    _resolve_entity_free,
    _calculate_basic_pnl,
    _close_data_flywheel,
)
from app.unified_scanner.scanner import scan_wallet
from app.unified_scanner.trending import (
    get_trending_tokens,
    get_new_launches,
    LAST_KNOWN_LAUNCHES,
)

__all__ = [
    "CHAINS",
    "WalletRiskFactors",
    "_cache_get",
    "_cache_set",
    "_get_helius_key",
    "_helius_call",
    "_fetch_helius_wallet",
    "_fetch_moralis_wallet",
    "_fetch_etherscan_wallet",
    "_fetch_sentinel_labels",
    "_fetch_wallet_memory",
    "_scan_held_tokens",
    "_check_hapi_risk",
    "_check_misttrack_light",
    "_check_honeydb_ip",
    "_check_trm_sanctions_wallet",
    "_trace_funding_source",
    "_resolve_entity_free",
    "_calculate_basic_pnl",
    "_close_data_flywheel",
    "scan_wallet",
    "get_trending_tokens",
    "get_new_launches",
    "LAST_KNOWN_LAUNCHES",
]
