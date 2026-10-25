"""Quota tiers for the public read API.

One table, one resolver. Every limit the middleware and the routes enforce is
read from here — a route never carries its own allowance, so "no bypass" is a
property of the data rather than of each handler's memory.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

TierName = Literal["anonymous", "free", "paid"]

# Depth is the projection, not a scan switch: a public caller never triggers a
# paid scan, it only reads what is already cached. ``full`` therefore means
# "more of the cached payload", never "run more work".
ScanDepth = Literal["summary", "standard", "full"]


@dataclass(frozen=True, slots=True)
class PublicTier:
    """One row of the public quota table."""

    name: TierName
    requests_per_min: int
    scan_depth: ScanDepth
    history_window_days: int
    export_max_rows: int
    websocket_streams: int
    cache_ttl_s: int
    stale_while_revalidate_s: int


_ANONYMOUS = PublicTier("anonymous", 20, "summary", 7, 0, 0, 30, 300)
_FREE = PublicTier("free", 60, "standard", 30, 1_000, 1, 60, 600)
_PAID = PublicTier("paid", 600, "full", 365, 100_000, 5, 300, 900)

TIERS: Final[dict[str, PublicTier]] = {tier.name: tier for tier in (_ANONYMOUS, _FREE, _PAID)}


def resolve_tier(plan: str | None, *, authenticated: bool) -> PublicTier:
    """Map a caller onto a tier. Unknown or paid-but-unauthenticated → anonymous."""
    if not authenticated:
        return _ANONYMOUS
    return TIERS.get((plan or "free").strip().lower(), _FREE)
