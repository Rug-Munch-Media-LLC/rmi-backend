"""Entity-overlap deduplication."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.rag.dedup._base import tokenize

ENTITY_OVERLAP_THRESHOLD = 0.8
ENTITY_WINDOW_HOURS = 6


def extract_crypto_entities(text: str) -> set[str]:
    """Extract crypto entities (tokens, protocols, addresses) from text."""
    import re
    addresses = set(re.findall(r"0x[a-fA-F0-9]{40}", text or ""))
    words = set(re.findall(r"\b[A-Z][a-z]{2,}\b", text or ""))
    return addresses | words


def is_duplicate_by_entities(
    text_a: str, text_b: str, threshold: float = ENTITY_OVERLAP_THRESHOLD
) -> bool:
    ta, tb = extract_crypto_entities(text_a), extract_crypto_entities(text_b)
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / min(len(ta), len(tb))
    return overlap >= threshold


def parse_ts(value: str) -> datetime | None:
    from datetime import datetime as dt
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.strptime(value, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def within_window(ts_a: datetime | None, ts_b: datetime | None, hours: int = ENTITY_WINDOW_HOURS) -> bool:
    if ts_a is None or ts_b is None:
        return False
    delta = abs((ts_a - ts_b).total_seconds())
    return delta <= hours * 3600
