"""Human label for a health metric."""

from __future__ import annotations

NAMES: dict[str, str] = {"load1": "load", "disk_pct": "disk", "mem_pct": "memory"}


def label(metric: str) -> str:
    """Return the name to show for a metric."""
    return NAMES.get(metric, metric)
