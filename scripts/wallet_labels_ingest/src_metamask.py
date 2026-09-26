#!/usr/bin/env python3
"""Source 8 — MetaMask eth-phishing-detect: pull addresses out of the blacklist config.

Primary function: ``ingest_metamask_phishing``.
"""

from __future__ import annotations

from typing import Any

import httpx

from .logging_setup import logger
from .store_labels import insert_wallet_labels
from .store_scams import insert_scam_addresses

__all__ = ["ingest_metamask_phishing"]

URL = "https://raw.githubusercontent.com/MetaMask/eth-phishing-detect/master/src/config.json"


def _extract_addresses(obj: Any, found: set[str]) -> None:
    """Recursively collect 0x-prefixed 42-char addresses from the config."""
    if isinstance(obj, str):
        if obj.startswith("0x") and len(obj) == 42:
            found.add(obj.lower())
    elif isinstance(obj, dict):
        for val in obj.values():
            _extract_addresses(val, found)
    elif isinstance(obj, list):
        for item in obj:
            _extract_addresses(item, found)


async def _fetch_config() -> Any:
    """Fetch the MetaMask config; None on any failure or non-200."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(URL)
            if resp.status_code != 200:
                logger.warning(f"  MetaMask list returned {resp.status_code}")
                return None
            return resp.json()
    except Exception as e:
        logger.warning(f"  MetaMask fetch failed: {e}")
        return None


async def ingest_metamask_phishing(ch: Any, redis: Any | None) -> dict[str, int]:
    """Ingest address-level phishing data from MetaMask phishing-detect.

    The list is primarily domain patterns; any addresses embedded in the config
    are extracted and treated as confirmed phishing.
    """
    logger.info("=" * 60)
    logger.info("SOURCE: MetaMask Phishing Detect")
    logger.info("=" * 60)

    totals: dict[str, int] = {"wallet_labels": 0, "scam_addresses": 0}
    data = await _fetch_config()
    if data is None:
        return totals

    addresses: set[str] = set()
    _extract_addresses(data, addresses)
    if not addresses:
        logger.info("  No address patterns found in MetaMask config (domain-only list)")
        return totals

    label_rows = [
        {
            "address": addr,
            "chain_id": "ethereum",
            "label_name": "MetaMask Phishing",
            "label_category": "phishing",
            "label_subtype": "metamask_phishing_detect",
            "source": "metamask_phishing_detect",
            "is_sanctioned": False,
        }
        for addr in addresses
    ]
    scam_rows = [
        {
            "address": addr,
            "chain_id": "ethereum",
            "source": "metamask_phishing_detect",
            "threat_type": "phishing",
            "confidence": 0.95,
            "evidence": "MetaMask eth-phishing-detect confirmed phishing address",
        }
        for addr in addresses
    ]

    totals["wallet_labels"] = insert_wallet_labels(ch, redis, label_rows)
    totals["scam_addresses"] = insert_scam_addresses(ch, redis, scam_rows)
    logger.info(f"  → {len(label_rows)} labels, {len(scam_rows)} scam from MetaMask phishing detect")
    return totals
