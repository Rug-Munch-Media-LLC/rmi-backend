"""
Static Label Importer — Split into focused modules.
Replaces the sync Redis-only wallet_label_loader.py with an async module.
Re-exports all public functions for backward compatibility.
"""

from __future__ import annotations

import json
import time

from app.core.logging import get_logger

logger = get_logger("wallet_memory.label_importer")

# Re-export config
from .bsc import _load_bsc_detail  # noqa: E402 — imported after logger init; kept below module header for readability
from .clean import load_clean_labels  # noqa: E402 — imported after logger init; kept below module header for readability
from .config import (  # noqa: E402 — imported after logger init; kept below module header for readability
    DATA_DIR,
    ETH_CSV_SPECS,
    ETHERSCAN_COMBINED,
    ETHERSCAN_PHISH_HACK,
    LABELS_DIR,
    SOLANA_CSV_SPECS,
    SOLANA_LABELS_REPO,
)

# Re-export download helpers
from .download import (  # noqa: E402 — module docstring precedes import block
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

# Re-export source-specific loaders
from .ethereum import _load_etherscan_combined, _load_etherscan_phish_hack  # noqa: E402 — module docstring precedes import block
from .exchange import _load_contract_names, _load_exchange_clusters, _load_exchange_wallets  # noqa: E402 — module docstring precedes import block

# Re-export shared loaders
from .loaders import (  # noqa: E402 — module docstring precedes import block
    _ensure_wallet_labels_table,
    _flush_batch,
    _load_csv_labels,
    _normalize_chain,
)
from .ofac import _load_ofac_sanctions  # noqa: E402 — module docstring precedes import block

# ── Module state ──

_loading = False
_last_load_counts: dict[str, int] = {}


def get_last_load_counts() -> dict[str, int]:
    """Return counts from the most recent label import."""
    return _last_load_counts


def is_loading() -> bool:
    """Check if a label import is currently in progress."""
    return _loading


# ── Main entry point ──


async def load_all_static_labels() -> dict[str, int]:
    """
    Load all static wallet label datasets on boot.

    Sources:
      1. Solana CEX labels (crypto-wallet-address-labels GitHub)
      2. Solana DApp labels
      3. Solana DeFi labels
      4. Etherscan combined labels (50K rows)
      5. Etherscan phishing/hack labels (111 rows)
      6. OFAC crypto sanctions
      7. BSC address tags (15K rows)
      8. Exchange cluster labels (~200)
      9. Contract name labels (~900)
     10. Exchange/DEX/ICO/Mining wallet labels (~350)

    Returns counts per source.
    """
    global _loading, _last_load_counts

    if _loading:
        logger.info("Label import already in progress - skipping")
        return _last_load_counts

    _loading = True
    start = time.time()
    counts: dict[str, int] = {}

    try:
        from .storage import get_storage

        storage = get_storage()

        # Ensure connections
        await storage._ensure_connections()
        logger.info(
            f"Storage connections: redis={'ok' if storage._redis else 'no'}, clickhouse={'ok' if storage._ch else 'no'}"
        )

        # Ensure ClickHouse table exists
        await _ensure_wallet_labels_table(storage)

        # 1. Download Solana label CSVs if needed
        await _ensure_solana_labels()

        # 2. Load Solana CSVs
        for filename, chain, addr_col, label_cols in SOLANA_CSV_SPECS:
            path = os.path.join(LABELS_DIR, filename)
            key = filename.replace(".csv", "").replace("_labels", "")
            if os.path.exists(path):
                c = await _load_csv_labels(storage, path, chain, addr_col, label_cols, source_name=key)
                counts[key] = c
            else:
                counts[key] = 0
                logger.warning(f"Solana labels missing: {filename}")

        # 2b. Load additional ETH phishing/scam CSVs
        for filename, chain, addr_col, label_cols in ETH_CSV_SPECS:
            path = os.path.join(LABELS_DIR, filename)
            key = filename.replace(".csv", "").replace("_labels", "")
            if os.path.exists(path):
                c = await _load_csv_labels(storage, path, chain, addr_col, label_cols, source_name=key)
                counts[key] = c
            else:
                counts[key] = 0

        # 3. Load Etherscan combined labels
        counts["etherscan_combined"] = await _load_etherscan_combined(storage)

        # 4. Load Etherscan phishing/hack labels
        counts["etherscan_phish_hack"] = await _load_etherscan_phish_hack(storage)

        # 5. Load OFAC sanctions
        counts["ofac"] = await _load_ofac_sanctions(storage)

        # 6. Download and load BSC address tags
        await _ensure_bsc_labels()
        counts["bsc_detail"] = await _load_bsc_detail(storage)

        # 7. Download and load exchange cluster labels
        await _ensure_exchange_cluster_labels()
        counts["exchange_cluster"] = await _load_exchange_clusters(storage)

        # 8. Download and load contract name labels
        await _ensure_contract_name_labels()
        counts["contract_names"] = await _load_contract_names(storage)

        # 9. Download and load exchange/DEX wallet labels
        await _ensure_exchange_wallet_labels()
        counts["exchange_wallets"] = await _load_exchange_wallets(storage)

        # Store aggregate counts in Redis
        total = sum(counts.values())
        counts["total"] = total
        await storage.redis_set("rmi:labels:counts", json.dumps(counts))

        elapsed = time.time() - start
        logger.info(f"Static label import complete: {total} labels in {elapsed:.1f}s - {counts}")

    except Exception as e:
        logger.error(f"Static label import failed: {e}", exc_info=True)
        counts["error"] = str(e)
    finally:
        _loading = False

    _last_load_counts = counts
    return counts


import os  # noqa: E402 — module docstring precedes import block

__all__ = [
    "DATA_DIR",
    "ETH_CSV_SPECS",
    "ETHERSCAN_COMBINED",
    "ETHERSCAN_PHISH_HACK",
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
