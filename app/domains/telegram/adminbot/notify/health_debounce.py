"""Debounce/hysteresis: is this incident settled enough to announce?

Two waits, both from ``Timing``. A breach must persist for ``settle``
before it pages, and a recovery must hold for ``clear`` before it is
announced. Either wait being 0 means the caller opted out of debouncing.
A first sighting is never held back: if the operator has never been told
about this incident, page now.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .health_timing import Timing
    from .incident import Incident


def settled(incident: Incident, now: float, timing: Timing) -> bool:
    """True when the pending state has stood long enough to announce it."""
    if not incident.last_sent:
        return True
    return (now - incident.since) >= timing.window(incident.since_firing)
