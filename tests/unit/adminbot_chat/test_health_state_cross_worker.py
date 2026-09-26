"""The health incident state is shared by every worker, and survives restart.

A FakeRedis stands in for the real server: the shared, cross-process object
that the module-level globals were not. Two stores bound to it model two
gunicorn workers; a third store built later models a worker restart.
"""

from __future__ import annotations

import pytest

from app.domains.telegram.adminbot.notify.health_state import HealthStore
from app.domains.telegram.adminbot.notify.incident import Incident
from tests.unit.adminbot_chat._support import BrokenRedis, FakeRedis


@pytest.mark.asyncio
async def test_only_one_worker_may_own_the_incident() -> None:
    """SET NX is the cross-worker guarantee: the second worker is refused."""
    redis = FakeRedis()
    assert await HealthStore(redis).claim() is True
    assert await HealthStore(redis).claim() is False


@pytest.mark.asyncio
async def test_state_survives_a_worker_restart() -> None:
    """A new worker on the same Redis reads the previous worker's decision."""
    redis = FakeRedis()
    first = HealthStore(redis)
    assert await first.claim() is True
    await first.save(Incident(1000.0, 1000.0, 1.0, 1000.0), "firing")

    recovered = await HealthStore(redis).load(2000.0)
    assert (recovered.last_sent, recovered.last_escalation) == (1000.0, 1.0)
    assert recovered.since_firing is True


@pytest.mark.asyncio
async def test_store_failure_never_silences_the_plane() -> None:
    """Fail-open: a broken Redis must not swallow an alert."""
    store = HealthStore(BrokenRedis())
    assert await store.claim() is True
    assert (await store.load(5.0)).last_sent == 0.0
