"""Published-at resolution — never fabricate freshness.

A feed entry with no machine-readable timestamp has UNKNOWN freshness. It must
not be stamped with the ingest clock: that turns an undated aggregator row into
a "brand new" story and inflates every fresh-share metric downstream.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime


def resolve_published_at(entry: dict) -> datetime | None:
    """Return the feed's published/updated time, or None when it is unknown."""
    raw = entry.get("published") or entry.get("updated")
    if not raw:
        return None
    with contextlib.suppress(Exception):
        yr, mo, dy, hr, mi, se = raw[:6]
        return datetime(yr, mo, dy, hr, mi, se, tzinfo=UTC)
    return None
