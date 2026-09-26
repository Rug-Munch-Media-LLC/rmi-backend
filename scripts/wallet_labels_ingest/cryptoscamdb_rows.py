#!/usr/bin/env python3
"""CryptoScamDB payload → label/scam row mapping (pure, no I/O)."""

from __future__ import annotations

from typing import Any

from .row_builders import LabelFields, ScamFields, label_row, scam_row, valid_address

__all__ = ["rows_from_entries", "rows_from_payload"]

CHAIN_ALIAS = {"eth": "ethereum", "btc": "bitcoin", "bnb": "bsc"}


def _chain_from_category(category: str) -> str:
    """Infer the chain from a scam category string (default ethereum)."""
    cat = category.lower()
    if "bitcoin" in cat or "btc" in cat:
        return "bitcoin"
    if "solana" in cat or "sol" in cat:
        return "solana"
    if "bsc" in cat or "bnb" in cat:
        return "bsc"
    return "ethereum"


def rows_from_entries(entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Map GitHub urls.yaml entries (name/category/addresses-by-chain) to rows."""
    labels: list[dict[str, Any]] = []
    scams: list[dict[str, Any]] = []
    for entry in entries:
        name = entry.get("name", "")
        category = entry.get("category", "scam")
        addresses = entry.get("addresses", {})
        if not isinstance(addresses, dict):
            continue
        for chain, chain_addrs in addresses.items():
            if not isinstance(chain_addrs, list):
                continue
            chain_id = CHAIN_ALIAS.get(str(chain).lower(), str(chain).lower())
            for addr in chain_addrs:
                if not valid_address(addr):
                    continue
                labels.append(
                    label_row(LabelFields(addr, chain_id, name or f'CryptoScamDB {category}', 'scam', category, 'cryptoscamdb'))
                )
                scams.append(scam_row(ScamFields(addr, chain_id, 'cryptoscamdb', category, 0.9, f'CryptoScamDB: {name}')))
    return labels, scams


def rows_from_payload(payload: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Map the public-API payload (dict-wrapped or bare list) to rows."""
    if isinstance(payload, dict):
        scams = payload.get("result", payload.get("scams", payload.get("data", [])))
    elif isinstance(payload, list):
        scams = payload
    else:
        scams = []

    labels: list[dict[str, Any]] = []
    scam_rows: list[dict[str, Any]] = []
    for scam in scams:
        if not isinstance(scam, dict):
            continue
        name = scam.get("name", "")
        category = scam.get("category", "scam")
        status = scam.get("status", "unknown")
        description = scam.get("description", "")
        chain = _chain_from_category(category)
        for addr in scam.get("addresses", []) or []:
            if not valid_address(addr):
                continue
            labels.append(label_row(LabelFields(addr, chain, name or f'CryptoScamDB {category}', 'scam', category, 'cryptoscamdb')))
            evidence = f"CryptoScamDB: {name} | {description[:200]}" if description else f"CryptoScamDB: {name}"
            scam_rows.append(
                scam_row(ScamFields(addr, chain, 'cryptoscamdb', category, 0.9 if status == 'Active' else 0.7, evidence))
            )
    return labels, scam_rows
