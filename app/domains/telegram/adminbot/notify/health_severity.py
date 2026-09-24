"""Rate a health sample as ok, warn or critical."""

from __future__ import annotations

import os

from .metric_label import label
from .metric_number import number

# Load is normalized by CPU count. These defaults avoid paging on a busy
# 16-core host whose 15-minute load is still below sustained saturation.
LOAD_WARN_PER_CPU = float(os.getenv("HEALTH_LOAD_WARN_PER_CPU", "1.5"))
LOAD_CRITICAL_PER_CPU = float(os.getenv("HEALTH_LOAD_CRITICAL_PER_CPU", "2.0"))


def _limits(sample: dict[str, float]) -> tuple[tuple[str, float, float], ...]:
    cpu_count = max(1.0, float(sample.get("cpu_count", 1.0)))
    load_metric = "load15" if "load15" in sample else "load1"
    return (
        ("disk_pct", 80.0, 92.0),
        ("mem_pct", 85.0, 95.0),
        (
            load_metric,
            max(8.0, cpu_count * LOAD_WARN_PER_CPU),
            max(16.0, cpu_count * LOAD_CRITICAL_PER_CPU),
        ),
    )


def _reason(metric: str, value: float, threshold: float, sample: dict[str, float]) -> str:
    text = f"{label(metric)} {number(metric, value)} (>= {number(metric, threshold)})"
    if metric in {"load1", "load15"}:
        cpu_count = max(1.0, float(sample.get("cpu_count", 1.0)))
        text += f" on {cpu_count:.0f} cores"
    return text


def rate(sample: dict[str, float]) -> tuple[str, str]:
    """Return (severity, reason); critical outranks warn.

    The worst signal is reported, not whichever threshold was checked first, so
    a box that is simultaneously low on disk and busy says so.  Load uses the
    15-minute average when available to suppress short-lived CPU bursts.
    """
    reason = ""
    for metric, warn_at, critical_at in _limits(sample):
        value = sample.get(metric, sample.get("load1", 0.0) if metric.startswith("load") else 0.0)
        if value >= critical_at:
            return "critical", _reason(metric, value, critical_at, sample)
        if value >= warn_at:
            reason = _reason(metric, value, warn_at, sample)
    return ("warn", reason) if reason else ("ok", "")
