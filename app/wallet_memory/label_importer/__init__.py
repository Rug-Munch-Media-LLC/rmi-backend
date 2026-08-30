"""
Static Label Importer — Split into focused modules.
THIN ADAPTER. Canonical source: `rugmunch_scanner.wallet_memory.label_importer`.
The implementation was superseded by the private rugmunch-scanner repo.
Re-exports all public functions for backward compatibility.
"""

from __future__ import annotations

# Re-export the canonical label importer surface from rugmunch-scanner.
# Explicit (no *) to satisfy ruff; the full supported surface is re-exported.
from rugmunch_scanner.wallet_memory.label_importer import (
    DATA_DIR,
    ETH_CSV_SPECS,
    ETHERSCAN_COMBINED,
    ETHERSCAN_PHISH_HACK,
    LABELS_DIR,
    SOLANA_CSV_SPECS,
    SOLANA_LABELS_REPO,
    _download_file,
    _download_json,
    _ensure_bsc_labels,
    _ensure_contract_name_labels,
    _ensure_eth_scam_labels,
    _ensure_exchange_cluster_labels,
    _ensure_exchange_wallet_labels,
    _ensure_solana_labels,
    _ensure_wallet_labels_table,
    _flush_batch,
    _get_http,
    _load_bsc_detail,
    _load_contract_names,
    _load_csv_labels,
    _load_etherscan_combined,
    _load_etherscan_phish_hack,
    _load_exchange_clusters,
    _load_exchange_wallets,
    _load_ofac_sanctions,
    _normalize_chain,
    get_last_load_counts,
    is_loading,
    load_all_static_labels,
    load_clean_labels,
)

__all__ = [
    "DATA_DIR",
    "ETHERSCAN_COMBINED",
    "ETHERSCAN_PHISH_HACK",
    "ETH_CSV_SPECS",
    "LABELS_DIR",
    "SOLANA_CSV_SPECS",
    "SOLANA_LABELS_REPO",
    "_download_file",
    "_download_json",
    "_ensure_bsc_labels",
    "_ensure_contract_name_labels",
    "_ensure_eth_scam_labels",
    "_ensure_exchange_cluster_labels",
    "_ensure_exchange_wallet_labels",
    "_ensure_solana_labels",
    "_ensure_wallet_labels_table",
    "_flush_batch",
    "_get_http",
    "_load_bsc_detail",
    "_load_contract_names",
    "_load_csv_labels",
    "_load_etherscan_combined",
    "_load_etherscan_phish_hack",
    "_load_exchange_clusters",
    "_load_exchange_wallets",
    "_load_ofac_sanctions",
    "_normalize_chain",
    "get_last_load_counts",
    "is_loading",
    "load_all_static_labels",
    "load_clean_labels",
]
