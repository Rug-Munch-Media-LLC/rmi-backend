"""The warn/critical thresholds for each health metric.

Split out of the rater so this module holds only the numbers, and so adding a
metric cannot quietly push the rater over its budget.
"""

from __future__ import annotations

import os

# Load is normalized by CPU count. These defaults avoid paging on a busy
# 16-core host whose 15-minute load is still below sustained saturation.
LOAD_WARN_PER_CPU = float(os.getenv("HEALTH_LOAD_WARN_PER_CPU", "1.5"))
LOAD_CRITICAL_PER_CPU = float(os.getenv("HEALTH_LOAD_CRITICAL_PER_CPU", "2.0"))


def limits(sample: dict[str, float]) -> tuple[tuple[str, float, float], ...]:
    """Return (metric, warn_at, critical_at) for every governed metric."""
    cpu_count = max(1.0, float(sample.get("cpu_count", 1.0)))
    load_metric = "load15" if "load15" in sample else "load1"
    return (
        ("disk_pct", 80.0, 92.0),
        ("mem_pct", 85.0, 95.0),
        (load_metric, max(8.0, cpu_count * LOAD_WARN_PER_CPU), max(16.0, cpu_count * LOAD_CRITICAL_PER_CPU)),
    )
