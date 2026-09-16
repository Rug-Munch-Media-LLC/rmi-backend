"""Tests for the adminbot self-test card (pure formatting)."""

from __future__ import annotations

from app.domains.telegram.adminbot.selftest import (
    ALERT_QUEUES,
    LANES,
    SERVICE_UNITS,
    format_selftest,
)


def _results(
    units: list | None = None,
    lanes: list | None = None,
    queues: list | None = None,
    engines: list | None = None,
) -> dict:
    return {
        "units": units if units is not None else [("rmi-backend", "active"), ("rugmunchbot", "active")],
        "lanes": lanes if lanes is not None else [("defiant-9b :8009", True, 12)],
        "queues": queues if queues is not None else [("rmi:alerts:system", 0)],
        "engines": engines if engines is not None else ["defiant", "cline"],
        "engines_total": 8,
        "resources": {"disk_used_pct": 42, "disk_free_gb": 100.0, "mem_avail_gb": 14.2},
    }


def test_all_go() -> None:
    text = format_selftest(_results())
    assert "ALL SYSTEMS GO" in text
    assert "✅ rmi-backend" in text


def test_downed_service_counts_as_issue() -> None:
    text = format_selftest(_results(units=[("rmi-backend", "failed")]))
    assert "❌ rmi-backend" in text
    assert "1 issue" in text


def test_parked_heavy_is_not_an_issue() -> None:
    text = format_selftest(_results(lanes=[("ff711-heavy :8014", False, -1)]))
    assert "💤 ff711-heavy" in text
    assert "ALL SYSTEMS GO" in text


def test_dead_lane_is_an_issue() -> None:
    text = format_selftest(_results(lanes=[("defiant-9b :8009", False, -1)]))
    assert "❌ defiant-9b" in text


def test_queue_backlog_warns() -> None:
    text = format_selftest(_results(queues=[("rmi:alerts:system", 40)]))
    assert "stalled" in text
    assert "issue" in text


def test_queue_redis_down() -> None:
    text = format_selftest(_results(queues=[("rmi:alerts:system", -1)]))
    assert "redis unreachable" in text


def test_constants_cover_empire() -> None:
    assert "llm-guard" in SERVICE_UNITS
    assert "hermes-gateway" in SERVICE_UNITS
    assert any("8009" in label for label, _ in LANES)
    assert "rmi:alerts:security" in ALERT_QUEUES
