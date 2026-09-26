#!/usr/bin/env python3
"""Source 2 — Etherscan label CSVs → wallet_labels + scam_addresses.

Primary function: ``ingest_etherscan_labels``. The CSV parsing itself lives in
``etherscan_csv`` so both stay under the per-file cap.
"""

from __future__ import annotations

import os
from typing import Any

from .etherscan_csv import (
    combined_csv_path,
    phish_csv_path,
    read_combined_csv,
    read_phish_csv,
)
from .logging_setup import logger
from .store_labels import insert_wallet_labels
from .store_scams import insert_scam_addresses

__all__ = ["ingest_etherscan_labels"]


def _ingest_phish(ch: Any, redis: Any | None, chain: str, totals: dict[str, int]) -> None:
    """Ingest the phish/hack CSV, if present, accumulating into totals."""
    path = phish_csv_path()
    if not os.path.exists(path):
        return
    labels, scams = read_phish_csv(path, chain)
    logger.info(f"  Read {len(labels)} rows from etherscan_phish_hack.csv")
    if labels:
        totals["wallet_labels"] += insert_wallet_labels(ch, redis, labels)
    if scams:
        totals["scam_addresses"] += insert_scam_addresses(ch, redis, scams)


def _ingest_combined(ch: Any, redis: Any | None, chain: str, totals: dict[str, int]) -> None:
    """Ingest the combined labels CSV, if present, accumulating into totals."""
    path = combined_csv_path()
    if not os.path.exists(path):
        return
    labels, scams = read_combined_csv(path, chain)
    if labels:
        cnt = insert_wallet_labels(ch, redis, labels)
        totals["wallet_labels"] += cnt
        logger.info(f"  → {cnt} wallet labels from etherscan_combined_labels.csv")
    if scams:
        cnt = insert_scam_addresses(ch, redis, scams)
        totals["scam_addresses"] += cnt
        logger.info(f"  → {cnt} scam addresses from etherscan_combined_labels.csv")


async def ingest_etherscan_labels(
    ch: Any,
    redis: Any | None,
    chain: str = "ethereum",
    addresses: list[str] | None = None,
) -> dict[str, int]:
    """Ingest Etherscan label CSVs for the requested chain.

    Both CSVs are optional; a missing file is skipped silently. ``addresses`` is
    accepted for API-compatibility with the per-address flow.
    """
    logger.info("=" * 60)
    logger.info(f"SOURCE: Etherscan Labels ({chain})")
    logger.info("=" * 60)

    totals: dict[str, int] = {"wallet_labels": 0, "scam_addresses": 0}
    _ingest_phish(ch, redis, chain, totals)
    _ingest_combined(ch, redis, chain, totals)
    return totals
