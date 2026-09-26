#!/usr/bin/env python3
"""Shared per-address label enrichment (walletLabels.xyz + bluepages.fyi).

Both sources are the same shape: a known-address list, one GET per address, a
label/category per response item, dedup on ``address:label``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .builtin_contracts import (
    TORNADO_CASH_ADDRESSES,
    UNISWAP_ROUTERS,
)
from .categories import infer_label_category
from .ch_client import ch_select
from .row_builders import LabelFields, label_row, valid_address

__all__ = ["addresses_for_chain", "ch_addresses", "fallback_addresses", "fetch_per_address_labels"]

REQUEST_DELAY_SECONDS = 0.05


def addresses_for_chain(chain: str, directories: tuple[Any, ...] = ()) -> list[str]:
    """Collect deduplicated built-in addresses for one chain."""
    collected: list[str] = []
    for directories_ in directories:
        for entries in directories_.get(chain, []):
            collected.append(entries["address"])
    return list(dict.fromkeys(collected))


def fallback_addresses(chain: str) -> list[str]:
    """Well-known addresses used when ClickHouse has nothing to enrich."""
    return addresses_for_chain(chain, (TORNADO_CASH_ADDRESSES, UNISWAP_ROUTERS))


def ch_addresses(ch: Any, chain: str) -> list[str]:
    """Top known addresses from wallet_labels (best effort; [] on failure)."""
    try:
        rows = ch_select(
            ch,
            "SELECT DISTINCT address FROM wallet_labels WHERE chain_id = {chain:String} LIMIT 200",
            {"chain": chain},
        )
        return [r[0] for r in rows[:50]]
    except Exception:
        return []


def _item_rows(addr: str, chain: str, source: str, items: Any) -> list[dict[str, Any]]:
    """Map one address's response items to label rows."""
    if isinstance(items, dict):
        items = [items]
    rows = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        label = item.get("label", item.get("name", "")).strip()
        category = item.get("category", item.get("type", "")).strip()
        if label:
            rows.append(label_row(LabelFields(addr, chain, label, infer_label_category(category), category, source)))
    return rows


async def _fetch_one(
    client: httpx.AsyncClient, url_template: str, addr: str, chain: str, source: str
) -> list[dict[str, Any]]:
    """One GET for one address; [] on any failure (rate limits, 404s)."""
    try:
        resp = await client.get(url_template.format(addr=addr), timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception:
        return []
    items = data.get("labels", data.get("data", [])) if isinstance(data, dict) else data
    return _item_rows(addr.lower(), chain, source, items)


async def fetch_per_address_labels(
    url_template: str,
    addresses: list[str],
    chain: str,
    source: str,
) -> list[dict[str, Any]]:
    """Query one label API per address, rate-limited, deduped on address:label."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    async with httpx.AsyncClient(timeout=15) as client:
        for addr in addresses:
            for row in await _fetch_one(client, url_template, addr, chain, source):
                key = f"{row['address']}:{row['label_name']}"
                if key in seen:
                    continue
                seen.add(key)
                if valid_address(row["address"]):
                    rows.append(row)
            await asyncio.sleep(REQUEST_DELAY_SECONDS)
    return rows
