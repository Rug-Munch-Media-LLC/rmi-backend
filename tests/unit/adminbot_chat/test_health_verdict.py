"""The ported suppression model decides an incident lifecycle, pure."""

from __future__ import annotations

from app.domains.telegram.adminbot.notify.health_verdict import decide
from app.domains.telegram.adminbot.notify.incident import Incident

REPEAT = 14400


def _state(**kwargs: float) -> Incident:
    """Return an incident stamped for a repeat-after window of REPEAT."""
    base = {"last_seen": 0.0, "last_sent": 1000.0, "last_escalation": 0.0, "since": 1000.0}
    return Incident(**(base | kwargs))


def test_first_sighting_pages() -> None:
    assert decide(Incident(0.0, 0.0, 0.0, 0.0), "warn", "firing", 1000.0, REPEAT, True) is True


def test_escalation_pages_immediately() -> None:
    """A crit after an announced warn is always worth reporting."""
    assert decide(_state(last_escalation=0.0), "critical", "firing", 1100.0, REPEAT, False) is True


def test_de_escalation_is_silent() -> None:
    """Flapping back down to an announced level is not news a second time."""
    assert decide(_state(last_escalation=1.0), "warn", "firing", 1100.0, REPEAT, False) is False


def test_steady_repeat_waits_for_backstop() -> None:
    assert decide(_state(last_escalation=0.0), "warn", "firing", 1100.0, REPEAT, False) is False


def test_steady_repeat_fires_after_backstop() -> None:
    """A long-lived incident is never silently forgotten."""
    assert decide(_state(last_escalation=0.0), "warn", "firing", 1000.0 + REPEAT, REPEAT, False) is True


def test_first_resolve_is_news() -> None:
    assert decide(_state(last_escalation=1.0), "ok", "resolved", 1100.0, REPEAT, True) is True


def test_repeat_resolve_is_the_flapping_echo() -> None:
    assert decide(_state(last_escalation=0.0), "ok", "resolved", 1100.0, REPEAT, False) is False
