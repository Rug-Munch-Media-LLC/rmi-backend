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
