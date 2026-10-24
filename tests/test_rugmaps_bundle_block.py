"""Tests for the normalized ``clusters``/``bundle`` block on scan responses.

Covers the pure normalizer (evidence, shared funder, confidence, null reasons,
no identity claims), the never-raising fetcher, and the /scan wiring.
"""

from __future__ import annotations

from types import SimpleNamespace as Fake
from unittest.mock import AsyncMock, patch

from app.routers.rugmaps_router.bundle_block import build_bundle_block
from app.routers.rugmaps_router.bundle_block_fetch import fetch_bundle_block


def _step(address: str, ts: int, tx: str = "") -> Fake:
    return Fake(address=address, amount=1.0, token="SOL", timestamp=ts, tx_signature=tx)


def _wallet(address: str, chain: list[Fake], block: int = 10) -> Fake:
    return Fake(address=address, funding_chain=chain, buy_amount=1.0, buy_block=block, confidence="HIGH", evidence=[])


def _cluster(wallets: list[str], confidence: float = 0.7, pattern: str = "same_size") -> Fake:
    return Fake(wallets=wallets, pattern=pattern, combined_percentage=12.5, confidence=confidence)


def _sniper(address: str, block: int = 11) -> Fake:
    return Fake(address=address, block_number=block, buy_amount=1.0, compute_units=0, priority_fee=0.0, is_jito=False)


def _report(**over: object) -> Fake:
    base: dict[str, object] = {
        "token_address": "TOKEN",
        "chain": "solana",
        "deployer": "DEVWALLET",
        "deploy_block": 100,
        "deploy_timestamp": 1700000000000,
        "total_bundled_percentage": 42.5,
        "deployer_funded": [],
        "same_block_snipers": [],
        "behavior_clusters": [],
        "jito_bundles_detected": 0,
        "risk_score": 70,
        "risk_level": "HIGH",
        "warnings": [],
    }
    base.update(over)
    return Fake(**base)


def test_missing_report_returns_null_block_with_reasons():
    block = build_bundle_block(None)
    assert block["available"] is False
    assert block["cluster_count"] is None
    assert block["clusters"] == []
    assert set(block["null_reasons"]) >= {"cluster_count", "shared_funder", "confidence"}
    assert block["assessment"]["identity_claims"] == []
    assert block["assessment"]["coordinated"] is None


def test_shared_funder_uses_earliest_funding_step_as_origin():
    origin = "ORIGINFUNDER"
    funded = [
        _wallet(f"W{i}", [_step("NEARHOP", 200), _step(origin, 100, "TX1")]) for i in range(3)
    ]
    block = build_bundle_block(_report(deployer_funded=funded))
    assert block["shared_funder"]["address"] == origin
    assert block["shared_funder"]["wallet_count"] == 3
    assert block["shared_funder"]["funding_tx"] == "TX1"
    assert block["cluster_count"] == 1
    assert block["clusters"][0]["basis"] == "shared_funder"
    assert block["confidence"] == 0.9
    assert block["assessment"]["coordinated"] is True
    assert block["assessment"]["basis"] == "shared_funder_group>=3_wallets"


def test_behaviour_cluster_and_funder_group_are_not_double_counted():
    funded = [_wallet(f"W{i}", [_step("FUNDER", 100)]) for i in range(2)]
    block = build_bundle_block(_report(deployer_funded=funded, behavior_clusters=[_cluster(["W0", "W1"])]))
    assert block["cluster_count"] == 1
    assert block["clusters"][0]["basis"] == "behaviour_pattern"
    assert block["shared_funder"]["wallet_count"] == 2


def test_same_block_snipers_drive_coordination_assessment():
    block = build_bundle_block(_report(same_block_snipers=[_sniper(f"S{i}") for i in range(3)]))
    assert block["same_block_sniper_count"] == 3
    assert block["confidence"] >= 0.8
    assert block["assessment"]["coordinated"] is True
    assert block["assessment"]["basis"] == "same_block_snipers>=3"


def test_two_snipers_without_funder_is_not_called_coordinated():
    block = build_bundle_block(_report(same_block_snipers=[_sniper("S0"), _sniper("S1")]))
    assert block["assessment"]["coordinated"] is False
    assert block["assessment"]["basis"] == "insufficient_evidence"
    assert block["assessment"]["identity_claims"] == []


def test_fresh_share_is_passed_through_and_reason_cleared():
    block = build_bundle_block(_report(), fresh_share_pct=66.666)
    assert block["fresh_wallet_share_pct"] == 66.67
    assert "fresh_wallet_share_pct" not in block["null_reasons"]


def test_bundled_supply_pct_kept_and_clusters_truncated():
    snipers = [_sniper(f"S{i}") for i in range(25)]
    block = build_bundle_block(_report(same_block_snipers=snipers))
    assert block["bundled_supply_pct"] == 42.5
    assert len(block["evidence"][-1]["wallets"]) == 10


async def test_fetch_bundle_block_unsupported_chain_returns_reason_without_network():
    block = await fetch_bundle_block("TOKEN", "dogechain")
    assert block["available"] is False
    assert block["null_reasons"]["cluster_count"] == "chain_not_supported:dogechain"


async def test_fetch_bundle_block_engine_error_degrades():
    with patch("app.domains.scanners.bundle_detector.BundleDetector") as detector_cls:
        detector_cls.return_value.detect_bundles = AsyncMock(side_effect=RuntimeError("boom"))
        detector_cls.return_value.close = AsyncMock()
        block = await fetch_bundle_block("TOKEN", "solana")
    assert block["available"] is False
    assert block["null_reasons"]["cluster_count"] == "bundle_detection_failed:RuntimeError"


def _scan_result() -> Fake:
    return Fake(
        token_address="TOKEN",
        chain="solana",
        symbol="TKN",
        name="Token",
        safety_score=30,
        risk_flags=["high_concentration"],
        confidence=80,
        modules_run=[{"module": "holder_analysis", "status": "ok"}],
        scanned_at="2026-10-24T00:00:00Z",
    )


async def test_scan_response_carries_clusters_block():
    from app.routers.rugmaps_router._helpers import _run_token_risk_scan

    fake_block = build_bundle_block(_report(behavior_clusters=[_cluster(["W0", "W1"])]))
    with (
        patch("app.domains.scanner.service.scan_token", AsyncMock(return_value=_scan_result())),
        patch(
            "app.routers.rugmaps_router.bundle_block_fetch.fetch_bundle_block",
            AsyncMock(return_value=fake_block),
        ) as fetch_mock,
    ):
        resp = await _run_token_risk_scan("TOKEN", "solana")
    fetch_mock.assert_awaited_once_with("TOKEN", "solana")
    assert resp["status"] == "completed"
    assert resp["clusters"]["cluster_count"] == 1
    assert resp["clusters"]["source"] == "bundle_detector"


async def test_scan_failure_still_returns_clusters_block():
    from app.routers.rugmaps_router._helpers import _run_token_risk_scan

    null_block = build_bundle_block(None)
    with (
        patch("app.domains.scanner.service.scan_token", AsyncMock(side_effect=RuntimeError("rpc down"))),
        patch(
            "app.routers.rugmaps_router.bundle_block_fetch.fetch_bundle_block",
            AsyncMock(return_value=null_block),
        ),
    ):
        resp = await _run_token_risk_scan("TOKEN", "solana")
    assert resp["status"] == "scan_failed"
    assert resp["clusters"]["available"] is False
