"""The delivery seam: who gets told, with what evidence, and never twice.

Driven by the same in-memory fakes as the cycle, so the assertion is about the
queue item the bot's worker will actually pop — not about a mock call.
"""

from __future__ import annotations

import json
from typing import Any

from app.domains.premium.bot_queue import QUEUE_KEY, enqueue_watch_alerts
from app.domains.premium.watch_events import diff_snapshots

from .fakes import FakeRedis

TOKEN = "MintBotQueue11111111111111111111111111111111"
SNAPSHOT: dict[str, Any] = {
    "token": TOKEN,
    "chain": "solana",
    "captured_at": "2026-10-23T12:00:00+00:00",
    "lp": {"liquidity_usd": 42_000.0, "locked_pct": 0.0},
    "holders": {"count": 812, "top": [{"wallet": "BagHolder111", "pct": 0.0}]},
    "bundle_wallets": ["BundleA", "BundleB"],
}


def _events() -> list[dict[str, Any]]:
    previous = {
        "lp": {"liquidity_usd": 100_000.0, "locked_pct": 100.0},
        "holders": {"top": [{"wallet": "BagHolder111", "pct": 9.5}]},
        "authority": {"mint": "MintAuthorityXyz"},
        "bundle_wallets": ["BundleA"],
    }
    return diff_snapshots(previous, SNAPSHOT)


async def test_one_item_per_numeric_chat_with_evidence() -> None:
    redis = FakeRedis()
    events = _events()

    queued = await enqueue_watch_alerts(redis, TOKEN, "solana", "TEST", events, SNAPSHOT, ["111", "222", "web:abc"])

    assert queued == 2
    items = [json.loads(raw) for raw in redis.lists[QUEUE_KEY]]
    assert {item["user_id"] for item in items} == {111, 222}
    payload = items[0]["payload"]
    assert payload["token_address"] == TOKEN
    assert payload["changes"], "a change card without a single number is not evidence"
    assert any("Liquidity: $100,000 -> $42,000" in line for line in payload["changes"])
    assert any("9.50%" in line for line in payload["changes"])


async def test_identical_cycles_queue_nothing() -> None:
    redis = FakeRedis()

    queued = await enqueue_watch_alerts(redis, TOKEN, "solana", "TEST", [], SNAPSHOT, ["111"])

    assert queued == 0
    assert QUEUE_KEY not in redis.lists or not redis.lists[QUEUE_KEY]


async def test_unknown_authority_is_not_a_change() -> None:
    previous = {"authority": {}}
    current = {"authority": {"mint": None, "freeze": None}}

    assert diff_snapshots(previous, current) == []
