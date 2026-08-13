"""Tests for scanner enhancements — bundle-launch detection + wallet freshness metrics.

Covers the 2026-08-09 scanner improvement:

  Bundle detection (engines/own_bundle_detect.py):
    - _score_launch_cluster — launch-window cluster signals (first-time buyers,
      similar amounts, deployer-funded wallets)
    - _compute_launch_bundle_verdict — launch-signal aggregation → is_bundled
    - detect_bundled_launch — ClickHouse orchestration (mocked) + result shape
    - _compute_bundle_score launch bonus (base weights unchanged)

  Wallet freshness metrics (engines/funding_analysis.py):
    - _aggregate_wallet_metrics — fresh_wallet_pct / cex_funded_pct / first_tx_pct
    - compute_wallet_metrics — ClickHouse orchestration (mocked) + result shape

  Scoring integration (unified/scoring.py):
    - BUNDLE_DETECTED risk flag (-25 security dimension)
    - FARMING_RISK flag (fresh_wallet_pct > 70)
    - INSTITUTIONAL green flag (cex_funded_pct > 40)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.domains.scanner.engines import funding_analysis as fa, own_bundle_detect as bd
from app.domains.scanner.unified.model import ScanResult
from app.domains.scanner.unified.scoring import BUNDLE_PENALTY, compute_score

BINANCE_HOT = "0x28C6c06298d514Db089934071355E5743bf21d60"  # known_entities CEX
UNKNOWN = "0x1111111111111111111111111111111111111111"
TOKEN = "0xToken000000000000000000000000000000000000"


def _result(**overrides: object) -> ScanResult:
    """Build a minimal ScanResult for scoring tests."""
    defaults: dict[str, object] = {
        "token_address": TOKEN,
        "chain": "ethereum",
    }
    defaults.update(overrides)
    return ScanResult(**defaults)  # type: ignore[arg-type]


# ═══════════════════════════════════════════════════════════════════════════
# Bundle detection — pure launch-cluster scoring
# ═══════════════════════════════════════════════════════════════════════════


def _launch_buyer(
    buyer: str,
    block: int = 1000,
    amount: float = 100.0,
    is_first_tx: bool = True,
    deployer_funded: bool = False,
) -> dict[str, Any]:
    return {
        "buyer": buyer,
        "block_number": block,
        "buy_count": 1,
        "amount": amount,
        "is_first_tx": is_first_tx,
        "deployer_funded": deployer_funded,
    }


class TestScoreLaunchCluster:
    def test_empty_cluster_sets_defaults_only(self) -> None:
        """No launch buyers → no sub-scores, deploy block preserved."""
        result: dict[str, Any] = {}
        bd._score_launch_cluster(result, {"deploy_block": 42, "launch_buyers": [], "cluster_blocks": []})
        assert result["deploy_block"] == 42
        assert result["cluster_blocks"] == []
        assert result.get("launch_cluster_score", 0) == 0
        assert result.get("first_time_buyer_pct", 0) == 0

    def test_dense_all_first_time_cluster_scores_100(self) -> None:
        """10 first-time buyers in one launch block → launch_cluster_score 100."""
        cluster = {
            "deploy_block": 1000,
            "launch_buyers": [_launch_buyer(f"0x{i}") for i in range(10)],
            "cluster_blocks": [{"block_number": 1000, "buyer_count": 10}],
        }
        result: dict[str, Any] = {}
        bd._score_launch_cluster(result, cluster)
        assert result["first_time_buyer_pct"] == 100.0
        assert result["launch_cluster_score"] == 100.0
        assert result["launch_buyers"] == 10

    def test_mixed_first_time_share(self) -> None:
        """3 of 6 buyers first-time → first_time_buyer_pct 50.0."""
        buyers = [
            _launch_buyer("0x1", is_first_tx=True),
            _launch_buyer("0x2", is_first_tx=True),
            _launch_buyer("0x3", is_first_tx=True),
            _launch_buyer("0x4", is_first_tx=False),
            _launch_buyer("0x5", is_first_tx=False),
            _launch_buyer("0x6", is_first_tx=False),
        ]
        cluster = {
            "deploy_block": 1000,
            "launch_buyers": buyers,
            "cluster_blocks": [{"block_number": 1000, "buyer_count": 6}],
        }
        result: dict[str, Any] = {}
        bd._score_launch_cluster(result, cluster)
        assert result["first_time_buyer_pct"] == 50.0
        # density 60 + first_tx 50 → 0.6*60 + 0.4*50 = 56
        assert result["launch_cluster_score"] == 56.0

    def test_identical_amounts_score_100(self) -> None:
        """Identical bot-sized buy amounts → similar_amount_score 100 (CV=0)."""
        cluster = {
            "deploy_block": 1000,
            "launch_buyers": [_launch_buyer(f"0x{i}", amount=100.0) for i in range(5)],
            "cluster_blocks": [{"block_number": 1000, "buyer_count": 5}],
        }
        result: dict[str, Any] = {}
        bd._score_launch_cluster(result, cluster)
        assert result["similar_amount_score"] == 100.0

    def test_varied_amounts_score_zero(self) -> None:
        """Wide amount spread (CV > 0.8) → similar_amount_score 0."""
        amounts = [10.0, 500.0, 12000.0, 80000.0, 300000.0]
        cluster = {
            "deploy_block": 1000,
            "launch_buyers": [_launch_buyer(f"0x{i}", amount=a) for i, a in enumerate(amounts)],
            "cluster_blocks": [{"block_number": 1000, "buyer_count": 5}],
        }
        result: dict[str, Any] = {}
        bd._score_launch_cluster(result, cluster)
        assert result["similar_amount_score"] == 0.0

    def test_deployer_funded_share(self) -> None:
        """4 of 5 launch buyers funded by the deployer → deployer_funded_pct 80."""
        cluster = {
            "deploy_block": 1000,
            "launch_buyers": [
                _launch_buyer("0x1", deployer_funded=True),
                _launch_buyer("0x2", deployer_funded=True),
                _launch_buyer("0x3", deployer_funded=True),
                _launch_buyer("0x4", deployer_funded=True),
                _launch_buyer("0x5", deployer_funded=False),
            ],
            "cluster_blocks": [{"block_number": 1000, "buyer_count": 5}],
        }
        result: dict[str, Any] = {}
        bd._score_launch_cluster(result, cluster)
        assert result["deployer_funded_pct"] == 80.0


class TestComputeLaunchBundleVerdict:
    def test_zero_signals_not_bundled(self) -> None:
        result: dict[str, Any] = {
            "launch_cluster_score": 0,
            "similar_amount_score": 0,
            "common_funder_score": 0,
            "deployer_funded_pct": 0,
        }
        bd._compute_launch_bundle_verdict(result)
        assert result["is_bundled"] is False
        assert result["risk_label"] == "low"

    def test_full_launch_signals_critical(self) -> None:
        result: dict[str, Any] = {
            "launch_cluster_score": 100,
            "similar_amount_score": 100,
            "common_funder_score": 100,
            "deployer_funded_pct": 100,
        }
        bd._compute_launch_bundle_verdict(result)
        # 100*0.40 + 100*0.20 + 100*0.25 + 100*0.15 = 100
        assert result["confidence"] == 100.0
        assert result["is_bundled"] is True
        assert result["risk_label"] == "critical"

    def test_cluster_alone_flags_high(self) -> None:
        """launch_cluster_score=100 alone → aggregated 40 → bundled 'high'."""
        result: dict[str, Any] = {
            "launch_cluster_score": 100,
            "similar_amount_score": 0,
            "common_funder_score": 0,
            "deployer_funded_pct": 0,
        }
        bd._compute_launch_bundle_verdict(result)
        assert result["confidence"] == 40.0
        assert result["is_bundled"] is True
        assert result["risk_label"] == "high"

    def test_weak_cluster_not_bundled(self) -> None:
        """launch_cluster_score=40 → aggregated 16 < 25 → not bundled."""
        result: dict[str, Any] = {
            "launch_cluster_score": 40,
            "similar_amount_score": 0,
            "common_funder_score": 0,
            "deployer_funded_pct": 0,
        }
        bd._compute_launch_bundle_verdict(result)
        assert result["confidence"] == 16.0
        assert result["is_bundled"] is False


class TestComputeBundleScoreLaunchBonus:
    def test_launch_bonus_boosts_aggregate(self) -> None:
        """launch_cluster_score adds launch*0.10 on top of the base weights."""
        result: dict[str, Any] = {
            "atomic_block_score": 100,
            "common_funder_score": 0,
            "distribution_anomaly_score": 0,
            "concentration_score": 0,
            "launch_cluster_score": 50,
        }
        bd._compute_bundle_score(result)
        # 100*0.35 + 50*0.10 = 40
        assert result["confidence"] == 40.0

    def test_no_launch_data_preserves_base(self) -> None:
        """Without launch data the aggregate is unchanged (35 for atomic=100)."""
        result: dict[str, Any] = {
            "atomic_block_score": 100,
            "common_funder_score": 0,
            "distribution_anomaly_score": 0,
            "concentration_score": 0,
        }
        bd._compute_bundle_score(result)
        assert result["confidence"] == 35.0


# ═══════════════════════════════════════════════════════════════════════════
# Bundle detection — detect_bundled_launch orchestration (ClickHouse mocked)
# ═══════════════════════════════════════════════════════════════════════════


def launch_run_sync(cluster: dict[str, Any] | None, funders: list[dict[str, Any]] | None) -> Any:
    """run_sync AsyncMock dispatching on the query function identity."""

    async def _side_effect(fn: Any, *args: Any, **kwargs: Any) -> Any:
        if fn is bd._query_launch_cluster:
            return cluster
        if fn is bd._query_common_funders:
            return funders
        return None

    return _side_effect


class TestDetectBundledLaunch:
    @pytest.mark.asyncio
    async def test_scanned_bundled_launch(self) -> None:
        """Dense first-time cluster + shared funder → scanned + is_bundled."""
        cluster = {
            "deploy_block": 1000,
            "launch_buyers": [_launch_buyer(f"0x{i}", amount=100.0, deployer_funded=True) for i in range(10)],
            "cluster_blocks": [{"block_number": 1000, "buyer_count": 10}],
        }
        funders = [{"funder": "0xfeed", "funded_count": 5, "funded_addresses_sample": ["0x1", "0x2"]}]
        with patch.object(bd, "run_sync", new=AsyncMock(side_effect=launch_run_sync(cluster, funders))):
            result = await bd.detect_bundled_launch(TOKEN, "ethereum", force_fresh=True)

        assert result["result"] == "scanned"
        assert result["check"] == "bundle_launch"
        assert result["is_bundled"] is True
        assert result["launch_cluster_score"] == 100.0
        assert result["first_time_buyer_pct"] == 100.0
        assert result["similar_amount_score"] == 100.0
        assert result["deployer_funded_pct"] == 100.0
        assert result["common_funder_score"] == 70.0
        assert result["deploy_block"] == 1000
        assert result["launch_buyers"] == 10
        assert result["cluster_blocks"] == [{"block_number": 1000, "buyer_count": 10}]

    @pytest.mark.asyncio
    async def test_unavailable_when_no_cluster_data(self) -> None:
        """No ClickHouse cluster data → graceful unavailable result."""
        with patch.object(bd, "run_sync", new=AsyncMock(side_effect=launch_run_sync(None, None))):
            result = await bd.detect_bundled_launch(TOKEN, "ethereum", force_fresh=True)

        assert result["result"] == "unavailable"
        assert result["is_bundled"] is False
        assert result["confidence"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# Wallet freshness — pure aggregation
# ═══════════════════════════════════════════════════════════════════════════


def _holder_detail(
    holder: str,
    age_days: float,
    first_tx_is_token: bool,
    funding_type: str = "other",
) -> dict[str, Any]:
    return {
        "holder": holder,
        "age_days": age_days,
        "fresh": age_days < fa._FRESH_WALLET_AGE_DAYS,
        "first_seen_at": None,
        "first_tx_token": TOKEN if first_tx_is_token else "0xOther",
        "first_tx_is_token": first_tx_is_token,
        "funder": UNKNOWN,
        "funder_label": None,
        "funding_type": funding_type,
    }


class TestAggregateWalletMetrics:
    def test_mixed_holders_percentages(self) -> None:
        """fresh/cex/first_tx counts aggregate into percentages."""
        details = [
            _holder_detail("0x1", age_days=2, first_tx_is_token=True, funding_type="exchange_funded"),
            _holder_detail("0x2", age_days=10, first_tx_is_token=True, funding_type="exchange_funded"),
            _holder_detail("0x3", age_days=120, first_tx_is_token=False, funding_type="other"),
        ]
        out = fa._aggregate_wallet_metrics(details, TOKEN, "ethereum")

        assert out["result"] == "scanned"
        assert out["holders_analyzed"] == 3
        assert out["fresh_wallet_pct"] == 66.7
        assert out["cex_funded_pct"] == 66.7
        assert out["first_tx_pct"] == 66.7
        assert out["fresh_wallet_count"] == 2
        assert out["cex_funded_count"] == 2
        assert out["first_tx_count"] == 2

    def test_fresh_threshold_is_exclusive(self) -> None:
        """Wallets >= 30 days old are NOT fresh; 29.9 is fresh."""
        details = [
            _holder_detail("0xyoung", age_days=29.9, first_tx_is_token=False),
            _holder_detail("0xold", age_days=30.0, first_tx_is_token=False),
        ]
        out = fa._aggregate_wallet_metrics(details, TOKEN, "ethereum")
        assert out["fresh_wallet_pct"] == 50.0

    def test_empty_details_unavailable(self) -> None:
        out = fa._aggregate_wallet_metrics([], TOKEN, "ethereum")
        assert out["result"] == "unavailable"
        assert out["holders_analyzed"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# Wallet freshness — compute_wallet_metrics orchestration (ClickHouse mocked)
# ═══════════════════════════════════════════════════════════════════════════


def metrics_run_sync(
    first_tx_rows: list[tuple] | None = None,
    first_funder_rows: list[tuple] | None = None,
    label_rows: list[tuple] | None = None,
    tx_count_rows: list[tuple] | None = None,
) -> Any:
    """run_sync AsyncMock dispatching on the query function identity."""

    async def _side_effect(fn: Any, *args: Any, **kwargs: Any) -> Any:
        if fn in (fa._query_holder_first_tx, fa._query_holder_first_tx_in, fa._query_holder_first_tx_out):
            return first_tx_rows or []
        if fn is fa._query_first_funders:
            return first_funder_rows or []
        if fn is fa._query_batch_labels:
            return label_rows or []
        if fn is fa._query_funder_tx_counts:
            return tx_count_rows or []
        if fn is fa._query_top_holders:
            return []
        if fn is fa._query_token_coverage_start:
            return None
        return []

    return _side_effect


class TestComputeWalletMetrics:
    @pytest.mark.asyncio
    async def test_scanned_metrics_with_holders(self) -> None:
        """Holders passed in → scanned with correct percentages."""
        now = datetime.now(UTC)
        first_tx_rows = [
            ("0x1", now - timedelta(days=2), TOKEN, "0xtx1"),
            ("0x2", now - timedelta(days=10), TOKEN, "0xtx2"),
            ("0x3", now - timedelta(days=120), "0xOther", "0xtx3"),
        ]
        first_funder_rows = [
            ("0x1", BINANCE_HOT, "0xftx1", now - timedelta(days=3)),
            ("0x2", UNKNOWN, "0xftx2", now - timedelta(days=11)),
            ("0x3", UNKNOWN, "0xftx3", now - timedelta(days=121)),
        ]
        label_rows = [
            (BINANCE_HOT, "Binance 7", "exchange", "hot", "test", 0),
        ]
        tx_count_rows = [(BINANCE_HOT, 500), (UNKNOWN, 50)]

        with (
            patch.object(
                fa,
                "run_sync",
                new=AsyncMock(
                    side_effect=metrics_run_sync(
                        first_tx_rows=first_tx_rows,
                        first_funder_rows=first_funder_rows,
                        label_rows=label_rows,
                        tx_count_rows=tx_count_rows,
                    )
                ),
            ),
            patch.object(fa, "_redis_get", new=AsyncMock(return_value=None)),
            patch.object(fa, "_redis_set", new=AsyncMock(return_value=True)),
        ):
            result = await fa.compute_wallet_metrics(TOKEN, "ethereum", holders=["0x1", "0x2", "0x3"], force_fresh=True)

        assert result["result"] == "scanned"
        assert result["holders_analyzed"] == 3
        # 0x1 + 0x2 wallets <30 days old
        assert result["fresh_wallet_pct"] == 66.7
        # 0x1 funded by Binance hot wallet
        assert result["cex_funded_pct"] == 33.3
        # 0x1 + 0x2 first-ever tx is this token
        assert result["first_tx_pct"] == 66.7

    @pytest.mark.asyncio
    async def test_unavailable_when_no_first_tx_data(self) -> None:
        """No wallet-age rows → graceful unavailable (no false fresh 100%)."""
        with (
            patch.object(fa, "run_sync", new=AsyncMock(side_effect=metrics_run_sync(first_tx_rows=[]))),
            patch.object(fa, "_redis_get", new=AsyncMock(return_value=None)),
            patch.object(fa, "_redis_set", new=AsyncMock(return_value=True)),
        ):
            result = await fa.compute_wallet_metrics(TOKEN, "ethereum", holders=["0x1", "0x2"], force_fresh=True)

        assert result["result"] == "unavailable"
        assert result["fresh_wallet_pct"] == 0.0
        assert result["cex_funded_pct"] == 0.0
        assert result["first_tx_pct"] == 0.0

    @pytest.mark.asyncio
    async def test_unavailable_when_no_holders(self) -> None:
        """No holder data → graceful unavailable."""
        with (
            patch.object(fa, "run_sync", new=AsyncMock(side_effect=metrics_run_sync())),
            patch.object(fa, "_redis_get", new=AsyncMock(return_value=None)),
            patch.object(fa, "_redis_set", new=AsyncMock(return_value=True)),
        ):
            result = await fa.compute_wallet_metrics(TOKEN, "ethereum", force_fresh=True)

        assert result["result"] == "unavailable"


# ═══════════════════════════════════════════════════════════════════════════
# Scoring integration
# ═══════════════════════════════════════════════════════════════════════════


class TestScoringBundleDetected:
    def test_bundled_launch_penalized(self) -> None:
        """is_bundled → BUNDLE_DETECTED risk flag, -25 security penalty."""
        r = _result(
            bundle_analysis={
                "check": "bundle_launch",
                "result": "scanned",
                "is_bundled": True,
                "confidence": 75.0,
                "launch_cluster_score": 100.0,
            }
        )
        out = compute_score(r)
        assert "BUNDLE_DETECTED" in r.risk_flags
        assert out["score"] == 50.0 + BUNDLE_PENALTY  # 25.0
        assert out["breakdown"]["security"]["delta"] == BUNDLE_PENALTY

    def test_high_launch_cluster_score_flags_even_if_not_flagged(self) -> None:
        """launch_cluster_score >= 50 flags BUNDLE_DETECTED even when is_bundled False."""
        r = _result(
            bundle_analysis={
                "check": "bundle_launch",
                "result": "scanned",
                "is_bundled": False,
                "launch_cluster_score": 60.0,
            }
        )
        out = compute_score(r)
        assert "BUNDLE_DETECTED" in r.risk_flags
        assert out["score"] == 25.0

    def test_clean_bundle_scan_no_flag(self) -> None:
        """Scanned but not bundled and low cluster score → no penalty."""
        r = _result(
            bundle_analysis={
                "check": "bundle_launch",
                "result": "scanned",
                "is_bundled": False,
                "launch_cluster_score": 10.0,
            }
        )
        out = compute_score(r)
        assert "BUNDLE_DETECTED" not in r.risk_flags
        assert out["score"] == 50.0


class TestScoringWalletMetrics:
    def test_farming_risk_flag(self) -> None:
        """fresh_wallet_pct > 70 → FARMING_RISK, -15 holders penalty."""
        r = _result(
            wallet_metrics={
                "holders_analyzed": 10,
                "fresh_wallet_pct": 80.0,
                "cex_funded_pct": 5.0,
                "first_tx_pct": 10.0,
            }
        )
        out = compute_score(r)
        assert "FARMING_RISK" in r.risk_flags
        assert out["score"] == 50.0 - 15  # 35.0
        assert out["breakdown"]["holders"]["delta"] == -15.0

    def test_institutional_green_flag(self) -> None:
        """cex_funded_pct > 40 → INSTITUTIONAL green flag, +10 holders bonus."""
        r = _result(
            wallet_metrics={
                "holders_analyzed": 10,
                "fresh_wallet_pct": 10.0,
                "cex_funded_pct": 50.0,
                "first_tx_pct": 5.0,
            }
        )
        out = compute_score(r)
        assert "INSTITUTIONAL" in r.green_flags
        assert "INSTITUTIONAL" not in r.risk_flags
        assert out["score"] == 50.0 + 10  # 60.0

    def test_farming_and_institutional_together(self) -> None:
        """Both signals present → both flags, net -5 holders delta."""
        r = _result(
            wallet_metrics={
                "holders_analyzed": 10,
                "fresh_wallet_pct": 85.0,
                "cex_funded_pct": 45.0,
                "first_tx_pct": 30.0,
            }
        )
        out = compute_score(r)
        assert "FARMING_RISK" in r.risk_flags
        assert "INSTITUTIONAL" in r.green_flags
        assert out["score"] == 50.0 - 15 + 10  # 45.0

    def test_low_freshness_no_farm_flag(self) -> None:
        """fresh_wallet_pct <= 70 → no FARMING_RISK."""
        r = _result(
            wallet_metrics={
                "holders_analyzed": 10,
                "fresh_wallet_pct": 65.0,
                "cex_funded_pct": 10.0,
                "first_tx_pct": 5.0,
            }
        )
        out = compute_score(r)
        assert "FARMING_RISK" not in r.risk_flags
        assert out["score"] == 50.0
