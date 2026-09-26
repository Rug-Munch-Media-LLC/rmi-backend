"""Render the human-readable reason for one threshold crossing.

Split out of the rater so the wording lives apart from the thresholds, and so
one function per module keeps both readable.
"""

from __future__ import annotations

from .metric_label import label
from .metric_number import number


def reason(metric: str, value: float, threshold: float, sample: dict[str, float]) -> str:
    """Return the alert reason, adding the core count for load metrics."""
    text = f"{label(metric)} {number(metric, value)} (>= {number(metric, threshold)})"
    if metric in {"load1", "load15"}:
        cpu_count = max(1.0, float(sample.get("cpu_count", 1.0)))
        text += f" on {cpu_count:.0f} cores"
    return text
