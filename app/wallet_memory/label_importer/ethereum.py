"""Ethereum label loaders — Etherscan combined + phish/hack."""

from __future__ import annotations

import contextlib
import csv
import json
from datetime import UTC, datetime

from app.core.logging import get_logger

from .config import ETHERSCAN_COMBINED, ETHERSCAN_PHISH_HACK
from .loaders import _flush_batch, _normalize_chain

logger = get_logger("wallet_memory.label_importer")

async def _load_etherscan_combined(storage) -> int:
    """Load etherscan_combined_labels.csv."""
    if not os.path.exists(ETHERSCAN_COMBINED):
        logger.warning(f"Etherscan combined labels not found: {ETHERSCAN_COMBINED}")
        return 0

    count = 0
    ch_rows = []
    redis_ops = []

    try:
        with open(ETHERSCAN_COMBINED, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                addr = row.get("address", "").strip().lower()
                if not addr or len(addr) < 10:
                    continue
                chain_raw = row.get("chain", "ethereum").strip().lower()
                chain = _normalize_chain(chain_raw)
                name = row.get("name", "").strip()
                label = row.get("label", "").strip()

                label_data = {
                    "chain": chain, "address": addr,
                    "address_name": name, "label_type": label,
                    "source": "etherscan_combined",
                }
                label_json = json.dumps(label_data)
                redis_key = f"rmi:label:{chain}:{addr}"
                redis_ops.append((redis_key, label_json, addr))
                ch_rows.append((
                    addr, chain, name, label, "",
                    "etherscan_combined", 0, datetime.now(UTC),
                ))
                count += 1
                if count % 5000 == 0:
                    await _flush_batch(storage, redis_ops, ch_rows, chain)
                    redis_ops = []
                    ch_rows = []
    except Exception as e:
        logger.warning(f"Failed to load etherscan combined: {e}")

    if redis_ops or ch_rows:
        await _flush_batch(storage, redis_ops, ch_rows, "ethereum")

    logger.info(f"Loaded {count} etherscan combined labels")
    return count

import os


async def _load_etherscan_phish_hack(storage) -> int:
    """Load etherscan_phish_hack.csv."""
    if not os.path.exists(ETHERSCAN_PHISH_HACK):
        logger.warning(f"Etherscan phish/hack not found: {ETHERSCAN_PHISH_HACK}")
        return 0

    count = 0
    ch_rows = []
    redis_ops = []

    try:
        with open(ETHERSCAN_PHISH_HACK, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                addr = row.get("address", "").strip().lower()
                if not addr or len(addr) < 10:
                    continue
                chain_raw = row.get("chain", "ethereum").strip().lower()
                chain = _normalize_chain(chain_raw)
                name_tag = row.get("name_tag", "").strip()
                label_type = row.get("label_type", "").strip()
                balance = row.get("balance", "").strip()
                txn_count = row.get("txn_count", "").strip()

                label_data = {
                    "chain": chain, "address": addr,
                    "address_name": name_tag, "label_type": label_type,
                    "source": "etherscan_phish_hack",
                }
                if balance:
                    label_data["balance"] = balance
                if txn_count:
                    label_data["txn_count"] = txn_count
                label_json = json.dumps(label_data)
                redis_key = f"rmi:label:{chain}:{addr}"
                redis_ops.append((redis_key, label_json, addr))

                is_scam = 1 if label_type.lower() in ("scam", "phishing", "hack") else 0
                ch_rows.append((
                    addr, chain, name_tag, label_type, "",
                    "etherscan_phish_hack", is_scam, datetime.now(UTC),
                ))

                if is_scam and storage._ch:
                    with contextlib.suppress(Exception):
                        storage._ch.execute(
                            """INSERT INTO scam_addresses
                            (address, chain_id, source, threat_type, confidence,
                             first_seen_at, evidence) VALUES""",
                            [(addr, chain, "etherscan_phish_hack", label_type, 0.95,
                              datetime.now(UTC), json.dumps({"name_tag": name_tag, "balance": balance}))],
                        )
                count += 1
    except Exception as e:
        logger.warning(f"Failed to load etherscan phish/hack: {e}")

    if redis_ops or ch_rows:
        await _flush_batch(storage, redis_ops, ch_rows, "ethereum")

    logger.info(f"Loaded {count} etherscan phish/hack labels")
    return count
