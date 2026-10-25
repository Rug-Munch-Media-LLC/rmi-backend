"""Freshness classification — unknown is a first-class answer."""

from __future__ import annotations

from datetime import datetime, timedelta

FRESH = "fresh"
STALE = "stale"
UNKNOWN = "unknown"

_MAX_FUTURE_SKEW = timedelta(hours=1)


def freshness(published_at: datetime | None, ingested_at: datetime, max_age_hours: int = 24) -> str:
    """Classify an item as fresh / stale / unknown at ingest time.

    Unknown timestamps and clock-garbage timestamps (more than one hour in the
    future) are UNKNOWN, never fresh.
    """
    if published_at is None:
        return UNKNOWN
    if published_at > ingested_at + _MAX_FUTURE_SKEW:
        return UNKNOWN
    if ingested_at - published_at <= timedelta(hours=max_age_hours):
        return FRESH
    return STALE
