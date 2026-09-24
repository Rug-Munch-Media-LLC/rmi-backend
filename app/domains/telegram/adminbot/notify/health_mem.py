"""Memory used as a percentage, from /proc/meminfo."""

from __future__ import annotations


def mem_pct() -> float:
    """Return memory in use as a percentage, 0.0 when unavailable."""
    values: dict[str, int] = {}
    try:
        with open("/proc/meminfo") as handle:
            for line in handle:
                if ":" not in line:
                    continue
                name, rest = line.split(":", 1)
                values[name] = int("".join(c for c in rest if c.isdigit()) or 0)
    except (OSError, ValueError):
        return 0.0
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    if total <= 0:
        return 0.0
    return (total - available) / total * 100
