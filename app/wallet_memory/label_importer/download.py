"""Download helpers for wallet label datasets.

THIN ADAPTER. Canonical source: `rugmunch_scanner.wallet_memory.label_importer.download`.
"""

from __future__ import annotations

from rugmunch_scanner.wallet_memory.label_importer.download import (
    _download_file,
    _download_json,
    _ensure_bsc_labels,
    _ensure_contract_name_labels,
    _ensure_eth_scam_labels,
    _ensure_exchange_cluster_labels,
    _ensure_exchange_wallet_labels,
    _ensure_solana_labels,
    _get_http,
)

__all__ = [
    "_download_file",
    "_download_json",
    "_ensure_bsc_labels",
    "_ensure_contract_name_labels",
    "_ensure_eth_scam_labels",
    "_ensure_exchange_cluster_labels",
    "_ensure_exchange_wallet_labels",
    "_ensure_solana_labels",
    "_get_http",
]
