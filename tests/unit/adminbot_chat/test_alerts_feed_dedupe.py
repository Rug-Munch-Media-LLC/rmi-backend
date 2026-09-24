"""Tests for durable adminbot alert deduplication."""

from __future__ import annotations

import pytest

from app.domains.telegram.adminbot import alerts_feed


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int) -> bool:
        self.values[key] = value
        return True

    async def delete(self, key: str) -> int:
        return int(self.values.pop(key, None) is not None)


@pytest.mark.asyncio
async def test_redis_dedupe_survives_repeated_alert_and_allows_state_change() -> None:
    redis = FakeRedis()
    firing = {
        "status": "firing",
        "labels": {"alertname": "HostHealth", "severity": "warning"},
    }

    assert await alerts_feed.redis_should_send(redis, firing) is True
    assert await alerts_feed.redis_should_send(redis, dict(firing)) is False

    critical = {
        "status": "firing",
        "labels": {"alertname": "HostHealth", "severity": "critical"},
    }
    assert await alerts_feed.redis_should_send(redis, critical) is True

    resolved = {
        "status": "resolved",
        "labels": {"alertname": "HostHealth", "severity": "critical"},
    }
    assert await alerts_feed.redis_should_send(redis, resolved) is True
    assert await alerts_feed.redis_should_send(redis, critical) is True
