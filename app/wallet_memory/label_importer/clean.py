"""Clean data label loader — from label quality fixer."""

from __future__ import annotations

import csv
import os
from typing import Any

from app.core.logging import get_logger

from .config import CLEAN_DIR

logger = get_logger("wallet_memory.label_importer")


async def load_clean_labels(storage) -> dict[str, Any]:
    """Load pre-cleaned, deduplicated labels from the quality fixer.
    Returns stats dict with counts."""
    stats = {"ethereum": 0, "solana": 0, "errors": 0}

    for chain in ["ethereum", "solana"]:
        path = os.path.join(CLEAN_DIR, f"wallet_labels_{chain}.csv")
        if not os.path.exists(path):
            logger.warning(f"Clean labels file not found: {path}")
            continue

        labels = []
        with open(path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                labels.append(
                    {
                        "address": row["address"],
                        "chain": row["chain"],
                        "source": row.get("source", "clean_import"),
                        "name": row.get("name", ""),
                        "label_type": row.get("label_type", ""),
                        "label_subtype": row.get("label_subtype", ""),
                        "project": row.get("project", ""),
                        "entity_type": row.get("entity_type", "unknown"),
                    }
                )

        if labels and storage:
            try:
                await storage.bulk_import(labels, source=f"clean_{chain}")
                stats[chain] = len(labels)
                logger.info(f"Loaded {len(labels):,} clean {chain} labels")
            except Exception as e:
                logger.error(f"Failed to import clean {chain} labels: {e}")
                stats["errors"] += 1

    logger.info(f"Clean label import complete: {stats}")
    return stats
