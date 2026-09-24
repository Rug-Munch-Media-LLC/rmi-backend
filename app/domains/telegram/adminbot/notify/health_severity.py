"""Rate a health sample as ok, warn or critical."""

from __future__ import annotations

from .metric_label import label
from .metric_number import number

# (metric, warn at, critical at)
LIMITS: tuple[tuple[str, float, float], ...] = (
    ("disk_pct", 80.0, 92.0),
    ("mem_pct", 85.0, 95.0),
    ("load1", 8.0, 16.0),
)


def rate(sample: dict[str, float]) -> tuple[str, str]:
    """Return (severity, reason); critical outranks warn.

    The worst signal is reported, not whichever threshold was checked first, so
    a box that is simultaneously low on disk and busy says so.
    """
    reason = ""
    for metric, warn_at, critical_at in LIMITS:
        value = sample.get(metric, 0.0)
        if value >= critical_at:
            return "critical", f"{label(metric)} {number(metric, value)} (>= {number(metric, critical_at)})"
        if value >= warn_at:
            reason = f"{label(metric)} {number(metric, value)} (>= {number(metric, warn_at)})"
    return ("warn", reason) if reason else ("ok", "")
