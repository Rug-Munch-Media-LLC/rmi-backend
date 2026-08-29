"""Tests for the first-buyers engine + phase + RiskReport cohort wiring.

Covers:
  * the pure classifier (``analyze_first_buyers_rows``): wallet-age buckets,
    same-window bundling flags, aggregates — no network, no ClickHouse.
  * fail-open contract (``get_first_buyers`` returns None when ClickHouse is
    not configured).
  * tier-scaled sample size (``run_first_buyers``: 25 quick / 250 deep).
  * the RiskReport first-buyer section consuming the 250-wallet cohort
    (``build_risk_report`` + ``_extract_first_buyer_section``).
  * the pump.fun on-demand path: ``_parse_solana_balance_deltas`` extracts
    (owner, delta) pairs from jsonParsed txs; ``_rpc_first_buyers_solana``
    returns earliest distinct buyers from RPC rows; ``get_first_buyers``
    falls back to RPC when ClickHouse has no Solana rows (pump.fun mints
    are not in the indexer's hardcoded tracked-mint list).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pytest

from app.domains.scanner.engines.first_buyers import (
    FIRST_BUYERS_LIMIT_DEEP,
    FIRST_BUYERS_LIMIT_QUICK,
    _parse_solana_balance_deltas,
    _rpc_first_buyers_solana,
    analyze_first_buyers_rows,
    get_first_buyers,
)
from app.domains.scanner.unified._phase_helpers import run_first_buyers
from app.domains.scanner.unified.model import ScanResult
from app.domains.scanner.unified.risk_report import build_risk_report

# ── Pure classifier ───────────────────────────────────────────────────────


def _rows_and_births() -> tuple[list[tuple[Any, ...]], dict[str, Any]]:
    now = datetime(2026, 8, 29, 12, 0, 0, tzinfo=UTC)
    launch = now - timedelta(minutes=5)
    rows = [
        ("0xaaa", launch + timedelta(seconds=0), 1e9),
        ("0xbbb", launch + timedelta(seconds=1), 2e9),
        ("0xccc", launch + timedelta(seconds=1), 3e9),  # same 2s window as bbb → bundled
        ("0xddd", launch + timedelta(hours=3), 4e9),
        ("0xeee", launch + timedelta(days=10), 5e9),
    ]
    births = {
        "0xaaa": launch - timedelta(minutes=1),  # fresh (<24h)
        "0xbbb": launch - timedelta(days=2),  # new (<7d)
        "0xccc": launch - timedelta(days=2),  # new
        "0xddd": launch - timedelta(days=100),  # established
        "0xeee": launch - timedelta(days=400),  # established
    }
    return rows, births


def test_analyze_first_buyers_classifies_wallets() -> None:
    res = analyze_first_buyers_rows(*_rows_and_births())
    buyers = res["buyers"]
    assert len(buyers) == 5
    by_wallet = {b["wallet"]: b for b in buyers}
    assert by_wallet["0xaaa"]["wallet_age_bucket"] == "fresh"
    assert by_wallet["0xbbb"]["wallet_age_bucket"] == "new"
    assert by_wallet["0xddd"]["wallet_age_bucket"] == "established"
    assert by_wallet["0xaaa"]["bundled"] is True  # 0xaaa+bbb+ccc inside 2s
    assert by_wallet["0xddd"]["bundled"] is False


def test_analyze_first_buyers_aggregates() -> None:
    res = analyze_first_buyers_rows(*_rows_and_births())
    agg = res["aggregates"]
    assert agg["unique_buyers"] == 5
    assert agg["fresh_wallet_pct"] == 20.0
    assert agg["new_wallet_pct"] == 60.0
    assert agg["established_pct"] == 40.0
    assert agg["bundled_pct"] == 60.0
    assert agg["median_latency_s"] == 1.0


def test_analyze_first_buyers_skips_empty_rows() -> None:
    res = analyze_first_buyers_rows([], {})
    assert res["buyers"] == []
    assert res["aggregates"]["unique_buyers"] == 0


# ── Fail-open contract ────────────────────────────────────────────────────


async def test_get_first_buyers_fail_open_without_clickhouse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLICKHOUSE_HOST", raising=False)
    out = await get_first_buyers("0xabc", "ethereum")
    assert out is None
    out = await get_first_buyers("", "ethereum")
    assert out is None
    out = await get_first_buyers("0xabc", "")
    assert out is None


# ── Phase tier scaling (25 quick / 250 deep) ──────────────────────────────


captured: list[int] = []


async def _fake_get_first_buyers(address: str, chain: str, limit: int) -> dict[str, Any]:
    captured.append(limit)
    return {
        "buyers": [{"wallet": "0xaaa", "first_buy_ts": "x", "tokens": 1.0, "bundled": False}],
        "aggregates": {"unique_buyers": 1},
        "limit": limit,
        "chain": chain,
    }


async def test_run_first_buyers_quick_uses_25(monkeypatch: pytest.MonkeyPatch) -> None:
    captured.clear()
    monkeypatch.setattr("app.domains.scanner.engines.first_buyers.get_first_buyers", _fake_get_first_buyers)
    r = ScanResult(token_address="0xabc", chain="ethereum", tier="free")
    await run_first_buyers(r, "0xabc", "ethereum", "free")
    assert captured == [FIRST_BUYERS_LIMIT_QUICK]
    assert r.first_buyers is not None
    assert "first_buyers" in r.modules_run
    assert r.first_buyers["limit"] == 25


async def test_run_first_buyers_deep_uses_250(monkeypatch: pytest.MonkeyPatch) -> None:
    captured.clear()
    monkeypatch.setattr("app.domains.scanner.engines.first_buyers.get_first_buyers", _fake_get_first_buyers)
    r = ScanResult(token_address="0xabc", chain="ethereum", tier="elite")
    await run_first_buyers(r, "0xabc", "ethereum", "elite")
    assert captured == [FIRST_BUYERS_LIMIT_DEEP]
    assert r.first_buyers is not None
    assert r.first_buyers["limit"] == 250


async def test_run_first_buyers_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    captured.clear()

    async def _broken(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("clickhouse down")

    monkeypatch.setattr("app.domains.scanner.engines.first_buyers.get_first_buyers", _broken)
    r = ScanResult(token_address="0xabc", chain="ethereum")
    await run_first_buyers(r, "0xabc", "ethereum", "elite")
    assert r.first_buyers is None


# ── RiskReport consumes the 250-wallet cohort ─────────────────────────────

_ETH_ADDR = "0x" + "a" * 38


def _cohort_payload(limit: int = 250) -> dict[str, Any]:
    now = datetime(2026, 8, 29, 12, 0, 0, tzinfo=UTC)
    launch = now - timedelta(minutes=5)
    return {
        "buyers": [
            {
                "wallet": _ETH_ADDR,
                "first_buy_ts": launch.isoformat(),
                "tokens": 1e9,
                "wallet_age_bucket": "fresh",
                "age_at_buy_s": 60,
                "bundled": True,
            },
            {
                "wallet": "0x" + "b" * 38,
                "first_buy_ts": (launch + timedelta(seconds=1)).isoformat(),
                "tokens": 2e9,
                "wallet_age_bucket": "new",
                "age_at_buy_s": 2 * 86400,
                "bundled": True,
            },
        ],
        "aggregates": {"unique_buyers": 2, "fresh_wallet_pct": 50.0, "bundled_pct": 100.0},
        "limit": limit,
        "chain": "ethereum",
    }


def _scan_with_first_buyers() -> ScanResult:
    r = ScanResult(token_address="0xabc", chain="ethereum", tier="elite")
    r.first_buyers = _cohort_payload()
    return r


def test_risk_report_first_buyer_section_reads_250_cohort() -> None:
    r = _scan_with_first_buyers()
    report = build_risk_report(r)
    fb = report.first_buyer
    assert fb.get("address") == _ETH_ADDR
    assert fb.get("in_bundle") is True
    assert fb.get("cohort_size") == 2
    assert fb.get("cohort_limit") == 250
    assert fb.get("cohort_fresh_pct") == 50.0
    assert fb.get("cohort_bundled_pct") == 100.0


def test_risk_report_deployer_match_against_cohort() -> None:
    r = _scan_with_first_buyers()
    r.security = {
        "owner_address": _ETH_ADDR,
        "is_honeypot": False,
    }
    report = build_risk_report(r)
    assert report.first_buyer.get("is_deployer") is True


def test_risk_report_without_first_buyers_stays_unknown() -> None:
    r = ScanResult(token_address="0xabc", chain="ethereum")
    report = build_risk_report(r)
    fb = report.first_buyer
    assert fb.get("address") is None
    assert fb.get("cohort_size") is None


# ── pump.fun on-demand path (Solana token program + bonding-curve transfers) ─


def _tx_with_balances(
    balances: list[tuple[str, str, int, int]],
    *,
    block_time: int = 1_700_000_000,
) -> dict[str, Any]:
    """Build a jsonParsed getTransaction result from (accountIndex, owner, pre, post)."""
    pre_balances = []
    post_balances = []
    for idx, owner, pre, post in balances:
        if pre is not None:
            pre_balances.append(
                {
                    "accountIndex": idx,
                    "mint": "pumpmint111111111111111111111111111111111111",
                    "owner": owner,
                    "uiTokenAmount": {"amount": str(pre), "decimals": 6, "uiAmount": pre / 1e6},
                }
            )
        if post is not None:
            post_balances.append(
                {
                    "accountIndex": idx,
                    "mint": "pumpmint111111111111111111111111111111111111",
                    "owner": owner,
                    "uiTokenAmount": {"amount": str(post), "decimals": 6, "uiAmount": post / 1e6},
                }
            )
    return {
        "blockTime": block_time,
        "meta": {"preTokenBalances": pre_balances, "postTokenBalances": post_balances},
        "transaction": {"signatures": ["sig1"]},
    }


def test_parse_solana_balance_deltas_extracts_buyers() -> None:
    """A buy = positive pre→post delta for the buyer's owner wallet."""
    tx = _tx_with_balances(
        [
            (0, "curvePDA", 1_000_000, 0),  # bonding-curve PDA: sold down
            (1, "buyerA", 0, 500_000),  # buyer A: bought 0.5 tokens
            (2, "buyerB", 0, 250_000),  # buyer B: bought 0.25 tokens
            (3, "noop", 0, 0),  # no change — skipped
        ]
    )
    deltas = _parse_solana_balance_deltas(tx, "pumpmint111111111111111111111111111111111111")
    by_owner = dict(deltas)
    assert by_owner == {"curvePDA": -1_000_000, "buyerA": 500_000, "buyerB": 250_000}
    # Wrong mint → no deltas.
    assert _parse_solana_balance_deltas(tx, "othermint11111111111111111111111111111111111") == []
    assert _parse_solana_balance_deltas({}, "pumpmint111111111111111111111111111111111111") == []


async def test_rpc_first_buyers_solana_returns_earliest_distinct(monkeypatch: pytest.MonkeyPatch) -> None:
    """The RPC path ranks earliest distinct buyers (newest-first sigs reversed)."""
    calls: list[tuple[str, list[Any]]] = []

    async def _fake_rpc_call(chain: str, method: str, params: list[Any], **kwargs: Any) -> dict[str, Any]:
        calls.append((method, params))
        if method == "getSignaturesForAddress":
            # newest-first: slot 100 (buyerB) before slot 90 (buyerA).
            return {
                "result": [
                    {"signature": "sigB", "slot": 100},
                    {"signature": "sigA", "slot": 90},
                ]
            }
        sig = params[0]
        if sig == "sigA":
            return {
                "result": _tx_with_balances([(0, "curvePDA", 1_000_000, 0), (1, "buyerA", 0, 500_000)], block_time=1000)
            }
        if sig == "sigB":
            return {
                "result": _tx_with_balances([(0, "curvePDA", 900_000, 0), (1, "buyerB", 0, 250_000)], block_time=2000)
            }
        return {"result": None}

    monkeypatch.setattr("app.domains.scanner.engines.first_buyers._get_rpc_call", lambda: _fake_rpc_call)
    rows = await _rpc_first_buyers_solana("pumpmint111111111111111111111111111111111111", 25)
    assert rows is not None
    assert len(rows) == 2
    assert str(rows[0][0]) == "buyerA"  # earliest buyer first
    assert str(rows[1][0]) == "buyerB"
    assert rows[0][2] == 500_000.0  # tokens_in
    # Same buyer re-buys later → merged into the earliest row.
    assert calls[0][0] == "getSignaturesForAddress"


async def test_rpc_first_buyers_solana_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _broken_rpc(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("rpc down")

    monkeypatch.setattr("app.domains.scanner.engines.first_buyers._get_rpc_call", lambda: _broken_rpc)
    assert await _rpc_first_buyers_solana("mint", 25) is None

    async def _empty_rpc(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"result": []}

    monkeypatch.setattr("app.domains.scanner.engines.first_buyers._get_rpc_call", lambda: _empty_rpc)
    assert await _rpc_first_buyers_solana("mint", 25) is None


async def test_get_first_buyers_solana_falls_back_to_rpc(monkeypatch: pytest.MonkeyPatch) -> None:
    """ClickHouse empty for a pump.fun mint → RPC path surfaces top-25 buyers."""
    monkeypatch.setattr(
        "app.domains.scanner.engines.first_buyers._query_first_buyers_solana",
        lambda mint, limit: None,  # indexer has no rows (pump.fun mint untracked)
    )
    monkeypatch.setattr(
        "app.domains.scanner.engines.first_buyers._query_wallet_births_solana",
        lambda buyers: {},
    )

    async def _fake_rpc_call(chain: str, method: str, params: list[Any], **kwargs: Any) -> dict[str, Any]:
        if method == "getSignaturesForAddress":
            return {
                "result": [
                    {"signature": "sigA", "slot": 90},
                    {"signature": "sigB", "slot": 100},
                ]
            }
        if params[0] == "sigA":
            return {"result": _tx_with_balances([(1, "buyerA", 0, 500_000)], block_time=1000)}
        return {"result": _tx_with_balances([(1, "buyerB", 0, 250_000)], block_time=2000)}

    monkeypatch.setattr("app.domains.scanner.engines.first_buyers._get_rpc_call", lambda: _fake_rpc_call)
    out = await get_first_buyers("pumpmint111111111111111111111111111111111111", "solana", limit=25)
    assert out is not None
    buyers = out["buyers"]
    assert len(buyers) == 2
    assert buyers[0]["wallet"] == "buyerA"
    assert buyers[0]["tokens"] == 500_000.0
    assert out["aggregates"]["unique_buyers"] == 2
    assert out["limit"] == 25
