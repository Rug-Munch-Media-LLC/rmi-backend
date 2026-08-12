"""
Market Intelligence Utility Functions
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _now() -> str:
    """ISO-8601 timestamp in UTC."""
    return datetime.now(UTC).isoformat()


def _safe_float(v: Any, default: float = 0.0) -> float:
    """Safely convert a value to float."""
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _parse_money(v: Any, default: float = 0.0) -> float:
    """Parse a CoinGecko money string like '$3,513,341' (or a float) to float."""
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        cleaned = v.replace("$", "").replace(",", "").replace(" ", "").strip()
        try:
            return float(cleaned)
        except (TypeError, ValueError):
            return default
    return default
