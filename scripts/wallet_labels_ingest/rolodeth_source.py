#!/usr/bin/env python3
"""RolodETH GitHub label database: fetch + payload→row mapping (no ClickHouse).

Split from the source orchestrator so both stay under the file cap.
"""

from __future__ import annotations

from typing import Any

import httpx

from .categories import infer_label_category
from .logging_setup import logger
from .row_builders import LabelFields, label_row, valid_address

__all__ = ["fetch_payload", "rows_from_payload"]

URLS = [
    "https://raw.githubusercontent.com/RoleTiker/rolodETH/main/rolodETH.json",
    "https://raw.githubusercontent.com/exdx/rolodETH/main/data/labels.json",
]
SOURCE = "rolodeth"


def _row(addr: str, label: str, category: str) -> dict[str, Any]:
    """Build one rolodeth wallet_labels row."""
    return label_row(LabelFields(addr, 'ethereum', label, infer_label_category(category), category, SOURCE))


def _rows_from_list(data: list[Any]) -> list[dict[str, Any]]:
    """Rows from a flat [{address,label,...}] payload."""
    rows = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        addr = entry.get("address", "").strip()
        label = entry.get("label", entry.get("name", "")).strip()
        category = entry.get("category", entry.get("type", "wallet")).strip()
        if valid_address(addr) and label:
            rows.append(_row(addr.lower(), label, category))
    return rows


def _plain_row(addr: str, label: str) -> dict[str, Any]:
    """Row for a bare-string label value: category ``wallet``, empty subtype."""
    return {
        "address": addr,
        "chain_id": "ethereum",
        "label_name": label,
        "label_category": "wallet",
        "label_subtype": "",
        "source": SOURCE,
        "is_sanctioned": False,
    }


def _rows_from_mapping_value(addr: str, info: Any) -> list[dict[str, Any]]:
    """Rows for one address whose value is a str, a dict, or a list of dicts."""
    if isinstance(info, str):
        return [_plain_row(addr, info.strip()[:256])] if info.strip() else []
    if isinstance(info, dict):
        label = info.get("label", info.get("name", "")).strip()
        category = info.get("category", info.get("type", "wallet")).strip()
        return [_row(addr, label, category)] if label else []
    if isinstance(info, list):
        rows = []
        for sub in info:
            if not isinstance(sub, dict):
                continue
            label = sub.get("label", sub.get("name", "")).strip()
            category = sub.get("category", sub.get("type", "wallet")).strip()
            if label:
                rows.append(_row(addr, label, category))
        return rows
    return []


def rows_from_payload(data: Any) -> list[dict[str, Any]]:
    """Map a RolodETH payload (list or address→info mapping) to label rows."""
    if isinstance(data, list):
        return _rows_from_list(data)
    if not isinstance(data, dict):
        return []
    rows = []
    for addr, info in data.items():
        addr_norm = addr.strip().lower()
        if not valid_address(addr_norm):
            continue
        rows.extend(_rows_from_mapping_value(addr_norm, info))
    return rows


async def fetch_payload() -> Any:
    """Fetch the first reachable RolodETH JSON payload (None when unavailable)."""
    async with httpx.AsyncClient(timeout=30) as client:
        for url in URLS:
            try:
                resp = await client.get(url)
            except Exception as e:
                logger.debug(f"  RolodETH {url} failed: {e}")
                continue
            if resp.status_code == 200:
                logger.info(f"  Got data from {url.split('/')[-1]}")
                return resp.json()
    return None
