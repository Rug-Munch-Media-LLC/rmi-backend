"""Shared CSV loading and storage helper functions."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime

from app.core.logging import get_logger

from .config import WALLET_LABELS_DDL

logger = get_logger("wallet_memory.label_importer")


def _normalize_chain(chain: str) -> str:
    """Normalize chain name to canonical form - delegates to shared_utils."""
    from app.core.shared_utils import normalize_chain

    result = normalize_chain(chain)
    return result or "ethereum"


async def _flush_batch(
    storage,
    redis_ops: list[tuple[str, str, str]],
    ch_rows: list[tuple],
    chain: str,
) -> None:
    """Flush a batch of Redis writes and ClickHouse inserts."""
    if storage._redis and redis_ops:
        try:
            async with storage._redis.pipeline() as pipe:
                for redis_key, label_json, addr in redis_ops:
                    pipe.set(redis_key, label_json)
                    pipe.sadd(f"rmi:labels:{chain}", addr)
                await pipe.execute()
        except Exception as e:
            logger.debug(f"Redis pipeline failed, falling back: {e}")
            for redis_key, label_json, addr in redis_ops:
                await storage.redis_set(redis_key, label_json)
                await storage.redis_sadd(f"rmi:labels:{chain}", addr)

    if ch_rows and storage._ch:
        try:
            storage._ch.execute(
                """INSERT INTO wallet_labels
                (address, chain_id, label_name, label_category, label_subtype,
                 source, is_sanctioned, loaded_at) VALUES""",
                ch_rows,
            )
        except Exception as e:
            logger.warning(f"ClickHouse batch insert failed: {e}")


async def _load_csv_labels(
    storage,
    path: str,
    chain: str,
    addr_col: str,
    label_cols: dict[str, str],
    source_name: str = "",
) -> int:
    """Load a CSV of labeled addresses into Redis + ClickHouse via WalletStorage."""
    if not os.path.exists(path):
        logger.warning(f"CSV not found: {path}")
        return 0

    src = source_name or os.path.basename(path)

    if storage._ch:
        try:
            existing = storage._ch.execute("SELECT count() FROM wallet_labels WHERE source = %(src)s", {"src": src})
            if existing and existing[0][0] > 100:
                logger.info(f"Source '{src}' already has {existing[0][0]:,} labels in CH - skipping re-import")
                return existing[0][0]
        except (ConnectionError, OSError):
            pass

    count = 0
    ch_rows = []
    redis_pipe_ops = []

    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames:
                reader.fieldnames = [h.lower().strip() for h in reader.fieldnames]
            for row in reader:
                addr = row.get(addr_col, "").strip().lower()
                if not addr or len(addr) < 10:
                    continue
                label = {"chain": chain, "address": addr, "source": src}
                for key, col in label_cols.items():
                    val = row.get(col, "").strip()
                    if val:
                        label[key] = val
                for extra in ("name", "label", "label_type", "label_subtype"):
                    if extra not in label:
                        val = row.get(extra, "").strip()
                        if val:
                            label[extra] = val
                label_json = json.dumps(label)
                redis_key = f"rmi:label:{chain}:{addr}"
                redis_pipe_ops.append((redis_key, label_json, addr))

                name = label.get(
                    "address_name",
                    label.get("dapp_name", label.get("protocol_name", label.get("name", ""))),
                )
                category = label.get("label_type", label.get("label", ""))
                subtype = label.get("label_subtype", "")
                ch_rows.append(
                    (
                        addr,
                        chain,
                        str(name) if name else "",
                        str(category) if category else "",
                        str(subtype) if subtype else "",
                        src,
                        0,
                        datetime.now(UTC),
                    )
                )
                count += 1
                if count % 5000 == 0:
                    await _flush_batch(storage, redis_pipe_ops, ch_rows, chain)
                    redis_pipe_ops = []
                    ch_rows = []
    except Exception as e:
        logger.warning(f"Failed to load CSV {path}: {e}")

    if redis_pipe_ops or ch_rows:
        await _flush_batch(storage, redis_pipe_ops, ch_rows, chain)

    logger.info(f"Loaded {count} labels from {src} (chain={chain})")
    return count


import os  # noqa: E402 — module docstring precedes import block


async def _ensure_wallet_labels_table(storage) -> None:
    """Create the wallet_labels table in ClickHouse if it doesn't exist."""
    if not storage._ch:
        return
    try:
        storage._ch.execute("CREATE DATABASE IF NOT EXISTS wallet_memory")
        storage._ch.execute(WALLET_LABELS_DDL)
        logger.info("wallet_labels table ensured in ClickHouse")
    except Exception as e:
        logger.warning(f"Failed to create wallet_labels table: {e}")
