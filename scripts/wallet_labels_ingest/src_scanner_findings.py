#!/usr/bin/env python3
"""Source 10 — sync internal scanner findings into wallet_labels.

Scanner auto-labeling writes scam_addresses; this closes the loop by mirroring
those rows as wallet_labels so lookups see them.
"""

from __future__ import annotations

from typing import Any

from .ch_client import ch_select
from .logging_setup import logger
from .store_labels import insert_wallet_labels

__all__ = ["ingest_scanner_findings"]

FINDINGS_QUERY = """SELECT address, chain_id, source, threat_type, confidence, evidence
   FROM scam_addresses
   WHERE threat_type IN ('scam', 'malicious', 'exploiter', 'phishing',
                         'money_laundering', 'ponzi', 'high_risk_deployer',
                         'malicious_deployer', 'rug', 'hack')
   ORDER BY first_seen_at DESC
   LIMIT 10000"""


def _rows_from_findings(rows: list[Any]) -> list[dict[str, Any]]:
    """Map scam_addresses rows to wallet_labels rows, deduped per address+chain."""
    label_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        addr, chain, source, threat = row[0], row[1], row[2], row[3]
        dedup_key = f"{addr}:{chain}:scanner_findings"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        label_rows.append(
            {
                "address": addr,
                "chain_id": chain,
                "label_name": f"{source}: {threat}",
                "label_category": "scam",
                "label_subtype": threat,
                "source": f"scanner:{source}",
                "is_sanctioned": False,
            }
        )
    return label_rows


async def ingest_scanner_findings(ch: Any, redis: Any | None) -> dict[str, int]:
    """Ensure every flagged scam_addresses row also has a wallet_labels entry."""
    logger.info("=" * 60)
    logger.info("SOURCE: Internal Scanner Findings")
    logger.info("=" * 60)

    totals: dict[str, int] = {"wallet_labels": 0}
    if not ch:
        logger.warning("  ClickHouse unavailable - cannot read scanner findings")
        return totals

    try:
        rows = ch_select(ch, FINDINGS_QUERY)
    except Exception as e:
        logger.warning(f"  Failed to query scam_addresses: {e}")
        return totals

    if not rows:
        logger.info("  No scanner findings to sync")
        return totals

    logger.info(f"  Found {len(rows)} scanner findings to sync")
    label_rows = _rows_from_findings(rows)
    if label_rows:
        totals["wallet_labels"] = insert_wallet_labels(ch, redis, label_rows)
    logger.info(f"  → {len(label_rows)} wallet labels synced from scanner findings")
    return totals
