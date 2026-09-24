"""Read one health sample from /proc and the filesystem."""

from __future__ import annotations

import shutil

from .health_mem import mem_pct


def sample() -> dict[str, float]:
    """Return load1, disk_pct and mem_pct for this host.

    Reads only /proc and statvfs, so a health check adds no meaningful load
    even on a saturated box.
    """
    load1 = 0.0
    try:
        with open("/proc/loadavg") as handle:
            load1 = float(handle.read().split()[0])
    except (OSError, ValueError, IndexError):
        pass
    usage = shutil.disk_usage("/")
    return {
        "load1": load1,
        "disk_pct": usage.used / usage.total * 100 if usage.total else 0.0,
        "mem_pct": mem_pct(),
    }
