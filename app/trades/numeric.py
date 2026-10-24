"""Shared numeric coercion for the trade adapters.

Providers send numbers as strings, ``None``, or junk, and every adapter needs
the same defensive coercion. It lives here once, and unparsable input is logged
instead of silently becoming a zero the caller cannot see.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# Anything below this is seconds; above it is already milliseconds.
_EPOCH_SECONDS_CUTOFF = 10_000_000_000


def as_number(value: Any, *, integer: bool = False) -> float | int:
    """Coerce one provider value; unparsable input yields 0 (logged at debug)."""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        logger.debug(
            "trade_number_unparsable",
            value_type=type(value).__name__,
            error=type(exc).__name__,
        )
        number = 0.0
    return int(number) if integer else number


def epoch_ms(value: Any) -> int:
    """Provider timestamp (seconds, milliseconds, or junk) as epoch millis."""
    n = as_number(value, integer=True)
    return n * 1000 if n < _EPOCH_SECONDS_CUTOFF else n
