"""Format a health metric with its unit."""

from __future__ import annotations


def number(metric: str, value: float) -> str:
    """Format a value: a percentage for the pct metrics, a plain load otherwise."""
    return f"{value:.0f}%" if metric.endswith("_pct") else f"{value:.1f}"
