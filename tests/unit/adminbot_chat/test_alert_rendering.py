"""End-to-end: what the operator actually receives for a nasty alert.

The unit tests cover the escaping rules; this pins the rendered output, which
is where both shipped defects showed up — a literal ``\\-`` in the timestamp
and a 400 from an escape the legacy parser does not recognise.
"""

from __future__ import annotations

from app.domains.telegram.adminbot.alerts_feed import format_alert
from app.domains.telegram.adminbot.tg_markdown import esc, unesc

NASTY = "warn_disk_85% (>= 80) *bold* [bracket] `tick` > quote # hash | pipe"
STARTED = "2026-09-27T10:00:00+00:00"


def _alert() -> dict:
    return {
        "status": "firing",
        "labels": {"alertname": "ValkeyHeadroom", "severity": "warning", "instance": "rmi-valkey"},
        "annotations": {"summary": NASTY, "description": "used_memory > maxmemory (>= 90)"},
        "startsAt": STARTED,
    }


def test_timestamp_is_not_escaped_inside_its_code_span() -> None:
    """Nothing is parsed inside backticks, so a backslash renders literally."""
    assert STARTED in format_alert(_alert(), "rmi:alerts:system")


def test_no_invalid_escapes_reach_the_message() -> None:
    """``\\-`` and ``\\:`` are the 400s; the legacy parser accepts neither."""
    text = format_alert(_alert(), "rmi:alerts:system")
    assert "\\-" not in text
    assert "\\:" not in text
    assert "\\+" not in text


def test_delimiters_are_still_escaped() -> None:
    assert esc(NASTY) in format_alert(_alert(), "rmi:alerts:system")


def test_the_plain_text_fallback_renders_clean() -> None:
    """A retry must not show the operator ``\\(15m\\)`` instead of ``(15m)``."""
    assert "(>= 80)" in unesc(format_alert(_alert(), "rmi:alerts:system"))
