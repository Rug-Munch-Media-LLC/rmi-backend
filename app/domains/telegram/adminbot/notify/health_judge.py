"""Compose the debounce and the suppression verdict into one decision.

Split out so the publisher reads as: sample, claim, judge, emit, save.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .health_debounce import settled
from .health_observe import observe
from .health_verdict import decide

if TYPE_CHECKING:
    from .health_timing import Timing
    from .incident import Incident


def judge(state: Incident, severity: str, status: str, now: float, timing: Timing) -> bool:
    """True when this sample is news; observing it is what debounces the next.

    Order matters: ``observe`` records the sample and reports a transition, so
    it must run even when ``settled`` says the state is too young to announce.
    ``decide`` handles the settled case, including escalation and the backstop.
    """
    return settled(state, now, timing) and decide(
        state, severity, status, now, timing.repeat, observe(state, severity, now)
    )
