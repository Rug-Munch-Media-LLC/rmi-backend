#!/usr/bin/env python3
"""Source 9 — Open Labels Initiative public endpoints.

Primary function: ``ingest_open_labels``.
"""

from __future__ import annotations

from typing import Any

import httpx

from .categories import infer_label_category
from .logging_setup import logger
from .row_builders import LabelFields, label_row, valid_address
from .store_labels import insert_wallet_labels

__all__ = ["ingest_open_labels"]

URLS = [
    "https://raw.githubusercontent.com/openlabelsinitiative/OLI/main/data/labels.json",
    "https://openlabels.xyz/api/v1/labels",
]
SOURCE = "open_labels_initiative"


async def _fetch_payload() -> Any:
    """First reachable OLI payload, or None (endpoint is expected to be flaky)."""
    async with httpx.AsyncClient(timeout=15) as client:
        for url in URLS:
            try:
                resp = await client.get(url)
            except Exception as exc:
                logger.debug(f"Open Labels endpoint failed: {exc}")
                continue
            if resp.status_code == 200:
                logger.info(f"  Got data from {url}")
                return resp.json()
    return None


def _rows_from_payload(data: Any) -> list[dict[str, Any]]:
    """Map an OLI list payload to label rows."""
    if not isinstance(data, list):
        return []
    rows = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        addr = entry.get("address", entry.get("id", "")).strip()
        label = entry.get("label", entry.get("name", "")).strip()
        chain = entry.get("chain", "ethereum").strip().lower()
        category = entry.get("category", "wallet").strip()
        if valid_address(addr) and label:
            rows.append(label_row(LabelFields(addr, chain, label, infer_label_category(category), category, SOURCE)))
    return rows


async def ingest_open_labels(ch: Any, redis: Any | None) -> dict[str, int]:
    """Fetch community labels from the Open Labels Initiative endpoints."""
    logger.info("=" * 60)
    logger.info("SOURCE: Open Labels Initiative")
    logger.info("=" * 60)

    totals: dict[str, int] = {"wallet_labels": 0, "scam_addresses": 0}
    payload = await _fetch_payload()
    if not payload:
        logger.info("  OLI endpoint unavailable (expected - still in development)")
        return totals

    rows = _rows_from_payload(payload)
    if rows:
        totals["wallet_labels"] = insert_wallet_labels(ch, redis, rows)
    logger.info(f"  → {len(rows)} wallet labels from OLI")
    return totals
