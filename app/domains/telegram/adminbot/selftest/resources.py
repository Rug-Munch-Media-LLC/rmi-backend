"""Disk and memory headroom for this host."""

from __future__ import annotations

import shutil


def _resources() -> dict[str, object]:
    """Return disk usage and available memory."""
    disk = shutil.disk_usage("/")
    mem_avail_gb = None
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    mem_avail_gb = round(int(line.split()[1]) / 1048576, 1)
                    break
    except OSError:
        mem_avail_gb = None
    return {
        "disk_used_pct": round(disk.used / disk.total * 100),
        "disk_free_gb": round(disk.free / 1073741824, 1),
        "mem_avail_gb": mem_avail_gb,
    }
