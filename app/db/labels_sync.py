"""
labels_sync.py — CSV/JSONL → ClickHouse (SSOT) batched upsert.

SSOT: ClickHouse wallet_memory.wallet_labels (MergeTree, 39.4M rows, 39.4M unique addresses).
PG public.labels is authoritative for application-level reads but is a downstream
consumer that is populated separately.

Usage:
    # Ingest public_labels.jsonl (smallest dataset, ~39K records)
    python -m app.db.labels_sync /path/to/labels/public_labels.jsonl

    # Dry-run everything else
    python -m app.db.labels_sync /path/to/labels/other.csv --dry-run
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

import clickhouse_connect

logger = logging.getLogger(__name__)

# ── config ──────────────────────────────────────────────────────────────
CLICKHOUSE_HOST = "127.0.0.1"
CLICKHOUSE_PORT = 8123
BATCH_SIZE = 10_000
TABLE = "wallet_memory.wallet_labels"

# JSONL → CH column mapping (public_labels.jsonl format)
COLUMN_MAP = {
    "address": "address",
    "label": "label_name",
    "chain": "chain_id",
    "source": "source",
    "category": "label_category",
}


def get_client() -> Any:
    """Return a ClickHouse client (raises on failure)."""
    return clickhouse_connect.get_client(
        host=CLICKHOUSE_HOST,
        port=CLICKHOUSE_PORT,
    )


def parse_file(path: Path) -> list[dict]:
    """Parse a file into a list of dicts keyed by CH column names.

    Supports:
      - .jsonl  (JSON Lines)
      - .csv    (header row, comma-delimited)
    """
    suffix = path.suffix.lower()
    rows: list[dict] = []

    if suffix == ".jsonl":
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                raw = json.loads(line)
                mapped: dict[str, Any] = {}
                for json_key, ch_col in COLUMN_MAP.items():
                    if json_key in raw:
                        mapped[ch_col] = raw[json_key]
                if "address" not in mapped:
                    continue  # skip malformed
                rows.append(mapped)

    elif suffix == ".csv":
        import csv

        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                mapped = {}
                for csv_key, ch_col in COLUMN_MAP.items():
                    if csv_key in row:
                        mapped[ch_col] = row[csv_key]
                if "address" not in mapped:
                    continue
                rows.append(mapped)
    else:
        raise ValueError(f"Unsupported file type: {suffix}")

    return rows


def upsert_batch(client: Any, batch: list[dict]) -> int:
    """INSERT a batch into MergeTree. Duplicates are handled by
    ORDER BY (address, label_name, source) — same-key rows coexist
    in MergeTree. For dedup, run periodically:

        OPTIMIZE TABLE {TABLE} FINAL

    or switch to ReplacingMergeTree.
    """
    client.insert(
        table=TABLE,
        column_names=["address", "chain_id", "label_name", "source", "label_category"],
        data=[
            (
                r.get("address", ""),
                r.get("chain_id", ""),
                r.get("label_name", ""),
                r.get("source", ""),
                r.get("label_category", ""),
            )
            for r in batch
        ],
    )
    return len(batch)


def main() -> None:
    args = sys.argv[1:]
    if not args:
        logger.error("Usage: python -m app.db.labels_sync <file> [--dry-run]")
        sys.exit(1)

    file_path = Path(args[0])
    dry_run = "--dry-run" in args

    if not file_path.exists():
        logger.error("File not found: %s", file_path)
        sys.exit(1)

    logger.info("[labels_sync] Parsing %s …", file_path)
    rows = parse_file(file_path)
    logger.info("[labels_sync]  %d rows parsed", len(rows))

    if dry_run:
        logger.warning("[labels_sync]  DRY RUN — no rows written")
        return

    client = get_client()
    total = 0
    t0 = time.time()

    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        n = upsert_batch(client, batch)
        total += n
        logger.info("[labels_sync]  inserted %d/%d  (%d this batch)", total, len(rows), n)

    elapsed = time.time() - t0
    logger.info("[labels_sync]  DONE — %d rows in %.1fs (%.0f rows/s)", total, elapsed, total / elapsed)


if __name__ == "__main__":
    main()
