#!/usr/bin/env python3
"""Source 11 — community-maintained label CSVs hosted on GitHub.

Primary function: ``ingest_github_community``.
"""

from __future__ import annotations

import csv
from typing import Any

import httpx

from .logging_setup import logger
from .row_builders import LabelFields, ScamFields, label_row, scam_row, valid_address
from .store_labels import insert_wallet_labels
from .store_scams import insert_scam_addresses

__all__ = ["ingest_github_community"]

_BASE = "https://raw.githubusercontent.com/ImMike/crypto-wallet-address-labels/main/datasets"
COMMUNITY_SOURCES = (
    {"url": f"{_BASE}/ethereum-exchange-and-dex-labels/exchange.csv", "chain": "ethereum", "category": "exchange"},
    {"url": f"{_BASE}/ethereum-exchange-and-dex-labels/dex.csv", "chain": "ethereum", "category": "dex"},
    {"url": f"{_BASE}/ethereum-cluster-exchange-labels/cluster1.csv", "chain": "ethereum", "category": "exchange"},
    {"url": f"{_BASE}/bsc-ethereum-address-tags/bsc_detail.csv", "chain": "bsc", "category": "entity"},
    {
        "url": "https://raw.githubusercontent.com/bitcoin-scammers-list/bitcoin-scammers-list/main/scammers.csv",
        "chain": "bitcoin",
        "category": "scam",
    },
)


def _rows_from_csv(content: str, source: dict[str, str], source_name: str) -> tuple[list, list, int]:
    """Map one community CSV body to label rows, scam rows, and a row count."""
    labels: list[dict[str, Any]] = []
    scams: list[dict[str, Any]] = []
    chain = source["chain"]
    category = source["category"]
    count = 0
    for row in csv.DictReader(content.splitlines()):
        addr = row.get("address", row.get("Address", row.get("addr", row.get("wallet", "")))).strip()
        name = row.get("name", row.get("Name", row.get("label", row.get("Label", "")))).strip() or source_name
        if not valid_address(addr):
            continue
        labels.append(label_row(LabelFields(addr, chain, name, category, 'community', f'github:{source_name}')))
        if category == "scam":
            scams.append(scam_row(ScamFields(addr, chain, f'github:{source_name}', 'scam', 0.8, f'Community list: {name}')))
        count += 1
    return labels, scams, count


async def ingest_github_community(ch: Any, redis: Any | None) -> dict[str, int]:
    """Fetch community-maintained address label CSVs from GitHub."""
    logger.info("=" * 60)
    logger.info("SOURCE: GitHub Community Labels")
    logger.info("=" * 60)

    totals: dict[str, int] = {"wallet_labels": 0, "scam_addresses": 0}
    label_rows: list[dict[str, Any]] = []
    scam_rows: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=30) as client:
        for source in COMMUNITY_SOURCES:
            url = source["url"]
            source_name = url.split("/")[-1].replace(".csv", "")
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.debug(f"  {source_name} returned {resp.status_code}")
                    continue
            except Exception as e:
                logger.debug(f"  {source_name} failed: {e}")
                continue
            labels, scams, count = _rows_from_csv(resp.text, source, source_name)
            label_rows.extend(labels)
            scam_rows.extend(scams)
            logger.info(f"  {source_name}: {count} rows")

    if label_rows:
        totals["wallet_labels"] = insert_wallet_labels(ch, redis, label_rows)
    if scam_rows:
        totals["scam_addresses"] = insert_scam_addresses(ch, redis, scam_rows)
    logger.info(f"  → {len(label_rows)} wallet labels, {len(scam_rows)} scam from GitHub community")
    return totals
