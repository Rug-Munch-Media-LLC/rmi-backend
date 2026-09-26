#!/usr/bin/env python3
"""Readers for the local Etherscan label CSVs (phish/hack + combined labels).

Each reader is synchronous and returns ``(label_rows, scam_rows)``. The caller
owns the ClickHouse writes.
"""

from __future__ import annotations

import csv
import os
from typing import Any

from .logging_setup import logger
from .paths import DATA_DIR
from .row_builders import LabelFields, ScamFields, label_row, scam_row, valid_address

__all__ = ["read_combined_csv", "read_phish_csv"]

COMBINED_SCAM_KEYWORDS = (
    "phish",
    "hack",
    "exploit",
    "scam",
    "drain",
    "attack",
    "heist",
    "malicious",
    "fraud",
    "rug",
    "compromis",
)


def read_phish_csv(path: str, chain: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read etherscan_phish_hack.csv into label + scam rows (best effort)."""
    labels: list[dict[str, Any]] = []
    scams: list[dict[str, Any]] = []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                addr = row.get("address", "").strip()
                name_tag = row.get("name_tag", "").strip()
                label_type = row.get("label_type", "phish-hack").strip()
                row_chain = row.get("chain", "Ethereum").strip()
                if chain != "all" and chain.lower() not in row_chain.lower():
                    continue
                if not valid_address(addr):
                    continue
                labels.append(
                    label_row(LabelFields(addr, 'ethereum', name_tag or f'Etherscan {label_type}', 'scam', label_type, 'etherscan_phish_hack'))
                )
                scams.append(
                    scam_row(ScamFields(addr, 'ethereum', 'etherscan_phish_hack', label_type, 0.95, f'Etherscan label: {name_tag} ({label_type})'))
                )
    except Exception as e:
        logger.warning(f"Failed to read phish CSV: {e}")
    return labels, scams


def read_combined_csv(path: str, chain: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read etherscan_combined_labels.csv into label + scam rows (best effort)."""
    labels: list[dict[str, Any]] = []
    scams: list[dict[str, Any]] = []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                addr = row.get("address", "").strip()
                label = row.get("label", "").strip()
                name = row.get("name", "").strip()
                row_chain = row.get("chain", "ethereum").strip().lower()
                if chain != "all" and chain.lower() not in row_chain:
                    continue
                if not valid_address(addr):
                    continue
                is_scam = any(kw in label.lower() for kw in COMBINED_SCAM_KEYWORDS)
                labels.append(
                    label_row(LabelFields(addr, row_chain, name or label, 'scam' if is_scam else 'entity', label, 'etherscan_combined'))
                )
                if is_scam:
                    scams.append(
                        scam_row(ScamFields(addr, row_chain, 'etherscan_combined', label, 0.9, f'Etherscan combined label: {label}'))
                    )
    except Exception as e:
        logger.warning(f"Failed to read combined CSV: {e}")
    return labels, scams


def phish_csv_path() -> str:
    """Absolute path of the phish/hack CSV."""
    return os.path.join(DATA_DIR, "etherscan_phish_hack.csv")


def combined_csv_path() -> str:
    """Absolute path of the combined labels CSV."""
    return os.path.join(DATA_DIR, "etherscan_combined_labels.csv")
