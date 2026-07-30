"""
Market Intelligence Cache Layer
In-memory caching with TTL for external API calls.
"""

from __future__ import annotations

import os
import time
from typing import Any

# ── API Keys from env ──────────────────────────────────────
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "")
COINGECKO_BASE = "https://api.coingecko.com/api/v3"
DEXSCREENER_BASE = "https://api.dexscreener.com"

# ── In-Memory Cache ────────────────────────────────────────
_cache: dict[str, tuple[float, Any]] = {}


def _cget(key: str, ttl: float) -> Any | None:
    """Get cached value if within TTL."""
    entry = _cache.get(key)
    if entry and (time.time() - entry[0]) < ttl:
        return entry[1]
    return None


def _cset(key: str, data: Any) -> None:
    """Set cached value and enforce max cache size."""
    _cache[key] = (time.time(), data)
    if len(_cache) > 200:
        oldest = min(_cache, key=lambda k: _cache[k][0])
        del _cache[oldest]
