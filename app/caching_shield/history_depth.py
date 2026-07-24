"""
Aggressive Caching Shield - Historical Depth Controller

Controls how far back transaction history queries go. Free tier RPC
endpoints often rate-limit or block deep history queries. This module
caps default queries to shallow depth and gates deep queries.

Strategy:
  - DEFAULT_DEPTH: 20 signatures (fast scan - enough to detect recent activity)
  - MAX_DEPTH: 100 signatures (deep scan - on-demand only, user clicks button)
  - MAX_PAGINATED: 200 (absolute maximum across all pages)
  - Deep queries are cached longer (5min vs 30s) since historical data rarely changes
  - Per-address cooldown: deep scan limited to once per 5 min per address
  - Before/signature pagination uses the "before" parameter (not offset-based)

Cache integration:
  - Shallow queries (limit <= 20): TTL 30s (default getSignaturesForAddress)
  - Deep queries (limit > 20): TTL 300s (5min, historical data is static)
  - Pagination queries: TTL 180s
"""

from __future__ import annotations

import time
from collections import OrderedDict

from app.core.logging import get_logger

logger = get_logger("history_depth")

# Default query depth for fast surface-level scans
DEFAULT_DEPTH = 20

# Maximum depth for a single query (prevents abuse)
MAX_DEPTH = 100

# Absolute maximum across all pagination
MAX_PAGINATED = 200

# Cooldown between deep scans per address (seconds)
DEEP_SCAN_COOLDOWN = 300  # 5 minutes

# How many addresses to track for cooldown
COOLDOWN_TRACK_SIZE = 500

class HistoryDepthController:
    """Controls transaction history query depth and deep-scan gating.

    Usage:
        ctrl = HistoryDepthController()
        limit = ctrl.clamp_limit(requested_limit=50, is_deep_scan=False)
        # -> returns 50 (within bounds, allowed)

        can_proceed, wait = ctrl.check_deep_cooldown("SoL...")
        if not can_proceed:
            raise DeepScanThrottled(f"Deep scan cooldown: {wait:.0f}s remaining")
    """

    def __init__(self) -> None:
        # Per-address deep scan cooldown: address -> last_deep_scan_ts
        self._deep_cooldowns: OrderedDict = OrderedDict()

    def clamp_limit(
        self,
        requested: int = DEFAULT_DEPTH,
        is_deep_scan: bool = False,
        is_paginated: bool = False,
    ) -> int:
        """Clamp a requested query limit to allowed bounds.

        Args:
            requested: The limit the caller wants
            is_deep_scan: True if this is an explicit deep-scan request (user clicked button)
            is_paginated: True if this is a continuation (before/after signature pagination)

        Returns:
            Clamped limit value
        """
        if is_deep_scan:
            return min(requested, MAX_DEPTH)
        elif is_paginated:
            return min(requested, MAX_PAGINATED)
        else:
            # Default shallow scan
            return min(requested, DEFAULT_DEPTH)

    def check_deep_cooldown(self, address: str) -> tuple[bool, float]:
        """Check if a deep scan is allowed for this address.

        Returns:
            (allowed, wait_seconds) - if not allowed, wait_seconds is how long to wait
        """
        now = time.monotonic()
        last = self._deep_cooldowns.get(address)

        if last is not None:
            elapsed = now - last
            if elapsed < DEEP_SCAN_COOLDOWN:
                wait = DEEP_SCAN_COOLDOWN - elapsed
                return (False, wait)

        return (True, 0.0)

    def record_deep_scan(self, address: str) -> None:
        """Record that a deep scan was performed for this address."""
        self._deep_cooldowns[address] = time.monotonic()
        # Evict oldest if over capacity
        while len(self._deep_cooldowns) > COOLDOWN_TRACK_SIZE:
            self._deep_cooldowns.popitem(last=False)

    def get_deep_cache_ttl(self, depth: int) -> int:
        """Get appropriate cache TTL based on query depth."""
        if depth > DEFAULT_DEPTH:
            return 300  # 5 min for deep queries
        elif depth > 10:
            return 60  # 1 min for medium queries
        else:
            return 30  # 30s for shallow queries

    def stats(self) -> dict:
        """Return controller statistics."""
        return {
            "addresses_in_cooldown": len(self._deep_cooldowns),
            "default_depth": DEFAULT_DEPTH,
            "max_depth": MAX_DEPTH,
            "max_paginated": MAX_PAGINATED,
            "deep_scan_cooldown_s": DEEP_SCAN_COOLDOWN,
        }

# ── Singleton ──────────────────────────────────────────────────────────────

_controller: HistoryDepthController | None = None

def get_history_controller() -> HistoryDepthController:
    global _controller
    if _controller is None:
        _controller = HistoryDepthController()
    return _controller
