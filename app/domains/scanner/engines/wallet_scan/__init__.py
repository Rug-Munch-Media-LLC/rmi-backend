"""
Unified Wallet Scanner + Trending Engine
=========================================
Split from unified_scanner.py into focused modules.

Backward-compatible re-exports.
"""

from app.domains.scanner.engines.wallet_scan.cache import _cache_get, _cache_set
from app.domains.scanner.engines.wallet_scan.helius import (
    _fetch_etherscan_wallet,
    _fetch_helius_wallet,
    _fetch_moralis_wallet,
    _get_helius_key,
    _helius_call,
)
from app.domains.scanner.engines.wallet_scan.models import CHAINS, WalletRiskFactors
from app.domains.scanner.engines.wallet_scan.providers import (
    _calculate_basic_pnl,
    _check_hapi_risk,
    _check_honeydb_ip,
    _check_misttrack_light,
    _check_trm_sanctions_wallet,
    _close_data_flywheel,
    _fetch_sentinel_labels,
    _fetch_wallet_memory,
    _resolve_entity_free,
    _scan_held_tokens,
    _trace_funding_source,
)
from app.domains.scanner.engines.wallet_scan.scanner import scan_wallet
from app.domains.scanner.engines.wallet_scan.trending import (
    LAST_KNOWN_LAUNCHES,
    get_new_launches,
    get_trending_tokens,
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
