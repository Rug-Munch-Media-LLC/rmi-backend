#!/usr/bin/env python3
"""Source 4 — ChainAbuse community scam reports.

Primary function: ``ingest_chainabuse``; the HTTP fan-out lives in
``chainabuse_fetch`` to keep both files small.
"""

from __future__ import annotations

from typing import Any

from .chainabuse_fetch import fetch_reports
from .logging_setup import logger
from .row_builders import LabelFields, ScamFields, label_row, scam_row, valid_address
from .store_labels import insert_wallet_labels
from .store_scams import insert_scam_addresses

__all__ = ["ingest_chainabuse"]

KNOWN_CHAINS = ("ethereum", "solana", "bsc", "bitcoin", "polygon", "arbitrum", "base")


def _rows_from_reports(reports: list[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Map raw ChainAbuse reports to label + scam rows."""
    labels: list[dict[str, Any]] = []
    scams: list[dict[str, Any]] = []
    for report in reports:
        if not isinstance(report, dict):
            continue
        address = report.get("address", report.get("targetAddress", "")).strip()
        category = report.get("category", report.get("type", "scam"))
        description = report.get("description", report.get("report", ""))
        chain = report.get("chain", report.get("network", "ethereum")).lower()
        if not valid_address(address):
            continue
        chain_id = chain if chain in KNOWN_CHAINS else "ethereum"
        evidence = f"ChainAbuse: {description[:200]}" if description else f"ChainAbuse category: {category}"
        labels.append(label_row(LabelFields(address, chain_id, f'ChainAbuse: {category}', 'scam', category, 'chainabuse')))
        scams.append(scam_row(ScamFields(address, chain_id, 'chainabuse', category, 0.85, evidence)))
    return labels, scams


async def ingest_chainabuse(ch: Any, redis: Any | None) -> dict[str, int]:
    """Fetch scam reports from the ChainAbuse API."""
    logger.info("=" * 60)
    logger.info("SOURCE: ChainAbuse")
    logger.info("=" * 60)

    totals: dict[str, int] = {"wallet_labels": 0, "scam_addresses": 0}
    reports = await fetch_reports()
    if not reports:
        logger.warning("  No ChainAbuse reports accessible")
        return totals

    logger.info(f"  Processing {len(reports)} reports")
    labels, scams = _rows_from_reports(reports)
    if labels:
        totals["wallet_labels"] = insert_wallet_labels(ch, redis, labels)
    if scams:
        totals["scam_addresses"] = insert_scam_addresses(ch, redis, scams)

    logger.info(f"  → {len(labels)} wallet labels, {len(scams)} scam addresses from ChainAbuse")
    return totals
