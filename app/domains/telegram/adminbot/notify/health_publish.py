"""Publish a health alert when a host threshold is crossed."""

from __future__ import annotations

from .health_read import sample
from .health_severity import rate
from .payload import build
from .queues import SYSTEM, push


async def publish() -> tuple[str, str]:
    """Sample the host and alert when it is warn or critical.

    Returns (severity, reason) so callers and tests can observe the decision
    without waiting on delivery. An ok sample pushes nothing.
    """
    severity, reason = rate(sample())
    if severity == "ok":
        return severity, reason
    await push(
        SYSTEM,
        build("HostHealth", severity, f"Host {severity}: {reason}", "threshold crossed on this host"),
    )
    return severity, reason
