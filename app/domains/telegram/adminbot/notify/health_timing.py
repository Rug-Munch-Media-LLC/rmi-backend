"""Debounce and hysteresis windows for the host-health alert plane.

``watch.py`` samples every 300s, so each number below is a whole number of
samples:

* ``settle`` 900s (3 samples) before a breach pages. 30 min (6 samples)
  before a recovery is announced. A warn that clears into crit and back
  inside those windows is one incident, not three messages.
* ``repeat`` 14400s (4h) backstop so a long-lived incident is never
  silently forgotten; this is the previous REPEAT_SECONDS default, kept.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Timing:
    """The three windows, injected so the decision stays clock-independent."""

    settle: int
    clear: int
    repeat: int

    def window(self, firing: bool) -> int:
        """Return the wait that applies to the pending state."""
        return self.settle if firing else self.clear


DEFAULT = Timing(
    settle=int(os.getenv("HEALTH_ALERT_SETTLE_SECONDS", "900")),
    clear=int(os.getenv("HEALTH_ALERT_CLEAR_SECONDS", "1800")),
    repeat=int(os.getenv("HEALTH_ALERT_REPEAT_SECONDS", "14400")),
)
