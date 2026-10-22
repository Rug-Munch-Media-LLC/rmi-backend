"""Watch store round-trip against a bytes-returning client.

The app pool is built without ``decode_responses``, so ``hkeys``/``smembers``/
``scan_iter`` yield bytes. ``str(b"solana:token")`` is ``"b'solana:token'"`` —
a different Redis key — which silently emptied ``GET /watch`` in production.
"""

from __future__ import annotations

import pytest

from app.domains.premium.watch_store import (
    add_watch,
    list_watches,
    remove_watch,
    watcher_count,
    watchers_for,
)

pytestmark = pytest.mark.asyncio

TOKEN = "So11111111111111111111111111111111111111112"


async def test_chat_listing_survives_bytes_keys() -> None:
    from tests.unit.domains.premium.fakes import FakeRedis

    store = FakeRedis()  # bytes, like the real pool
    assert await add_watch(store, TOKEN, "solana", "chat-1") == 1

    for_chat = await list_watches(store, "chat-1")
    assert [row["token"] for row in for_chat] == [TOKEN]
    assert for_chat[0]["watchers"] == ["chat-1"]
    assert for_chat[0]["watching"] is True

    every = await list_watches(store)
    assert [row["token"] for row in every] == [TOKEN]
    assert await watchers_for(store, TOKEN, "solana") == ["chat-1"]


async def test_remove_drops_both_structures() -> None:
    from tests.unit.domains.premium.fakes import FakeRedis

    store = FakeRedis()
    await add_watch(store, TOKEN, "solana", "chat-2")
    assert await remove_watch(store, TOKEN, "solana", "chat-2") is True
    assert await watcher_count(store, TOKEN, "solana") == 0
    assert await list_watches(store, "chat-2") == []
