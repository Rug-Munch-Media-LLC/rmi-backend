"""Tests for the adminbot alerts feed — Alertmanager last mile.

Covers the pure functions only (no Redis): formatting, fingerprints, dedupe
window semantics. The BLPOP loop itself is exercised end-to-end via the
``test_alert`` callback in production.
"""

from __future__ import annotations

from app.domains.telegram.adminbot.alerts_feed import (
    QUEUE_KEYS,
    SECURITY_QUEUE,
    SYSTEM_QUEUE,
    AlertDedupe,
    fingerprint,
    format_alert,
)


def _alert(
    status: str = "firing",
    name: str = "ApiDown",
    severity: str = "critical",
    instance: str = "talos:8000",
) -> dict:
    return {
        "status": status,
        "labels": {
            "alertname": name,
            "severity": severity,
            "instance": instance,
            "job": "rmi-api",
        },
        "annotations": {"summary": "API is down", "description": "No response for 5m"},
        "startsAt": "2026-09-16T09:00:00Z",
    }


class TestFormatAlert:
    def test_firing_critical(self) -> None:
        text = format_alert(_alert(), SYSTEM_QUEUE)
        assert "ApiDown" in text
        assert "🚨" in text
        assert "CRITICAL" in text
        assert "API is down" in text
        assert "_alerts:system_" in text

    def test_warning_emoji(self) -> None:
        text = format_alert(_alert(severity="warning"), SYSTEM_QUEUE)
        assert "⚠️" in text

    def test_resolved(self) -> None:
        text = format_alert(_alert(status="resolved"), SECURITY_QUEUE)
        assert "RESOLVED" in text
        assert "✅" in text
        assert "_alerts:security_" in text

    def test_minimal_payload_never_raises(self) -> None:
        text = format_alert({}, SYSTEM_QUEUE)
        assert "Unknown alert" in text


class TestFingerprint:
    def test_stable(self) -> None:
        alert = _alert()
        assert fingerprint(alert) == fingerprint(dict(alert))

    def test_status_independent(self) -> None:
        # Status is excluded from the identity so resolved clears firing.
        assert fingerprint(_alert(status="firing")) == fingerprint(_alert(status="resolved"))

    def test_changes_with_instance(self) -> None:
        assert fingerprint(_alert(instance="a")) != fingerprint(_alert(instance="b"))


class TestDedupe:
    def test_repeat_firing_suppressed(self) -> None:
        dedupe = AlertDedupe(window_seconds=60)
        assert dedupe.should_send(_alert()) is True
        assert dedupe.should_send(_alert()) is False

    def test_resolved_passes_and_clears(self) -> None:
        dedupe = AlertDedupe(window_seconds=60)
        assert dedupe.should_send(_alert()) is True
        assert dedupe.should_send(_alert(status="resolved")) is True
        # After resolution a re-fire must page again.
        assert dedupe.should_send(_alert()) is True

    def test_window_expiry(self) -> None:
        dedupe = AlertDedupe(window_seconds=0)
        assert dedupe.should_send(_alert()) is True
        assert dedupe.should_send(_alert()) is True

    def test_different_instances_not_deduped(self) -> None:
        dedupe = AlertDedupe(window_seconds=60)
        assert dedupe.should_send(_alert(instance="a")) is True
        assert dedupe.should_send(_alert(instance="b")) is True


def test_queue_keys_cover_both_buckets() -> None:
    assert QUEUE_KEYS == [SYSTEM_QUEUE, SECURITY_QUEUE]
