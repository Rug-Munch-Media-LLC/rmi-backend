#!/usr/bin/env python3
"""Batched writers for wallet_labels / scam_addresses (ClickHouse + Redis).

One primary function per module (fleet canon): ``insert_wallet_labels`` and
``insert_scam_addresses`` are the two entry points, each delegating row
normalisation to a private helper.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from .ch_client import ch_select
from .logging_setup import logger

__all__ = ["insert_wallet_labels", "make_label_id"]

LABEL_COLUMNS = [
    "address",
    "chain_id",
    "label_name",
    "label_category",
    "label_subtype",
    "source",
    "is_sanctioned",
    "loaded_at",
]

SCAM_COLUMNS = ["address", "chain_id", "source", "threat_type", "confidence", "first_seen_at", "evidence"]


def make_label_id(address: str, source: str, chain: str, name: str = "") -> str:
    """Stable hash-based ID for dedup."""
    raw = f"wallet_label:{address.lower()}:{source}:{chain.lower()}:{name}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _known_sources(ch: Any) -> set[str]:
    """Best-effort set of source values already present in wallet_labels."""
    try:
        result = ch_select(ch, "SELECT DISTINCT source FROM wallet_labels")
        return {r[0] for r in result} if result else set()
    except Exception:
        return set()


def _label_rows(
    labels: list[dict[str, Any]],
    seen_sources: set[str],
    now: Any,
    as_tuple: bool,
) -> tuple[list, list[tuple[str, str]]]:
    """Normalise one label batch into CH rows plus Redis (key, json) pairs."""
    ch_rows: list = []
    redis_ops: list[tuple[str, str]] = []
    for lbl in labels:
        addr = lbl["address"].lower().strip()
        chain = lbl.get("chain_id", "ethereum").lower().strip()
        name = lbl.get("label_name", "").strip()[:256]
        category = lbl.get("label_category", "").strip()[:128]
        subtype = lbl.get("label_subtype", "").strip()[:128]
        source = lbl.get("source", "ingest_script").strip()[:128]
        sanctioned = 1 if lbl.get("is_sanctioned", False) else 0

        if not addr or len(addr) < 10 or not name:
            continue
        if source in seen_sources:
            continue

        row = [addr, chain, name, category, subtype, source, sanctioned, now]
        ch_rows.append(tuple(row) if as_tuple else row)

        label_json = json.dumps({"chain": chain, "address": addr, "name": name, "type": category, "source": source})
        redis_ops.append((f"rmi:label:{chain}:{addr}", label_json))
    return ch_rows, redis_ops


def _flush_label_batch(
    ch: Any, redis: Any | None, ch_rows: list, redis_ops: list[tuple[str, str]], as_tuple: bool
) -> int:
    """Write one label batch to ClickHouse and the Redis hot cache; return accepted rows."""
    accepted = 0
    if ch_rows:
        try:
            if as_tuple:
                ch.execute(
                    """INSERT INTO wallet_labels
                    (address, chain_id, label_name, label_category, label_subtype,
                     source, is_sanctioned, loaded_at) VALUES""",
                    ch_rows,
                )
            else:
                ch.insert("wallet_labels", ch_rows, column_names=LABEL_COLUMNS)
            accepted = len(ch_rows)
        except Exception as e:
            logger.warning(f"ClickHouse insert failed ({len(ch_rows)} rows): {e}")

    if redis and redis_ops:
        try:
            pipe = redis.pipeline()
            for rk, rv in redis_ops:
                pipe.set(rk, rv)
            pipe.execute()
        except Exception as e:
            logger.debug(f"Redis pipeline failed: {e}")
    return accepted


def insert_wallet_labels(ch: Any, redis: Any | None, labels: list[dict[str, Any]], batch_size: int = 1000) -> int:
    """Batch insert labels into ClickHouse wallet_labels + Redis cache."""
    if not labels:
        return 0

    inserted = 0
    now = datetime.now(UTC)
    as_tuple = not hasattr(ch, "query")
    seen_sources = _known_sources(ch)

    for i in range(0, len(labels), batch_size):
        ch_rows, redis_ops = _label_rows(labels[i : i + batch_size], seen_sources, now, as_tuple)
        inserted += _flush_label_batch(ch, redis, ch_rows, redis_ops, as_tuple)
        if (i // batch_size) % 10 == 0 and i > 0 and not (i // batch_size) % 100:
            logger.info(f"  Inserted {inserted}/{len(labels)} labels...")

    return inserted
