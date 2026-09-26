"""Publishing is debounced across workers, using the store as a DI seam."""

from __future__ import annotations

import pytest

from app.domains.telegram.adminbot.notify import health_publish
from tests.unit.adminbot_chat._support import MemoryStore, ladder_rating


@pytest.mark.asyncio
async def test_first_breach_pages_and_repeats_do_not() -> None:
    """One incident, one firing message, then silence until the backstop."""
    store, outbox = MemoryStore(), []
    publish = health_publish.publish
    for _ in range(3):
        await publish(store=store, rating=ladder_rating("warn"), outbox=outbox)
    assert [a["status"] for a in outbox] == ["firing"]
    assert outbox[0]["annotations"]["summary"].startswith("Host warn:")


@pytest.mark.asyncio
async def test_recovery_is_sent_once() -> None:
    """The operator gets one clear end to the incident, not one per sample."""
    store, outbox = MemoryStore(), []
    publish, ladder = health_publish.publish, ladder_rating("warn", "ok")
    # 8 ticks is 40 min, past the 30 min quiet window a recovery waits for.
    for _ in range(8):
        await publish(store=store, rating=ladder, outbox=outbox)
    assert [a["status"] for a in outbox] == ["firing", "resolved"]


@pytest.mark.asyncio
async def test_ladder_walk_yields_one_incident() -> None:
    """warn -> crit -> warn inside the debounce window is not a storm."""
    store, outbox = MemoryStore(), []
    publish = health_publish.publish
    ladder = ladder_rating("warn", "critical", "warn", "critical")
    for _ in range(4):
        await publish(store=store, rating=ladder, outbox=outbox)
    assert len(outbox) == 2  # the first breach, then the escalation
