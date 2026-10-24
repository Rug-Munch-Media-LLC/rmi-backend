"""Coercion helpers for the GMGN intelligence normalizers.

Null-plus-reason is the contract: a missing field yields ``None`` plus an
entry in the caller's ``missing`` list — never a fabricated zero.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

_CHAIN_ALIASES = {
    "sol": "solana",
    "solana": "solana",
    "eth": "ethereum",
    "ethereum": "ethereum",
    "bsc": "bsc",
    "bnb": "bsc",
    "base": "base",
}


_GMGN_SLUGS = {"solana": "sol", "ethereum": "eth", "bsc": "bsc", "base": "base"}


def gmgn_chain(value: Any) -> str:
    """Canonical chain name -> GMGN slug (``solana`` -> ``sol``)."""
    name = canonical_chain(value)
    return _GMGN_SLUGS.get(name, name)


def num(value: Any) -> float | None:
    """Coerce to float; ``None`` when absent, boolean, or unparseable."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def whole(value: Any) -> int | None:
    """Coerce to int; ``None`` when absent."""
    raw = num(value)
    return None if raw is None else int(raw)


def iso_ts(value: Any) -> str | None:
    """Unix seconds -> ISO-8601 UTC; ``None`` for zero/absent/out-of-range."""
    ts = num(value)
    if not ts or ts <= 0:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=UTC).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def now_iso() -> str:
    """Current UTC timestamp in ISO-8601."""
    return datetime.now(UTC).isoformat()


def gap(field: str, reason: str) -> dict[str, str]:
    """One machine-readable data gap (field + why it is null)."""
    return {"field": field, "reason": reason}


def text(value: Any) -> str | None:
    """Trimmed non-empty string, else ``None``."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def canonical_chain(value: Any) -> str:
    """Map GMGN chain slugs (``sol``) onto our canonical chain names."""
    raw = text(value) or "solana"
    return _CHAIN_ALIASES.get(raw.lower(), raw.lower())
