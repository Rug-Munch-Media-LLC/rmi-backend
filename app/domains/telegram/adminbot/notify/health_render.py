"""Render the host-health alert body for ``payload.build``.

Split out of the publisher so the decision (debounce + suppression) and the
wording stay independently readable, and so this module keeps one function.
"""

from __future__ import annotations

from typing import Any

from .payload import build


def render(severity: str, status: str, reason: str) -> dict[str, Any]:
    """Return the alert dict for a breach or a recovery.

    A recovery carries severity ``info`` and ``status='resolved'``, which is
    what the alerts feed keys its ✅ RESOLVED line off, and what clears the
    dedupe entry so a later re-fire pages again.
    """
    if status == "resolved":
        return build("HostHealth", "info", "Host recovered", "all monitored thresholds returned to normal", "resolved")
    summary = f"Host {severity}: {reason}"
    return build("HostHealth", severity, summary, "threshold crossed on this host", "firing")
