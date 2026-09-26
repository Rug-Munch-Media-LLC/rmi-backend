"""Rate a health sample as ok, warn or critical."""

from __future__ import annotations

from .health_limits import limits
from .health_reason import reason


def rate(sample: dict[str, float]) -> tuple[str, str]:
    """Return (severity, reason); critical outranks warn.

    The worst signal is reported, not whichever threshold was checked first, so
    a box that is simultaneously low on disk and busy says so. Load uses the
    15-minute average when available, to suppress short-lived CPU bursts.
    """
    worst = ""
    for metric, warn_at, critical_at in limits(sample):
        value = sample.get(metric, sample.get("load1", 0.0) if metric.startswith("load") else 0.0)
        if value >= critical_at:
            return "critical", reason(metric, value, critical_at, sample)
        if value >= warn_at:
            worst = reason(metric, value, warn_at, sample)
    return ("warn", worst) if worst else ("ok", "")
