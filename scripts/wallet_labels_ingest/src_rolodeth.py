#!/usr/bin/env python3
"""Source 5 — RolodETH public GitHub label database.

Primary function: ``ingest_rolodeth``.
"""

from __future__ import annotations

from typing import Any

from .logging_setup import logger
from .rolodeth_source import fetch_payload, rows_from_payload
from .store_labels import insert_wallet_labels

__all__ = ["ingest_rolodeth"]


async def ingest_rolodeth(ch: Any, redis: Any | None) -> dict[str, int]:
    """Fetch address labels from the RolodETH GitHub repository."""
    logger.info("=" * 60)
    logger.info("SOURCE: RolodETH")
    logger.info("=" * 60)

    totals: dict[str, int] = {"wallet_labels": 0, "scam_addresses": 0}
    payload = await fetch_payload()
    if not payload:
        logger.warning("  RolodETH unavailable")
        return totals

    label_rows = rows_from_payload(payload)
    if label_rows:
        totals["wallet_labels"] = insert_wallet_labels(ch, redis, label_rows)

    logger.info(f"  → {len(label_rows)} wallet labels from RolodETH")
    return totals
