"""Tests for edge-triggered host-health publishing."""

from __future__ import annotations

import pytest

from app.domains.telegram.adminbot.notify import health_publish


@pytest.fixture(autouse=True)
def reset_health_state():
    health_publish.reset_state()
    yield
    health_publish.reset_state()


@pytest.mark.asyncio
async def test_repeated_same_severity_is_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    pushed: list[dict] = []

    async def fake_push(_queue: str, alert: dict) -> None:
        pushed.append(alert)

    monkeypatch.setattr(health_publish, "sample", lambda: {"load15": 25.0, "cpu_count": 16.0})
    monkeypatch.setattr(health_publish, "rate", lambda _sample: ("warn", "load is elevated"))
    monkeypatch.setattr(health_publish, "push", fake_push)

    assert await health_publish.publish() == ("warn", "load is elevated")
    assert await health_publish.publish() == ("warn", "load is elevated")
    assert len(pushed) == 1


@pytest.mark.asyncio
async def test_recovery_is_sent_once(monkeypatch: pytest.MonkeyPatch) -> None:
    pushed: list[dict] = []
    levels = iter([("warn", "load is elevated"), ("ok", ""), ("ok", "")])

    async def fake_push(_queue: str, alert: dict) -> None:
        pushed.append(alert)

    monkeypatch.setattr(health_publish, "sample", dict)
    monkeypatch.setattr(health_publish, "rate", lambda _sample: next(levels))
    monkeypatch.setattr(health_publish, "push", fake_push)

    await health_publish.publish()
    await health_publish.publish()
    await health_publish.publish()

    assert [alert["status"] for alert in pushed] == ["firing", "resolved"]
