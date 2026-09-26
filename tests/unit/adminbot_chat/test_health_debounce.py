"""Debounce absorbs oscillation; a brief excursion never pages."""

from __future__ import annotations

from app.domains.telegram.adminbot.notify.health_debounce import settled
from app.domains.telegram.adminbot.notify.health_observe import observe
from app.domains.telegram.adminbot.notify.health_timing import Timing
from app.domains.telegram.adminbot.notify.incident import Incident

TIMING = Timing(settle=900, clear=1800, repeat=14400)


def test_first_breach_is_never_held_back() -> None:
    """An operator who has never been told gets told now."""
    assert settled(Incident(0.0, 0.0, 0.0, 0.0), 1000.0, TIMING) is True


def test_brief_excursion_does_not_page() -> None:
    """A single sample above the line is not an incident."""
    state = Incident(1000.0, 1000.0, 0.0, 1000.0)
    observe(state, "warn", 1000.0)
    assert settled(state, 1200.0, TIMING) is False


def test_breach_pages_once_it_persists() -> None:
    state = Incident(1000.0, 1000.0, 0.0, 1000.0)
    observe(state, "warn", 1000.0)
    assert settled(state, 1000.0 + 900, TIMING) is True


def test_ladder_walk_is_one_incident() -> None:
    """ok -> warn -> crit -> ok with re-samples never slides the anchor.

    Each observe() call re-samples the same state, so a condition that never
    clears must still settle instead of sliding forever. That is what stops
    the load oscillating across the warn/crit line from paging every cycle.
    """
    state = Incident(0.0, 1000.0, 0.0, 1000.0)
    for offset in (300, 600, 900):
        assert observe(state, "warn", 1000.0 + offset) is False
    assert settled(state, 1900.0, TIMING) is True


def test_recovery_needs_the_longer_quiet_window() -> None:
    state = Incident(1000.0, 1000.0, 1.0, 1000.0)
    observe(state, "ok", 1000.0)
    assert settled(state, 1000.0 + 900, TIMING) is False
    assert settled(state, 1000.0 + 1800, TIMING) is True
