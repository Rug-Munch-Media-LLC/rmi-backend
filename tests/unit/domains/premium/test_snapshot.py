"""Snapshot: missing facts stay ``None``, never zero.

A premium module drops out whenever a provider degrades. The snapshot is the
boundary where that degradation must remain visible: if a missing liquidity
figure became ``0.0`` the watch diff would report a drained pool, and if a
missing lock became ``0.0`` it would report an unlocked LP. Both are lies
produced by data absence, so every one of these tests asserts ``is None``.
"""

from __future__ import annotations

import pytest

from app.domains.premium.snapshot import snapshot_from_scan


def test_bare_payload_keeps_every_unknown_none() -> None:
    snapshot = snapshot_from_scan({"token": "MintA", "chain": "solana"})

    assert snapshot["token"] == "MintA"
    assert snapshot["chain"] == "solana"
    assert snapshot["authority"] == {"mint": None, "freeze": None, "owner": None}
    assert snapshot["lp"] == {
        "liquidity_usd": None,
        "locked_pct": None,
        "unlocked": None,
        "owner": None,
    }
    assert snapshot["holders"] == {"count": None, "top": [], "dev_pct": None}
    assert snapshot["bundle_wallets"] == []


def test_no_token_is_an_error_not_an_empty_snapshot() -> None:
    with pytest.raises(ValueError, match="no token address"):
        snapshot_from_scan({"token": "   ", "chain": "solana"})


def test_non_numeric_values_do_not_become_zero() -> None:
    snapshot = snapshot_from_scan(
        {
            "token": "MintB",
            "chain": "solana",
            "liquidity_usd": "n/a",
            "dev_holdings_pct": "unknown",
            "top_holders": [{"address": "W1", "pct": "bad"}, {"address": "", "pct": 5}],
        }
    )

    assert snapshot["lp"]["liquidity_usd"] is None
    assert snapshot["holders"]["dev_pct"] is None
    assert snapshot["holders"]["top"] == [{"wallet": "W1", "pct": None}]


def test_locked_pool_is_reported_as_locked_not_unknown() -> None:
    snapshot = snapshot_from_scan(
        {
            "token": "MintC",
            "chain": "solana",
            "contract_authority": {"owner": "Owner1", "lp_locked_pct": 100},
            "liquidity": {"usd": 41_000, "locked_pct": 100},
        }
    )

    assert snapshot["lp"]["liquidity_usd"] == 41_000.0
    assert snapshot["lp"]["locked_pct"] == 100.0
    assert snapshot["lp"]["unlocked"] is False
    assert snapshot["lp"]["owner"] == "Owner1"


def test_unlocked_pool_is_reported_as_unlocked() -> None:
    snapshot = snapshot_from_scan({"token": "MintD", "chain": "solana", "liquidity": {"locked_pct": 0}})

    assert snapshot["lp"]["locked_pct"] == 0.0
    assert snapshot["lp"]["unlocked"] is True


def test_bundle_wallets_are_deduplicated_and_sorted() -> None:
    snapshot = snapshot_from_scan(
        {
            "token": "MintE",
            "chain": "solana",
            "bundle_analysis": {"bundles": [{"wallets": ["W2", "W1"]}, {"wallets": ["W2"]}]},
            "bundled_first_buyers": ["W3", "W1", ""],
        }
    )

    assert snapshot["bundle_wallets"] == ["W1", "W2", "W3"]


def test_holders_prefer_pct_then_percentage_and_cap_at_fifty() -> None:
    holders = [{"address": f"W{i}", "percentage": 1.0} for i in range(60)]
    snapshot = snapshot_from_scan({"token": "MintF", "chain": "solana", "top_holders": holders})

    assert len(snapshot["holders"]["top"]) == 50
    assert snapshot["holders"]["top"][0] == {"wallet": "W0", "pct": 1.0}


def test_zero_liquidity_without_a_pool_block_is_unknown_not_drained() -> None:
    """A 0.0 from a scan with no pool block must not read as an empty pool."""
    snapshot = snapshot_from_scan({"token": "T", "chain": "base", "liquidity_usd": 0.0})

    assert snapshot["lp"]["liquidity_usd"] is None
    assert snapshot["lp"]["unlocked"] is None


def test_bundled_first_buyer_count_does_not_crash_the_snapshot() -> None:
    """The EVM scanner emits a count where Solana emits a wallet list."""
    snapshot = snapshot_from_scan(
        {"token": "T", "chain": "base", "bundled_first_buyers": 25, "top_holders": [{"address": "h", "pct": 3.0}]}
    )

    assert snapshot["bundle_wallets"] == []
    assert snapshot["holders"]["top"] == [{"wallet": "h", "pct": 3.0}]
