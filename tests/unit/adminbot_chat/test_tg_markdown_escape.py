"""Alert text is safe for Telegram's Markdown parse mode (the HTTP 400 fix).

"""

from __future__ import annotations

from app.domains.telegram.adminbot.alerts_feed import format_alert
from app.domains.telegram.adminbot.tg_markdown import esc

# Every character the legacy Markdown parser treats as an entity delimiter.
NASTY = "warn_disk_85% (>= 80) *bold* [bracket] `tick` ~tilde~ > quote # hash | pipe {brace}"


def test_esc_escapes_every_delimiter() -> None:
    for char in "`*_[]()~>":
        assert esc(char) == f"\\{char}"
    assert esc("plain text 85%") == "plain text 85%"
    assert esc("> quoted").startswith("\\>")


def test_backslashes_are_never_doubled() -> None:
    """A double escape would render a literal backslash on screen."""
    assert esc("a\\b") == "a\\\\b"
    assert esc(esc("x_y")) == "x\\\\\\_y"


def test_formatted_alert_carries_no_raw_delimiter() -> None:
    """The real regression: a ValkeyHeadroom-style payload must be safe."""
    text = format_alert(
        {
            "status": "firing",
            "labels": {
                "alertname": "ValkeyHeadroom",
                "severity": "warning",
                "instance": "rmi-valkey",
                "job": "valkey(headroom)",
            },
            "annotations": {
                "summary": NASTY,
                "description": "used_memory > maxmemory (>= 90) on rmi-valkey",
            },
            "startsAt": "2026-09-26T18:00:00+00:00",
        },
        "rmi:alerts:system",
    )
    # backticks left are the formatter's own code-span fences.
    assert esc(NASTY) in text
    assert NASTY not in text
    assert text.count("`") == text.count("\\`") + 8
