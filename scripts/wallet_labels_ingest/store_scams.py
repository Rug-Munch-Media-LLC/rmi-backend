#!/usr/bin/env python3
"""Batched writer for the scam_addresses table (ClickHouse + Redis hot cache).

Split from ``store_labels`` so each module keeps a single primary writer.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from .logging_setup import logger

__all__ = ["insert_scam_addresses"]

SCAM_COLUMNS = ["address", "chain_id", "source", "threat_type", "confidence", "first_seen_at", "evidence"]


def _scam_rows(addresses: list[dict[str, Any]], now: Any, as_tuple: bool) -> list:
    """Normalise one scam batch into ClickHouse rows."""
    rows: list = []
    for entry in addresses:
        addr = entry["address"].lower().strip()
        chain = entry.get("chain_id", "ethereum").lower().strip()
        source = entry.get("source", "ingest_script").strip()[:128]
        threat = entry.get("threat_type", "scam").strip()[:128]
        confidence = float(entry.get("confidence", 0.9))
        evidence = entry.get("evidence", "").strip()[:1024]

        if not addr or len(addr) < 10:
            continue

        row = [addr, chain, source, threat, confidence, now, evidence]
        rows.append(tuple(row) if as_tuple else row)
    return rows


def _cache_scam_rows(redis: Any, rows: list) -> None:
    """Mirror one scam batch into Redis (``wm:scam:*`` keys + set)."""
    try:
        pipe = redis.pipeline()
        for r in rows:
            data = {"source": r[2], "threat_type": r[3], "confidence": r[4]}
            pipe.set(f"wm:scam:{r[1]}:{r[0]}", json.dumps(data))
            pipe.sadd(f"wm:scam_set:{r[1]}", r[0])
        pipe.execute()
    except Exception as e:
        logger.debug(f"Redis scam cache failed: {e}")


def _flush_scam_batch(ch: Any, redis: Any | None, rows: list, as_tuple: bool) -> int:
    """Write one scam batch to ClickHouse then Redis; return rows accepted."""
    accepted = 0
    if not rows:
        return accepted
    try:
        if as_tuple:
            ch.execute(
                """INSERT INTO scam_addresses
                (address, chain_id, source, threat_type, confidence, first_seen_at, evidence) VALUES""",
                rows,
            )
        else:
            ch.insert("scam_addresses", rows, column_names=SCAM_COLUMNS)
        accepted = len(rows)
    except Exception as e:
        logger.warning(f"ClickHouse scam insert failed: {e}")
    if redis:
        _cache_scam_rows(redis, rows)
    return accepted


def insert_scam_addresses(ch: Any, redis: Any | None, addresses: list[dict[str, Any]], batch_size: int = 500) -> int:
    """Batch insert into the scam_addresses table."""
    if not addresses:
        return 0

    inserted = 0
    now = datetime.now(UTC)
    as_tuple = not hasattr(ch, "query")

    for i in range(0, len(addresses), batch_size):
        inserted += _flush_scam_batch(ch, redis, _scam_rows(addresses[i : i + batch_size], now, as_tuple), as_tuple)

    return inserted
