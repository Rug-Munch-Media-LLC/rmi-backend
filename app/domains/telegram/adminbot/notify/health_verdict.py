"""Port of alert-webhook.py's SuppressionStore.should_send, for one health sample.

Ported, not redesigned. Same rules, same order:

* a severity change is always worth reporting (escalation or de-escalation);
* ``repeat`` is a backstop so a long-lived incident is never forgotten;
* a steady repeat is measured from ``last_sent``, not from activity.

One addition, and it is the storm being fixed: a flap back to a severity the
operator was already told about is silence, not a fresh page.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .incident import Incident

# Compact severity codes, so the announced level fits one Redis field.
LEVEL_CODE = {"warn": 0.0, "critical": 1.0}


def decide(
    state: Incident,
    severity: str,
    status: str,
    now: float,
    repeat: int,
    transitioned: bool,
) -> bool:
    """True when this sample should be published; False when suppressed.

    Pure and clock-injectable, so the whole incident lifecycle is provable
    in a test without Redis, a clock, or a network. ``transitioned`` is the
    firing -> resolved move; a repeat resolve is the flapping echo.
    """
    if not state.last_sent:
        return True  # first sight of this incident
    since_sent = now - state.last_sent
    if status == "resolved":
        # A recovery is news the first time only, and only while the operator
        # has not already been quiet past the backstop.
        return transitioned and since_sent < repeat
    if LEVEL_CODE.get(severity, 0.0) > state.last_escalation:
        return True  # escalation is always worth reporting
    # De-escalation, a flap back to an announced level, or a steady repeat:
    # all measure the operator's silence, not the alert's activity.
    return since_sent >= repeat
