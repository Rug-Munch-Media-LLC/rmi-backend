#!/usr/bin/env python3
"""Source 1 — ingest the built-in known-contract directories.

Primary function: ``ingest_builtin_contracts``.
"""

from __future__ import annotations

from typing import Any

from .builtin_contracts import (
    DEFI_ADMIN_ADDRESSES,
    GNOSIS_SAFE_ADDRESSES,
    INCH_ROUTERS,
    TORNADO_CASH_ADDRESSES,
    UNISWAP_ROUTERS,
)
from .logging_setup import logger
from .store_labels import insert_wallet_labels
from .store_scams import insert_scam_addresses

__all__ = ["ingest_builtin_contracts"]

BUILTIN_DIRECTORIES = (
    ("tornado_cash", TORNADO_CASH_ADDRESSES),
    ("uniswap_routers", UNISWAP_ROUTERS),
    ("1inch_routers", INCH_ROUTERS),
    ("defi_admin", DEFI_ADMIN_ADDRESSES),
    ("gnosis_safe", GNOSIS_SAFE_ADDRESSES),
)


def _collect_entries(chain_filter: str) -> dict[str, list[dict[str, str]]]:
    """Merge every built-in directory, filtered by chain, into per-chain entries."""
    merged: dict[str, list[dict[str, str]]] = {}
    for name, directories in BUILTIN_DIRECTORIES:
        for chain, entries in directories.items():
            if chain_filter and chain != chain_filter:
                continue
            for entry in entries:
                merged.setdefault(chain, []).append({**entry, "source_name": name})
    return merged


def _rows_for_entry(chain: str, entry: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Build the wallet_labels row (and scam row for mixers) for one entry."""
    source = f"builtin:{entry['source_name']}"
    label = {
        "address": entry["address"],
        "chain_id": chain,
        "label_name": entry["name"],
        "label_category": entry["category"],
        "label_subtype": "builtin",
        "source": source,
        "is_sanctioned": False,
    }
    scam = None
    if entry["category"] == "mixer":
        scam = {
            "address": entry["address"],
            "chain_id": chain,
            "source": source,
            "threat_type": "mixer",
            "confidence": 1.0,
            "evidence": f"Known mixer: {entry['name']}",
        }
    return label, scam


async def ingest_builtin_contracts(ch: Any, redis: Any | None, chain_filter: str = "") -> dict[str, int]:
    """Ingest known contract addresses from built-in directories.

    Sources: Tornado Cash, Uniswap, 1inch, DeFi admin, Gnosis Safe.
    """
    logger.info("=" * 60)
    logger.info("SOURCE: Built-in Contract Directories")
    logger.info("=" * 60)

    label_rows: list[dict[str, Any]] = []
    scam_rows: list[dict[str, Any]] = []
    for chain, entries in _collect_entries(chain_filter).items():
        for entry in entries:
            label, scam = _rows_for_entry(chain, entry)
            label_rows.append(label)
            if scam is not None:
                scam_rows.append(scam)

    totals = {"wallet_labels": 0, "scam_addresses": 0}
    if label_rows:
        totals["wallet_labels"] = insert_wallet_labels(ch, redis, label_rows)
        logger.info(f"  → {totals['wallet_labels']} wallet labels from built-in directories")
    if scam_rows:
        totals["scam_addresses"] = insert_scam_addresses(ch, redis, scam_rows)
        logger.info(f"  → {totals['scam_addresses']} scam addresses (mixers) from built-in directories")
    return totals
