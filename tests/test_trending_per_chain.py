"""Per-chain trending: scoring, guards and the paid-slot separation contract.

No database is required — the query boundary is monkeypatched, exactly like
``tests/test_trending_api.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.public import trending_per_chain as route_mod
from app.trending.per_chain_score import WEIGHTS, score_components
from app.trending.promoted import DISCLOSURE, MAX_PER_CHAIN, select_promoted

app = FastAPI()
app.include_router(route_mod.router, prefix="/api/v1")
client = TestClient(app)

# Anchored on real now: ``_slot`` builds its window from datetime.now(UTC),
# so a fixed constant here made every slot look expired once the date moved.
NOW = datetime.now(UTC)


def _row(**overrides):
    base = {
        "token": "Mint111",
        "symbol": "TST",
        "vol": 50_000.0,
        "swaps": 120,
        "buyers": 60,
        "traders": 70,
        "buy_share": 0.6,
        "top1_share": 0.2,
        "roundtrip_share": 0.1,
        "top10_holder_share": 0.4,
        "first_px": 1.0,
        "last_px": 1.2,
        "first_ts": NOW - timedelta(hours=23),
        "last_ts": NOW - timedelta(minutes=5),
        "age_s": 3600,
        "window_s": 86_400,
        "stale_s": 300,
    }
    base.update(overrides)
    return base


def test_weights_sum_to_one_hundred():
    assert sum(WEIGHTS.values()) == 100.0


def test_all_components_exposed_and_scored():
    out = score_components(_row())
    assert set(out["components"]) == set(WEIGHTS)
    assert out["components"]["liquidity"] is None
    assert out["coverage"] == 0.9  # liquidity weight dropped, holders present
    assert 0.0 <= out["score"] <= 100.0
    assert out["unavailable"]["liquidity"] == "pool_reserves_not_indexed"


def test_missing_holders_shrinks_denominator_not_the_score():
    with_holders = score_components(_row())
    without = score_components(_row(top10_holder_share=None))
    assert without["coverage"] == 0.8
    assert without["score"] > 0  # a missing input must not zero a token
    assert with_holders["score"] != without["score"]


def test_wash_guards_fire_and_are_named():
    churn = score_components(_row(swaps=500, traders=10))["guards"]
    pump = score_components(_row(top1_share=0.9))["guards"]
    trip = score_components(_row(roundtrip_share=0.8))["guards"]
    assert churn["churn"] == 0.6
    assert pump["single_wallet_pump"] == 0.5
    assert trip["round_trip"] == 0.7


def test_scam_overlap_is_a_hard_demotion():
    out = score_components(_row(), scam_hit=True)
    assert out["guards"]["scam_overlap"] == 0.2
    assert out["score"] < score_components(_row())["score"]


def _slot(token, **overrides):
    slot = {
        "chain": "solana",
        "token": token,
        "symbol": "PAID",
        "advertiser": "acme",
        "starts_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        "ends_at": (datetime.now(UTC) + timedelta(hours=23)).isoformat(),
        "price_usd_per_day": 500.0,
    }
    slot.update(overrides)
    return slot


def test_promotions_are_labelled_and_capped():
    slots = [_slot(f"t{i}") for i in range(4)]
    promoted, rejected = select_promoted(slots, "solana", now=NOW)
    assert len(promoted) == MAX_PER_CHAIN
    assert all(p["placement"] == "promoted" and p["disclosure"] == DISCLOSURE for p in promoted)
    assert {r["reason"] for r in rejected} == {"chain_slot_capacity_reached"}


def test_expired_wrong_chain_and_risk_blocked_slots_are_refused():
    expired = _slot(
        "old",
        starts_at=(NOW - timedelta(days=3)).isoformat(),
        ends_at=(NOW - timedelta(days=2)).isoformat(),
    )
    other_chain = _slot("bsc1", chain="bsc")
    risk = _slot("rugged")
    promoted, rejected = select_promoted([expired, other_chain, risk], "solana", now=NOW, blocked={"rugged"})
    assert promoted == []
    assert rejected == [{"token": "rugged", "reason": "risk_flagged_not_promotable"}]


def test_route_ranks_per_chain_and_keeps_paid_slot_separate(monkeypatch):
    def fake(chain, hours, limit):
        if chain == "arbitrum":
            raise RuntimeError("clickhouse down")
        if chain == "solana":
            return [
                _row(token="low", vol=1000.0),
                _row(token="high", vol=900_000.0, swaps=290, buyers=99),
            ]
        return [_row(token=f"{chain}-tok")]

    monkeypatch.setattr(route_mod, "fetch_chain_rows", fake)
    monkeypatch.setattr(route_mod, "load_slots", lambda: [_slot("paid1")])
    resp = client.get("/api/v1/trending/per-chain", params={"chains": "solana,arbitrum,bsc", "window": 24})
    assert resp.status_code == 200
    body = resp.json()
    boards = {b["chain"]: b for b in body["boards"]}
    assert boards["solana"]["items"][0]["token"] == "high"
    assert [i["placement"] for i in boards["solana"]["items"]] == ["organic", "organic"]
    assert [p["token"] for p in body["promoted"]] == ["paid1"]
    assert "paid1" not in [i["token"] for i in boards["solana"]["items"]]
    assert boards["arbitrum"]["source"] == "degraded"
    assert any(d.startswith("arbitrum:") for d in body["degraded"])


def test_route_rejects_unsupported_window():
    assert client.get("/api/v1/trending/per-chain", params={"window": 4}).status_code == 422
