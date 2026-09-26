#!/usr/bin/env python3
"""Source 3 — CryptoScamDB → wallet_labels + scam_addresses.

Primary function: ``ingest_cryptoscamdb``.
"""

from __future__ import annotations

from typing import Any

from .cryptoscamdb_fetch import fetch_cryptoscamdb_entries, fetch_cryptoscamdb_payload
from .cryptoscamdb_rows import rows_from_entries, rows_from_payload
from .logging_setup import logger
from .store_labels import insert_wallet_labels
from .store_scams import insert_scam_addresses

__all__ = ["ingest_cryptoscamdb"]


async def ingest_cryptoscamdb(ch: Any, redis: Any | None) -> dict[str, int]:
    """Fetch scam addresses from CryptoScamDB (GitHub first, then API)."""
    logger.info("=" * 60)
    logger.info("SOURCE: CryptoScamDB")
    logger.info("=" * 60)

    totals: dict[str, int] = {"wallet_labels": 0, "scam_addresses": 0}
    labels, scams = rows_from_entries(await fetch_cryptoscamdb_entries())

    if not labels:
        payload = await fetch_cryptoscamdb_payload()
        if not payload:
            logger.warning("  CryptoScamDB unavailable (API 502 + GitHub fallback)")
            return totals
        logger.info(f"  Found {len(payload)} scam entries")
        labels, scams = rows_from_payload(payload)

    if labels:
        totals["wallet_labels"] = insert_wallet_labels(ch, redis, labels)
    if scams:
        totals["scam_addresses"] = insert_scam_addresses(ch, redis, scams)

    logger.info(f"  → {len(labels)} wallet labels, {len(scams)} scam addresses from CryptoScamDB")
    return totals
