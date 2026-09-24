"""Publish a health alert when a host threshold is crossed."""

from __future__ import annotations

import os
import time

from .health_read import sample
from .health_severity import rate
from .payload import build
from .queues import SYSTEM, push

REPEAT_SECONDS = int(os.getenv("HEALTH_ALERT_REPEAT_SECONDS", "14400"))
_last_severity: str | None = None
_last_sent_at = 0.0


def reset_state() -> None:
    """Reset the in-process transition state (used by tests)."""
    global _last_severity, _last_sent_at
    _last_severity = None
    _last_sent_at = 0.0


async def publish() -> tuple[str, str]:
    """Publish only on severity transitions, plus a long repeat interval.

    A health sample is still returned to the caller, but repeated non-ok
    samples do not enqueue a new alert every five minutes. A recovery is sent
    once so the operator gets a clear end to the incident.
    """
    global _last_severity, _last_sent_at

    severity, reason = rate(sample())
    now = time.monotonic()

    if severity == "ok":
        if _last_severity not in (None, "ok"):
            await push(
                SYSTEM,
                build(
                    "HostHealth",
                    "ok",
                    "Host recovered",
                    "all monitored thresholds returned to normal",
                    status="resolved",
                ),
            )
            _last_severity = "ok"
            _last_sent_at = now
        return severity, reason

    if _last_severity == severity and (now - _last_sent_at) < REPEAT_SECONDS:
        return severity, reason

    await push(
        SYSTEM,
        build("HostHealth", severity, f"Host {severity}: {reason}", "threshold crossed on this host"),
    )
    _last_severity = severity
    _last_sent_at = now
    return severity, reason
