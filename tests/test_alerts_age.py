"""Max-age gate: a backlogged alert is suppressed, not delivered late.

The queue item's ``queuedAt`` (stamped at push) is the age basis. A live
incident keeps its original ``startsAt`` across Alertmanager repeats and must
still be delivered, so startsAt alone must never suppress.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domains.telegram.adminbot.alerts_age import MAX_ALERT_AGE_SECONDS, is_stale

NOW = datetime(2026, 10, 22, 12, 54, tzinfo=UTC)


def _alert(**extra):
    return {"status": "firing", "labels": {"alertname": "HostHealth"}, **extra}


def test_fresh_item_is_delivered():
    fresh = (NOW - timedelta(seconds=30)).isoformat()
    assert is_stale(_alert(queuedAt=fresh), now=NOW) is False


def test_week_old_item_is_dropped():
    old = (NOW - timedelta(days=5)).isoformat()
    assert is_stale(_alert(queuedAt=old), now=NOW) is True


def test_boundary_respects_window():
    edge = (NOW - timedelta(seconds=MAX_ALERT_AGE_SECONDS)).isoformat()
    past = (NOW - timedelta(seconds=MAX_ALERT_AGE_SECONDS + 1)).isoformat()
    assert is_stale(_alert(queuedAt=edge), now=NOW) is False
    assert is_stale(_alert(queuedAt=past), now=NOW) is True


def test_old_startsat_alone_does_not_suppress_ongoing_incident():
    old_start = (NOW - timedelta(days=5)).isoformat()
    assert is_stale(_alert(startsAt=old_start), now=NOW) is False


def test_undated_and_malformed_payloads_pass():
    assert is_stale(_alert(), now=NOW) is False
    assert is_stale(_alert(queuedAt="not-a-timestamp"), now=NOW) is False


def test_zulu_suffix_is_parsed():
    zulu = (NOW - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert is_stale(_alert(queuedAt=zulu), now=NOW) is True
