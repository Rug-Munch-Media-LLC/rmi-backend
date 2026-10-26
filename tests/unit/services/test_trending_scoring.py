"""Unit tests for transparent trending scoring (no ClickHouse, no network)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.services.trending import service
from app.services.trending.scoring import score_candidates


def _row(**overrides):
    base = {
        "chain": "ethereum",
        "token_address": "0xabc",
        "symbol": "ABC",
        "swaps": 100,
        "senders": 50,
        "top_sender_swaps": 5,
        "volume_usd": 1_000_000.0,
        "price_change": 0.25,
        "transfers_24h": 1_000,
        "holders_now": 120,
        "holders_prev": 100,
        "launched_at": (datetime.now(UTC) - timedelta(hours=6)).isoformat(),
        "sources": ["dex_events", "prices", "transfers", "launches"],
        "social": {"mentions": 4, "polarity": 0.5, "method": "test"},
    }
    base.update(overrides)
    return base


def test_boosted_is_excluded_from_rank_and_returned_separately():
    rows = [_row(token_address="0xboost"), _row(token_address="0xplain", swaps=90)]
    boosts = {("ethereum", "0xboost"): {"amount": 500, "total_amount": 500, "icon": "https://x/i.png"}}
    ranked, boosted = score_candidates(rows, set(boosts), boosts)

    assert [r["token_address"] for r in ranked] == ["0xplain"]
    assert [b["token_address"] for b in boosted] == ["0xboost"]
    assert boosted[0]["rank"] is None
    assert boosted[0]["excluded_from_rank"] is True
    assert boosted[0]["icon_url"] == "https://x/i.png"
    assert boosted[0]["symbol"] == "ABC"


def test_missing_factors_are_omitted_never_imputed():
    full, _ = score_candidates([_row()], set())
    bare, _ = score_candidates(
        [
            _row(
                volume_usd=None,
                price_change=None,
                social=None,
                launched_at=None,
                transfers_24h=None,
                holders_prev=0,
                sources=["dex_events"],
            )
        ],
        set(),
    )

    full_c = full[0]["score_components"]
    bare_c = bare[0]["score_components"]
    assert full_c["price_momentum"]["status"] == "ok"
    assert bare_c["price_momentum"]["status"] == "null"
    assert bare_c["volume_liquidity"]["status"] == "null"
    assert bare_c["social_sentiment"]["status"] == "null"
    assert bare_c["age"]["status"] == "null"
    assert bare_c["holder_growth"]["status"] == "no_baseline"
    # tx_count falls back to measured swaps when the transfer table has no row
    assert bare_c["tx_count"]["value"] == 100
    assert full[0]["score"] > bare[0]["score"]


def test_score_is_rederivable_from_reported_components():
    ranked, _ = score_candidates([_row()], set())
    item = ranked[0]
    present = [c for c in item["score_components"].values() if c["status"] == "ok"]
    total_w = sum(c["weight"] for c in present)
    raw = sum(c["weight"] * c["score"] for c in present) / total_w
    assert item["score"] == pytest.approx(round(100.0 * raw * item["penalty_multiplier"], 2))
    assert item["score_raw"] == pytest.approx(round(100.0 * raw, 2))


def test_manipulation_penalties_are_applied_and_explained():
    wash = _row(swaps=1000, senders=10, top_sender_swaps=600)
    ranked, _ = score_candidates([wash], set())
    kinds = {p["kind"] for p in ranked[0]["penalties"]}
    assert {"wash_trading", "single_wallet_volume"} <= kinds
    assert ranked[0]["penalty_multiplier"] < 1.0
    assert any("penalty wash_trading" in r for r in ranked[0]["reasons"])

    bundled = _row(bundled_blocks=10, sources=["dex_events", "launches"])
    ranked_b, _ = score_candidates([bundled], set())
    assert ranked_b[0]["penalties"][0]["kind"] == "fresh_wallet_bundles"


def test_rank_is_dense_and_score_ordered():
    rows = [_row(token_address=f"0x{i}", transfers_24h=100 * (i + 1)) for i in range(4)]
    ranked, _ = score_candidates(rows, set())
    assert [r["rank"] for r in ranked] == [1, 2, 3, 4]
    assert ranked[0]["score"] >= ranked[-1]["score"]


def test_unknown_chain_returns_null_score_and_reason(monkeypatch):
    monkeypatch.setattr(service, "fetch_factors", lambda *a, **k: pytest.fail("must not query"))
    result = asyncio.run(service.trending_scored(chain="dogechain", limit=5, hours=24))

    assert result["data"] == []
    assert result["score"] is None
    assert result["score_components"] is None
    assert result["reason"].startswith("unsupported_chain")
    assert "ethereum" in result["supported_chains"]


def test_supported_chain_ranks_measured_rows_and_keeps_boosts_out(monkeypatch):
    rows = [_row(token_address="0xboost"), _row(token_address="0xplain", transfers_24h=5000)]

    async def fake_boosts(chain):
        return {("ethereum", "0xboost"): {"amount": 9, "total_amount": 9, "icon": None}}

    async def fake_sentiment(symbols):
        return {}

    monkeypatch.setattr(service, "fetch_factors", lambda *a, **k: rows)
    monkeypatch.setattr(service, "fetch_boosts", fake_boosts)
    monkeypatch.setattr(service, "fetch_sentiment", fake_sentiment)

    result = asyncio.run(service.trending_scored(chain="ethereum", limit=10, hours=24))
    assert result["chain"] == "ethereum"
    assert result["count"] == 1
    assert result["data"][0]["token_address"] == "0xplain"
    assert result["data"][0]["rank"] == 1
    assert result["boosted_count"] == 1
    assert result["boosted"][0]["excluded_from_rank"] is True
    assert result["score_formula"].startswith("score = 100 * clamp")


def test_no_candidates_reports_reason_without_fabricating(monkeypatch):
    async def fake_boosts(chain):
        return {}

    monkeypatch.setattr(service, "fetch_factors", lambda *a, **k: [])
    monkeypatch.setattr(service, "fetch_boosts", fake_boosts)
    result = asyncio.run(service.trending_scored(chain="solana", limit=5, hours=24))
    assert result["data"] == []
    assert result["reason"].startswith("no_recent_data")
