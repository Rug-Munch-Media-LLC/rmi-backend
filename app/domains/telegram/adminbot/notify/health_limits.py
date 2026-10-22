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
        # 2026-10-22: warn 80 -> 85. Measured steady state is 77 % and the
        # nightly corpus-backup + qdrant-b2-offload write ~58 GB before the B2
        # upload and prune, so 80-82 % is reached every night and paged a human
        # for a window that recovers on its own. 85 % (18 % free = ~360 GB on
        # this 2 TB root) still leaves a full day of headroom over the 58 GB
        # write, and it matches HighDiskUsage in /etc/prometheus/alert_rules.yml
        # so one host does not report two different disk answers. Critical stays
        # at 92 % (~160 GB free = 2.7 days at the observed write rate).
        ("disk_pct", 85.0, 92.0),
        ("mem_pct", 85.0, 95.0),
        (load_metric, max(8.0, cpu_count * LOAD_WARN_PER_CPU), max(16.0, cpu_count * LOAD_CRITICAL_PER_CPU)),
    )
