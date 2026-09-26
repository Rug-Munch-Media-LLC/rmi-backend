"""The ownership marker is a lease, so the plane publishes on every tick.

Regression: the marker took the 7-day incident TTL and was never refreshed or
released, so the first tick won the key for a week and every later tick
returned early at claim(). One message ever, and it looked healthy.
"""

from __future__ import annotations

import pytest

from app.domains.telegram.adminbot.notify.health_keys import (
    LEASE_SECONDS,
    TTL_SECONDS,
)
from app.domains.telegram.adminbot.notify.health_state import HealthStore
from app.domains.telegram.adminbot.notify.incident import Incident
from tests.unit.adminbot_chat._support import FakeRedis

MARKER = "rmi:health:incident:host_health:seen"


@pytest.mark.asyncio
async def test_the_marker_is_leased_not_held_for_the_incident_ttl() -> None:
    """The lease must be far shorter than the incident record it guards."""
    assert LEASE_SECONDS < TTL_SECONDS
    redis = FakeRedis()
    assert await HealthStore(redis).claim() is True
    assert (MARKER, LEASE_SECONDS) in redis.leases


@pytest.mark.asyncio
async def test_a_write_refreshes_the_lease_and_never_extends_it() -> None:
    """Re-saving must not quietly turn the lease back into a week-long lock."""
    redis = FakeRedis()
    store = HealthStore(redis)
    assert await store.claim() is True
    await store.save(Incident(1000.0, 1000.0, 0.0, 1000.0), "firing")
    assert {ttl for key, ttl in redis.leases if key == MARKER} == {LEASE_SECONDS}


@pytest.mark.asyncio
async def test_a_resolved_incident_releases_the_marker() -> None:
    """Resolution frees the lock, so the next incident pages without waiting."""
    redis = FakeRedis()
    store = HealthStore(redis)
    assert await store.claim() is True
    await store.save(Incident(1000.0, 1000.0, 0.0, 1000.0), "resolved")
    assert await HealthStore(redis).claim() is True
