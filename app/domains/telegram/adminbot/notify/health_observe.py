"""Observe one health sample: did the pending state change?

The debounce anchor is ``since`` — when the current pending state began —
never ``last_seen``. Sliding ``last_seen`` must not also reset how long a
breach has been standing, or a condition that never clears would slide
forever and never settle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .incident import Incident


def observe(incident: Incident, severity: str, now: float) -> bool:
    """Record the sample and return True when the pending state changed.

    The caller uses that to tell a genuine firing -> resolved transition
    (news) from a repeat resolve (the flapping echo), and to reset the
    debounce anchor so a new state has to earn its own wait.
    """
    firing = severity != "ok"
    transitioned = firing != incident.since_firing
    incident.last_seen = now
    if transitioned:
        incident.since = now
        incident.since_firing = firing
    return transitioned
