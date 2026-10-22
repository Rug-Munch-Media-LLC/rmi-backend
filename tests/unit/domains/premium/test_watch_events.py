"""Diff: exactly four event kinds, and only real transitions.

The hard requirement is the negative one: two equal states emit nothing, and
two *equally unknown* states emit nothing either. A diff that fired on
``None`` would page an operator every cycle about a provider it cannot reach,
which is how alert channels get muted.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.domains.premium.watch_events import diff_snapshots


def _snapshot(
    *,
    mint: str | None = None,
    freeze: str | None = None,
    owner: str | None = None,
    locked_pct: float | None = None,
    liquidity_usd: float | None = None,
    holders: list[dict[str, Any]] | None = None,
    bundle_wallets: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "token": "MintA",
        "chain": "solana",
        "captured_at": "2026-10-23T00:00:00+00:00",
        "authority": {"mint": mint, "freeze": freeze, "owner": owner},
        "lp": {
            "liquidity_usd": liquidity_usd,
            "locked_pct": locked_pct,
            "unlocked": None if locked_pct is None else locked_pct == 0.0,
            "owner": None,
        },
        "holders": {"count": None, "top": holders or [], "dev_pct": None},
        "bundle_wallets": bundle_wallets or [],
    }


def test_first_cycle_is_a_baseline_not_an_event() -> None:
    assert diff_snapshots(None, _snapshot(locked_pct=100, holders=[{"wallet": "W1", "pct": 9}])) == []


def test_identical_snapshots_emit_nothing() -> None:
    state = _snapshot(mint=None, locked_pct=100, holders=[{"wallet": "W1", "pct": 9}])

    assert diff_snapshots(state, state) == []


def test_equally_unknown_states_emit_nothing() -> None:
    unknown = _snapshot()

    assert diff_snapshots(unknown, _snapshot()) == []


def test_holder_disappearing_is_a_cash_out() -> None:
    events = diff_snapshots(
        _snapshot(holders=[{"wallet": "Whale1", "pct": 12.0}]),
        _snapshot(holders=[]),
    )

    assert [event["kind"] for event in events] == ["cashed_out"]
    assert events[0]["wallet"] == "Whale1"
    assert events[0]["before_pct"] == 12.0
    assert events[0]["after_pct"] is None
    assert events[0]["detail"] == "holder gone from top holders"


def test_collapsed_share_is_a_cash_out() -> None:
    events = diff_snapshots(
        _snapshot(holders=[{"wallet": "Whale1", "pct": 12.0}]),
        _snapshot(holders=[{"wallet": "Whale1", "pct": 2.0}]),
    )

    assert [event["kind"] for event in events] == ["cashed_out"]
    assert events[0]["detail"] == "share collapsed"


def test_minor_trimming_is_not_a_cash_out() -> None:
    assert (
        diff_snapshots(
            _snapshot(holders=[{"wallet": "Whale1", "pct": 12.0}]),
            _snapshot(holders=[{"wallet": "Whale1", "pct": 11.0}]),
        )
        == []
    )


def test_dust_holder_leaving_is_not_a_cash_out() -> None:
    assert (
        diff_snapshots(
            _snapshot(holders=[{"wallet": "Dust", "pct": 0.2}]),
            _snapshot(holders=[]),
        )
        == []
    )


def test_lock_removed_is_lp_unlocked() -> None:
    events = diff_snapshots(_snapshot(locked_pct=100), _snapshot(locked_pct=0.0))

    assert [event["kind"] for event in events] == ["lp_unlocked"]
    assert events[0]["before_pct"] == 100.0
    assert events[0]["after_pct"] == 0.0
    assert events[0]["detail"] == "lock removed"


def test_unknown_lock_after_known_lock_is_not_an_unlock() -> None:
    # Provider degraded: the lock is unreported, not removed. Silence is correct
    # here because the snapshot cannot distinguish the two yet.
    assert diff_snapshots(_snapshot(locked_pct=100), _snapshot(locked_pct=None)) == []


def test_liquidity_halving_is_lp_unlocked() -> None:
    events = diff_snapshots(_snapshot(liquidity_usd=100_000), _snapshot(liquidity_usd=40_000))

    assert [event["kind"] for event in events] == ["lp_unlocked"]
    assert events[0]["before_usd"] == 100_000
    assert events[0]["after_usd"] == 40_000
    assert events[0]["detail"] == "liquidity halved or worse"


def test_new_bundled_wallet_is_an_event_and_a_repeat_is_not() -> None:
    before = _snapshot(bundle_wallets=["W1"])
    after = _snapshot(bundle_wallets=["W1", "W9"])

    events = diff_snapshots(before, after)

    assert [event["kind"] for event in events] == ["new_bundle_wallet"]
    assert events[0]["wallet"] == "W9"
    assert diff_snapshots(after, after) == []


@pytest.mark.parametrize(
    ("before_value", "after_value", "detail"),
    [
        ("Owner1", None, "owner authority revoked"),
        ("Owner1", "Owner2", "owner authority moved"),
        ("Owner2", "Owner3", "owner authority moved"),
    ],
)
def test_authority_transitions(before_value: str | None, after_value: str | None, detail: str) -> None:
    events = diff_snapshots(_snapshot(owner=before_value), _snapshot(owner=after_value))

    assert [event["kind"] for event in events] == ["authority_changed"]
    assert events[0]["field"] == "owner"
    assert events[0]["before_value"] == before_value
    assert events[0]["after_value"] == after_value
    assert events[0]["detail"] == detail


def test_newly_learned_authority_is_not_a_change() -> None:
    # Nothing was known last cycle, so this is new information, not a transfer.
    assert diff_snapshots(_snapshot(mint=None), _snapshot(mint="MintAuth1")) == []


def test_a_changed_cycle_emits_all_four_kinds() -> None:
    before = _snapshot(
        mint="MintAuth1",
        locked_pct=100,
        liquidity_usd=100_000,
        holders=[{"wallet": "Whale1", "pct": 12.0}],
        bundle_wallets=["W1"],
    )
    after = _snapshot(
        mint=None,
        locked_pct=0.0,
        liquidity_usd=100_000,
        holders=[],
        bundle_wallets=["W1", "W9"],
    )

    kinds = sorted(event["kind"] for event in diff_snapshots(before, after))

    assert kinds == ["authority_changed", "cashed_out", "lp_unlocked", "new_bundle_wallet"]
