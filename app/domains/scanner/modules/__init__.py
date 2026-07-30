"""Scanner modules package — explicit re-exports."""

# Chain pattern detector (on-chain behavioral pattern analysis → RAG)
# Core helpers (no underscore-prefixed scan functions)
from app.domains.scanner.modules._core import (
    BLOCKSCOUT_URLS,
    DEFILLAMA_API,
    EXPLORER_APIS,
    GOPLUS_CHAIN_IDS,
    PREFER_OWN_DATA,
    _fetch_dexscreener,
    _fetch_json,
    _fetch_with_fallback,
    _solana_rpc_call,
)
from app.domains.scanner.modules.chain_pattern_detector import (
    analyze_and_ingest,
    detect_all_patterns,
    ingest_patterns_into_rag,
)

# Deployer checks
from app.domains.scanner.modules.deployer import (
    _get_deployer_info,
)

# Holder analysis
from app.domains.scanner.modules.holders import (
    _check_liquidity_lock_multi,
    _check_lp_concentration,
    _get_holder_data,
)

# Market data
from app.domains.scanner.modules.market import (
    _check_blockscout,
    _check_coingecko_price,
    _check_defi_scanner,
    _check_defillama_context,
    _check_token_sniffer,
    _check_volume_anomaly,
    _check_volume_liquidity_ratio,
    _get_price_consensus,
)

# Security checks
from app.domains.scanner.modules.security import (
    _check_goplus_security,
    _check_honeypot_is,
    _check_spam,
)

# Social / bundle checks
from app.domains.scanner.modules.social import (
    _check_bundle,
    _check_contract_verification,
    _check_copycat,
    _check_ownership_renounced,
    _check_social_sentiment,
    _check_token_age,
    _get_top_symbols,
    _levenshtein_distance,
)

__all__ = [
    "BLOCKSCOUT_URLS",
    "DEFILLAMA_API",
    "EXPLORER_APIS",
    "GOPLUS_CHAIN_IDS",
    "PREFER_OWN_DATA",
    "_check_blockscout",
    "_check_bundle",
    "_check_coingecko_price",
    "_check_contract_verification",
    "_check_copycat",
    "_check_defi_scanner",
    "_check_defillama_context",
    "_check_goplus_security",
    "_check_honeypot_is",
    "_check_liquidity_lock_multi",
    "_check_lp_concentration",
    "_check_ownership_renounced",
    "_check_social_sentiment",
    "_check_spam",
    "_check_token_age",
    "_check_token_sniffer",
    "_check_volume_anomaly",
    "_check_volume_liquidity_ratio",
    "_fetch_dexscreener",
    "_fetch_json",
    "_fetch_with_fallback",
    "_get_deployer_info",
    "_get_holder_data",
    "_get_price_consensus",
    "_get_top_symbols",
    "_levenshtein_distance",
    "_solana_rpc_call",
    "analyze_and_ingest",
    "detect_all_patterns",
    "ingest_patterns_into_rag",
]
