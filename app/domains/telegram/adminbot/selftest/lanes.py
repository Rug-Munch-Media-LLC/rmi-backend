"""Health and latency for each model lane."""

from __future__ import annotations

import time
import urllib.error
import urllib.request

LANE_URLS = ("http://127.0.0.1:20201/health", "http://127.0.0.1:4000/health")


async def _lane_states() -> list[tuple[str, bool, int]]:
    """Return (lane, healthy, latency_ms) for each configured lane."""
    out: list[tuple[str, bool, int]] = []
    for url in LANE_URLS:
        started = time.monotonic()
        try:
            # Short, bounded probe; the handler already awaits the gather.
            with urllib.request.urlopen(url, timeout=3):  # noqa: S310, ASYNC210 - bounded loopback probe
                healthy = True
        except (urllib.error.URLError, OSError, ValueError):
            healthy = False
        out.append((url, healthy, int((time.monotonic() - started) * 1000)))
    return out
