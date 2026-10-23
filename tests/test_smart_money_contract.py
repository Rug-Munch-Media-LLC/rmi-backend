"""Contract + logic tests for /api/v1/smart-money/moves.

The endpoint contract is frozen: consumers deserialize ``items[]`` keys
directly. These tests lock the key set, the window validation and the
pnl semantics without needing a live ClickHouse.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.public.smart_money import router
from app.domains.smart_money.models import (
    SmartMoneyMove,
    SmartMoneyMovesResponse,
)
from app.domains.smart_money.service import build_moves

CONTRACT_KEYS = {
    "wallet", "label", "chain", "token", "symbol", "action", "amount_usd",
    "price", "pnl_usd", "pnl_pct", "win_rate", "roi_30d", "netflow_usd",
    "cluster_id", "labels", "confidence", "reasons",
}


def _row(wallet: str, token: str, inflow: float, outflow: float) -> dict:
    return {
        "chain": "ethereum",
        "wallet": wallet,
        "token": token,
        "symbol": "WETH",
        "inflow_usd": inflow,
        "outflow_usd": outflow,
        "last_ts": datetime.now(UTC).replace(tzinfo=None),
    }


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def test_closed_round_trip_pnl():
    rows = [_row("0xabc", "0xweth", 1000.0, 1500.0)]
    moves = build_moves(rows, {}, {}, {("ethereum", "0xweth"): 2400.0}, window_hours=24, limit=5)
    move = moves[0]
    assert move.pnl_usd == 500.0
    assert move.pnl_pct == 0.5
    assert move.action == "sell"
    assert move.netflow_usd == -500.0
    assert move.price == 2400.0


def test_open_position_reports_null_with_reason():
    rows = [_row("0xabc", "0xweth", 1000.0, 0.0)]
    moves = build_moves(rows, {}, {}, {}, window_hours=24, limit=5)
    move = moves[0]
    assert move.pnl_usd is None
    assert move.pnl_pct is None
    assert "pnl:open_position_in_window" in move.reasons
    assert move.action == "buy"
    assert move.label is None
    assert "label:none_matched" in move.reasons


def _score(move: SmartMoneyMove) -> float:
    return float(next(r.split("=", 1)[1] for r in move.reasons if r.startswith("score=")))


def test_scoring_discards_missing_evidence_from_confidence():
    rows = [_row("0xa", "0xt", 1000.0, 1500.0), _row("0xb", "0xt", 900.0, 0.0)]
    moves = build_moves(rows, {}, {}, {}, window_hours=24, limit=5)
    assert all(0.0 <= m.confidence <= 1.0 for m in moves)
    assert _score(moves[0]) >= _score(moves[-1])


def test_route_is_registered(app):
    spec = app.openapi()["paths"]["/api/v1/smart-money/moves"]["get"]
    params = {p["name"]: p for p in spec["parameters"]}
    assert set(params) == {"chain", "window", "limit"}
    assert params["window"]["schema"]["default"] == 24
    assert spec["responses"]["200"]["content"]["application/json"]["schema"]


def test_response_envelope_contract():
    """Lock the serialized key set consumers deserialize."""
    rows = [_row("0xabc", "0xweth", 1000.0, 1500.0)]
    moves = build_moves(
        rows, {"0xabc": ["Whale"]}, {"0xabc": "c_1"},
        {("ethereum", "0xweth"): 2400.0}, window_hours=24, limit=5,
    )
    payload = SmartMoneyMovesResponse(
        items=moves, generated_at=datetime.now(UTC)
    ).model_dump(mode="json")
    assert set(payload) == {"items", "generated_at"}
    assert set(payload["items"][0]) == CONTRACT_KEYS
    item = payload["items"][0]
    assert item["label"] == "Whale"
    assert item["cluster_id"] == "c_1"
    assert item["roi_30d"] is None
    assert any(r.startswith("score=") for r in item["reasons"])
    # win_rate has < 3 closed positions, so 0.20 of the weight lacks
    # evidence and confidence is coverage: 1.0 - 0.20 = 0.8
    assert item["confidence"] == 0.8


def test_endpoint_rejects_bad_window(client):
    resp = client.get("/api/v1/smart-money/moves", params={"window": 7})
    assert resp.status_code == 422


def test_endpoint_rejects_unknown_chain(client):
    resp = client.get("/api/v1/smart-money/moves", params={"chain": "dogechain"})
    assert resp.status_code == 422


def test_stale_evidence_degrades_confidence():
    old = _row("0xabc", "0xweth", 1000.0, 1500.0)
    old["last_ts"] = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=23)
    fresh = dict(old)
    fresh["last_ts"] = datetime.now(UTC).replace(tzinfo=None)
    stale_moves = build_moves([old], {}, {}, {}, window_hours=24, limit=5)
    fresh_moves = build_moves([fresh], {}, {}, {}, window_hours=24, limit=5)
    assert _score(stale_moves[0]) < _score(fresh_moves[0])
    assert stale_moves[0].confidence == fresh_moves[0].confidence
