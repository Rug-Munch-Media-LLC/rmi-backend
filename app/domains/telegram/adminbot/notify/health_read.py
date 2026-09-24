"""Read one health sample from /proc and the filesystem."""

from __future__ import annotations

import os
import shutil

from .health_mem import mem_pct


def sample() -> dict[str, float]:
    """Return smoothed load, CPU count, disk usage and memory usage.

    ``load15`` is used for alerting so a short-lived embed/API burst does not
    page the operator; ``load1`` remains available for diagnostics and
    backwards-compatible callers.
    """
    load1 = 0.0
    load15 = 0.0
    try:
        with open("/proc/loadavg") as handle:
            fields = handle.read().split()
        load1 = float(fields[0])
        load15 = float(fields[2]) if len(fields) > 2 else load1
    except (OSError, ValueError, IndexError):
        pass
    usage = shutil.disk_usage("/")
    return {
        "load1": load1,
        "load15": load15,
        "cpu_count": float(max(os.cpu_count() or 1, 1)),
        "disk_pct": usage.used / usage.total * 100 if usage.total else 0.0,
        "mem_pct": mem_pct(),
    }
