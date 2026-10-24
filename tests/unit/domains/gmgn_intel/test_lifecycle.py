"""Lifecycle staging: classification, dedupe, counts, honest gaps."""

from __future__ import annotations

from app.domains.gmgn_intel.lifecycle import STAGES, build_lifecycle, classify_stage

OBSERVED = "2026-10-24T12:00:00+00:00"


def _row(address: str, **extra: object) -> dict[str, object]:
    base = {"address": address, "chain": "sol", "symbol": "TST", "holder_count": 100, "liquidity": 5000}
    base.update(extra)
    return base


def test_classify_stage_covers_all_four_stages() -> None:
    assert classify_stage(_row("a", progress=0.01), "new_creation")["stage"] == "new"
    assert classify_stage(_row("b", progress=0.42), "new_creation")["stage"] == "bonding"
    assert classify_stage(_row("c", progress=0.95), "near_completion")["stage"] == "almost_graduated"
    assert classify_stage(_row("d", complete_timestamp=1792828027), "completed")["stage"] == "graduated"
    assert classify_stage(_row("e", launchpad_status=1), "new_creation")["stage"] == "graduated"


def test_build_lifecycle_counts_stages_and_trending_surface() -> None:
    buckets = {
        "new_creation": [_row("new1", progress=0.02, volume_24h=1000)],
        "near_completion": [_row("near1", progress=0.97, volume_24h=50000)],
        "completed": [_row("done1", complete_timestamp=1792828027, volume_24h=200000)],
    }
    rank_rows = [{"address": "done1", "rank": 1}, {"address": "new1", "rank": 9}]
    result = build_lifecycle(buckets, rank_rows, "sol", OBSERVED)
    assert result["chain"] == "solana"
    assert result["counts"]["new"] == 1
    assert result["counts"]["almost_graduated"] == 1
    assert result["counts"]["graduated"] == 1
    assert result["counts"]["trending"] == 2
    assert result["token_count"] == 3
    assert set(result["counts"]) == set(STAGES)
    trending = {t["address"]: t for t in result["tokens"]}
    assert trending["done1"]["trending"] is True
    assert trending["done1"]["trending_rank"] == 1
    assert trending["near1"]["trending"] is False


def test_build_lifecycle_keeps_most_advanced_stage_on_duplicate() -> None:
    buckets = {
        "new_creation": [_row("dup", progress=0.01)],
        "completed": [_row("dup", complete_timestamp=1792828027)],
    }
    result = build_lifecycle(buckets, [], "solana", OBSERVED)
    assert result["token_count"] == 1
    assert result["tokens"][0]["stage"] == "graduated"
    assert len(result["tokens"][0]["evidence"]) == 2


def test_sparse_row_reports_gaps_instead_of_fake_zeros() -> None:
    result = build_lifecycle({"new_creation": [{"address": "bare"}]}, None, "sol", OBSERVED)
    token = result["tokens"][0]
    assert token["liquidity_usd"] is None
    assert token["created_at"] is None
    gaps = {item["field"] for item in token["missing"]}
    assert {"liquidity_usd", "created_at", "progress", "social"} <= gaps
    assert token["score"] is not None
    assert token["score_grade"] in {"A", "B", "C", "D", "F"}
