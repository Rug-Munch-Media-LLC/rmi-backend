"""BSC label loader."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime

from app.core.logging import get_logger

from .config import LABELS_DIR
from .loaders import _flush_batch, _normalize_chain

logger = get_logger("wallet_memory.label_importer")

async def _load_bsc_detail(storage) -> int:
    """Load BSC address tag detail CSV. 15K BSC addresses with entity labels."""
    path = os.path.join(LABELS_DIR, "2024-01-31-detail.csv")
    if not os.path.exists(path):
        logger.warning(f"BSC detail CSV not found: {path}")
        return 0

    count = 0
    ch_rows = []
    redis_ops = []

    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                addr = row.get("address", "").strip().lower()
                if not addr or len(addr) < 10:
                    continue
                label_name = row.get("label_name", "").strip()
                name_tag = row.get("name_tag", "").strip()
                addr_type = row.get("address_type", "").strip()
                chain_code = row.get("chain_code", "BSC").strip().lower()
                chain = _normalize_chain(chain_code)

                category = (
                    "exchange" if "exchange" in (label_name + name_tag).lower()
                    else "defi" if any(w in (label_name + name_tag).lower()
                                      for w in ("protocol", "swap", "defi", "lend"))
                    else "bridge" if "bridge" in (label_name + name_tag).lower()
                    else "wallet"
                )

                label_data = {
                    "chain": chain, "address": addr,
                    "address_name": label_name,
                    "label_type": name_tag or label_name,
                    "label_subtype": addr_type,
                    "source": "bsc_detail",
                }
                label_json = json.dumps(label_data)
                redis_key = f"rmi:label:{chain}:{addr}"
                redis_ops.append((redis_key, label_json, addr))
                ch_rows.append((
                    addr, chain, name_tag or label_name, category, addr_type,
                    "bsc_detail", 0, datetime.now(UTC),
                ))
                count += 1
                if count % 5000 == 0:
                    await _flush_batch(storage, redis_ops, ch_rows, chain)
                    redis_ops = []
                    ch_rows = []
    except Exception as e:
        logger.warning(f"Failed to load BSC detail: {e}")

    if redis_ops or ch_rows:
        await _flush_batch(storage, redis_ops, ch_rows, "bsc")

    logger.info(f"Loaded {count} BSC detail labels")
    return count

import os
