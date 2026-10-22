"""One watch cycle end to end, against the shared in-memory fakes.

The cycle is the only place in the premium layer that writes. Driving it with
:mod:`tests.unit.domains.premium.fakes` (not mocks) means the assertions are
about real behaviour: the baseline is stored for the next cycle, an unwatched
token never fills the alert table, and a repeated identical event is absorbed
by the alert store's ``ON CONFLICT`` instead of paging twice.
"""

from __future__ import annotations

import json
from typing import Any

from app.domains.premium.watch_cycle import run_watch_cycle
from app.domains.premium.watch_store import add_watch

from .fakes import FakeRedis, FakeSession

TOKEN = "MintWatchCycle111111111111111111111111111111"
SNAPSHOT_KEY = f"premium:snapshot:solana:{TOKEN}"


def _payload(*, locked_pct: float | None, liquidity_usd: float | None = 100_000.0) -> dict[str, Any]:
    return {
        "token": TOKEN,
        "chain": "solana",
        "scanned_at": "2026-10-23T00:00:00+00:00",
        "liquidity": {"usd": liquidity_usd, "locked_pct": locked_pct},
        "top_holders": [{"address": "HolderOne", "pct": 12.0}],
        "holder_count": 400,
    }


async def test_first_cycle_stores_a_baseline_and_emits_nothing() -> None:
    redis, session = FakeRedis(), FakeSession()
    await add_watch(redis, TOKEN, "solana", "chat-1")

    result = await run_watch_cycle(session, redis, TOKEN, "solana", _payload(locked_pct=100.0))

    assert result["baseline"] is False
    assert result["watchers_n"] == 1
    assert result["events"] == []
    assert result["alert_rows"] == 0
    assert session.rows == {}
    assert json.loads(redis.kv[SNAPSHOT_KEY])["lp"]["locked_pct"] == 100.0


async def test_unlock_on_the_next_cycle_writes_one_critical_alert() -> None:
    redis, session = FakeRedis(), FakeSession()
    await add_watch(redis, TOKEN, "solana", "chat-1")
    await run_watch_cycle(session, redis, TOKEN, "solana", _payload(locked_pct=100.0))

    result = await run_watch_cycle(session, redis, TOKEN, "solana", _payload(locked_pct=0.0))

    assert result["baseline"] is True
    assert [event["kind"] for event in result["events"]] == ["lp_unlocked"]
    assert result["alert_rows"] == 1
    assert result["inserted"] == 1
    assert result["watchers"] == ["chat-1"]
    row = next(iter(session.rows.values()))
    assert row["alert_type"] == "lp_unlocked"
    assert row["severity"] == "critical"
    assert row["token_id"] == TOKEN
    assert row["alert_id"].startswith("alert_")
    assert json.loads(row["evidence"])["watchers_n"] == 1


async def test_an_unchanged_token_produces_no_rows() -> None:
    redis, session = FakeRedis(), FakeSession()
    await add_watch(redis, TOKEN, "solana", "chat-1")
    payload = _payload(locked_pct=100.0)
    await run_watch_cycle(session, redis, TOKEN, "solana", payload)

    result = await run_watch_cycle(session, redis, TOKEN, "solana", payload)

    assert result["events"] == []
    assert session.rows == {}


async def test_an_unwatched_token_refreshes_its_baseline_without_rows() -> None:
    redis, session = FakeRedis(), FakeSession()
    await run_watch_cycle(session, redis, TOKEN, "solana", _payload(locked_pct=100.0))

    result = await run_watch_cycle(session, redis, TOKEN, "solana", _payload(locked_pct=0.0))

    assert result["watchers_n"] == 0
    assert len(result["events"]) == 1  # the change is still observed
    assert result["alert_rows"] == 0  # but an unwatched token never fills the table
    assert session.rows == {}


async def test_a_repeated_identical_event_is_absorbed_by_the_alert_store() -> None:
    redis, session = FakeRedis(), FakeSession()
    await add_watch(redis, TOKEN, "solana", "chat-1")
    await run_watch_cycle(session, redis, TOKEN, "solana", _payload(locked_pct=100.0))
    locked = redis.kv[SNAPSHOT_KEY]
    await run_watch_cycle(session, redis, TOKEN, "solana", _payload(locked_pct=0.0))

    # Replay the same transition: restore the locked baseline and unlock again.
    redis.kv[SNAPSHOT_KEY] = locked
    replay = await run_watch_cycle(session, redis, TOKEN, "solana", _payload(locked_pct=0.0))

    assert replay["alert_rows"] == 1
    assert replay["inserted"] == 0  # ON CONFLICT DO NOTHING — no second page
    assert len(session.rows) == 1
