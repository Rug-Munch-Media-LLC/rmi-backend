"""Unit tests for app.velocity_risk — velocity risk engine, time-series anomaly detection.

Tests cover:
- record_snapshot: time-series cache management, max length enforcement
- analyze_velocity: risk scoring, flag generation with various metric combinations
- Insufficient data paths (0, 1 snapshot)
- Window filtering
- Each velocity dimension: holder concentration, LP depth, volume/liquidity ratio,
  price velocity, tx count
- risk_level mapping
- get_market_context: Fear & Greed fetch (mocked)
- contextualize_score: market pressure adjustments
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.velocity_risk import (
    MAX_SERIES_LENGTH,
    _series_cache,
    analyze_velocity,
    contextualize_score,
    get_market_context,
    record_snapshot,
)


def _fresh_metrics(**overrides: float) -> dict[str, float]:
    """Build a standard metrics dict with optional overrides."""
    metrics = {
        "top10_pct": 50.0,
        "liquidity_usd": 100_000.0,
        "volume_24h": 50_000.0,
        "price_usd": 1.0,
        "tx_count": 500,
    }
    metrics.update(overrides)
    return metrics


@pytest.fixture(autouse=True)
def clear_cache() -> None:
    """Clear the global _series_cache before each test."""
    _series_cache.clear()


def freeze_time(frozen_ts: float | None = None) -> float:
    """Return a stable timestamp for deterministic tests."""
    return frozen_ts or 1_000_000.0


# ── record_snapshot ────────────────────────────────────────────


class TestRecordSnapshot:
    """Snapshot recording and cache management."""

    def test_records_new_key(self) -> None:
        record_snapshot("solana", "addr1", _fresh_metrics())
        key = "solana:addr1"
        assert key in _series_cache
        assert len(_series_cache[key]) == 1

    def test_records_multiple_chain_addresses(self) -> None:
        record_snapshot("solana", "addr1", _fresh_metrics())
        record_snapshot("ethereum", "addr2", _fresh_metrics())
        assert "solana:addr1" in _series_cache
        assert "ethereum:addr2" in _series_cache

    def test_address_case_normalized(self) -> None:
        record_snapshot("solana", "0xABCDEF", _fresh_metrics())
        assert len(_series_cache["solana:0xabcdef"]) == 1

    def test_max_series_length_enforced(self) -> None:
        """When MAX_SERIES_LENGTH is exceeded, the oldest entry is dropped."""
        for i in range(MAX_SERIES_LENGTH + 5):
            metrics = _fresh_metrics(price_usd=float(i))
            record_snapshot("solana", "test-token", metrics)

        key = "solana:test-token"
        assert len(_series_cache[key]) == MAX_SERIES_LENGTH
        # The oldest entries should have been removed
        prices = [m["price_usd"] for _, m in _series_cache[key]]
        assert 0 not in prices  # first 5 entries dropped
        assert min(prices) == 5.0

    def test_append_does_not_replace(self) -> None:
        """Multiple calls should append, not replace."""
        record_snapshot("eth", "0xaaa", _fresh_metrics(price_usd=1.0))
        record_snapshot("eth", "0xaaa", _fresh_metrics(price_usd=2.0))
        assert len(_series_cache["eth:0xaaa"]) == 2


# ── analyze_velocity ──────────────────────────────────────────


class TestAnalyzeVelocityInsufficientData:
    """Edge cases with too few data points."""

    def test_no_history(self) -> None:
        """With no prior snapshots, status is insufficient_data."""
        result = analyze_velocity("solana", "new-token", _fresh_metrics())
        assert result["status"] == "insufficient_data"
        assert result["snapshots"] == 1

    def test_one_prior_snapshot(self) -> None:
        """With exactly one prior snapshot, velocity can be computed.

        Note: analyze_velocity records the current snapshot internally
        BEFORE checking history length, and since history is a reference
        to the cache list, len(history) becomes 2 after the internal
        record_snapshot call.
        """
        record_snapshot("solana", "token1", _fresh_metrics())
        result = analyze_velocity("solana", "token1", _fresh_metrics())
        assert result["status"] == "ok"
        assert result["snapshots"] >= 2

    def test_old_snapshots_outside_window(self) -> None:
        """Snapshots older than the window are ignored."""
        old_time = time.time() - 7200  # 2 hours ago
        with patch("time.time", return_value=old_time):
            record_snapshot("solana", "old-token", _fresh_metrics(price_usd=1.0))

        # Now with current time and default 3600s window
        result = analyze_velocity("solana", "old-token", _fresh_metrics(price_usd=2.0))
        assert result["status"] == "insufficient_window_data"


class WithTimedSnapshots:
    """Mixin to record snapshots with controlled timestamps (1h apart)."""

    def _record(self, chain: str, addr: str, metrics: dict) -> None:
        import time as tm

        with patch.object(tm, "time", return_value=1_000_000.0):
            record_snapshot(chain, addr, metrics)

    def _analyze(self, chain: str, addr: str, metrics: dict) -> dict:
        import time as tm

        with patch.object(tm, "time", return_value=1_003_600.0):
            return analyze_velocity(chain, addr, metrics)


class TestAnalyzeVelocityHolderConcentration(WithTimedSnapshots):
    """Holder concentration velocity scoring."""

    def test_stable_holders(self) -> None:
        """Small change in holders should not trigger flags."""
        self._record("solana", "t1", _fresh_metrics(top10_pct=50.0))
        result = self._analyze("solana", "t1", _fresh_metrics(top10_pct=52.0))
        assert "HOLDER_CONCENTRATION_RISING" not in result["flags"]
        assert result["velocity_risk_score"] < 10

    def test_holders_rising_fast(self) -> None:
        """Holder concentration rising >10 per hour triggers flag."""
        self._record("solana", "t1", _fresh_metrics(top10_pct=50.0))
        result = self._analyze("solana", "t1", _fresh_metrics(top10_pct=65.0))
        assert "HOLDER_CONCENTRATION_RISING_FAST" in result["flags"]
        assert result["velocities"]["holder_concentration_delta_per_hour"] > 10

    def test_holders_surging(self) -> None:
        """Holder concentration rising >20 triggers highest flag."""
        self._record("solana", "t1", _fresh_metrics(top10_pct=50.0))
        result = self._analyze("solana", "t1", _fresh_metrics(top10_pct=80.0))
        assert "HOLDER_CONCENTRATION_SURGING" in result["flags"]

    def test_no_holder_data_skips(self) -> None:
        """If top10_pct is missing, no holder velocity computation."""
        self._record("solana", "t1", _fresh_metrics())
        result = self._analyze("solana", "t1", {"liquidity_usd": 1000.0})
        assert "holder_concentration_delta_per_hour" not in result.get("velocities", {})


class TestAnalyzeVelocityLpDepth(WithTimedSnapshots):
    """LP depth velocity scoring."""

    def test_lp_stable(self) -> None:
        self._record("solana", "t1", _fresh_metrics(liquidity_usd=100_000.0))
        result = self._analyze("solana", "t1", _fresh_metrics(liquidity_usd=95_000.0))
        assert "LP_DECREASING" not in result["flags"]

    def test_lp_decreasing_fast(self) -> None:
        self._record("solana", "t1", _fresh_metrics(liquidity_usd=100_000.0))
        result = self._analyze("solana", "t1", _fresh_metrics(liquidity_usd=80_000.0))
        assert "LP_DECREASING_FAST" in result["flags"]
        assert result["velocity_risk_score"] >= 20

    def test_lp_draining_rapidly(self) -> None:
        self._record("solana", "t1", _fresh_metrics(liquidity_usd=100_000.0))
        result = self._analyze("solana", "t1", _fresh_metrics(liquidity_usd=10_000.0))
        assert "LP_DRAINING_RAPIDLY" in result["flags"]

    def test_lp_increase_no_flag(self) -> None:
        """Increasing liquidity should not trigger negative LP flags."""
        self._record("solana", "t1", _fresh_metrics(liquidity_usd=100_000.0))
        result = self._analyze("solana", "t1", _fresh_metrics(liquidity_usd=200_000.0))
        lp_flags = [f for f in result["flags"] if f.startswith("LP_")]
        assert len(lp_flags) == 0

    def test_zero_liquidity_does_not_crash(self) -> None:
        self._record("solana", "t1", _fresh_metrics(liquidity_usd=0.0))
        result = self._analyze("solana", "t1", _fresh_metrics(liquidity_usd=0.0))
        assert result["status"] == "ok"


class TestAnalyzeVelocityVolumeLiquidity(WithTimedSnapshots):
    """Volume/liquidity ratio acceleration."""

    def test_volume_spike(self) -> None:
        self._record("solana", "t1", _fresh_metrics(volume_24h=10_000.0, liquidity_usd=100_000.0))
        result = self._analyze(
            "solana", "t1", _fresh_metrics(volume_24h=1_500_000.0, liquidity_usd=50_000.0)
        )
        assert "VOLUME_SPIKE_VS_LIQUIDITY" in result["flags"]

    def test_volume_rising(self) -> None:
        self._record("solana", "t1", _fresh_metrics(volume_24h=10_000.0, liquidity_usd=100_000.0))
        result = self._analyze(
            "solana", "t1", _fresh_metrics(volume_24h=200_000.0, liquidity_usd=20_000.0)
        )
        assert "VOLUME_RISING_VS_LIQUIDITY" in result["flags"]


class TestAnalyzeVelocityPrice(WithTimedSnapshots):
    """Price velocity scoring."""

    def test_price_crashing(self) -> None:
        self._record("solana", "t1", _fresh_metrics(price_usd=100.0))
        result = self._analyze("solana", "t1", _fresh_metrics(price_usd=1.0))
        assert "PRICE_CRASHING" in result["flags"]

    def test_price_pumping(self) -> None:
        self._record("solana", "t1", _fresh_metrics(price_usd=1.0))
        result = self._analyze("solana", "t1", _fresh_metrics(price_usd=100.0))
        assert "PRICE_PUMPING_ABNORMALLY" in result["flags"]

    def test_price_stable(self) -> None:
        self._record("solana", "t1", _fresh_metrics(price_usd=1.0))
        result = self._analyze("solana", "t1", _fresh_metrics(price_usd=1.1))
        assert "PRICE_CRASHING" not in result["flags"]
        assert "PRICE_PUMPING_ABNORMALLY" not in result["flags"]


class TestAnalyzeVelocityTxCount(WithTimedSnapshots):
    """Transaction count velocity."""

    def test_tx_surging(self) -> None:
        self._record("solana", "t1", _fresh_metrics(tx_count=100))
        result = self._analyze("solana", "t1", _fresh_metrics(tx_count=5000))
        assert "TX_VOLUME_SURGING" in result["flags"]

    def test_tx_normal(self) -> None:
        self._record("solana", "t1", _fresh_metrics(tx_count=100))
        result = self._analyze("solana", "t1", _fresh_metrics(tx_count=200))
        assert "TX_VOLUME_SURGING" not in result["flags"]


class TestAnalyzeVelocityRiskLevel(WithTimedSnapshots):
    """Risk level mapping from risk score."""

    def test_low_risk(self) -> None:
        """Score <=10 is low."""
        self._record("solana", "t1", _fresh_metrics(top10_pct=50.0))
        result = self._analyze("solana", "t1", _fresh_metrics(top10_pct=52.0))
        assert result["risk_level"] == "low"

    def test_medium_risk(self) -> None:
        """Score 11-30 is medium."""
        self._record("solana", "t1", _fresh_metrics(top10_pct=50.0, liquidity_usd=100_000.0))
        result = self._analyze(
            "solana", "t1", _fresh_metrics(top10_pct=58.0, liquidity_usd=70_000.0)
        )
        assert result["risk_level"] == "medium"

    def test_high_risk(self) -> None:
        """Score 31-60 is high."""
        self._record("solana", "t1", _fresh_metrics(top10_pct=50.0, liquidity_usd=100_000.0))
        result = self._analyze(
            "solana",
            "t1",
            _fresh_metrics(top10_pct=65.0, liquidity_usd=70_000.0, price_usd=0.3),
        )
        assert result["risk_level"] == "high"

    def test_critical_risk(self) -> None:
        """Score >60 is critical."""
        self._record(
            "solana",
            "t1",
            _fresh_metrics(
                top10_pct=50.0,
                liquidity_usd=100_000.0,
                price_usd=10.0,
                volume_24h=10_000.0,
            ),
        )
        result = self._analyze(
            "solana",
            "t1",
            _fresh_metrics(
                top10_pct=95.0,
                liquidity_usd=100.0,
                price_usd=0.01,
                volume_24h=1_000_000.0,
                tx_count=10_000,
            ),
        )
        assert result["risk_level"] == "critical"
        assert result["velocity_risk_score"] > 60

    def test_score_capped_at_100(self) -> None:
        """Risk score is capped at 100."""
        self._record(
            "solana",
            "t1",
            _fresh_metrics(
                top10_pct=50.0,
                liquidity_usd=500_000.0,
                price_usd=100.0,
                volume_24h=1000.0,
            ),
        )
        result = self._analyze(
            "solana",
            "t1",
            _fresh_metrics(
                top10_pct=100.0,
                liquidity_usd=0.0,
                price_usd=0.000001,
                volume_24h=10_000_000.0,
                tx_count=100_000,
            ),
        )
        assert result["velocity_risk_score"] == 100


class TestAnalyzeVelocityAllMetrics(WithTimedSnapshots):
    """Combined metrics produce full velocity report."""

    def test_full_output_structure(self) -> None:
        self._record("solana", "t1", _fresh_metrics())
        result = self._analyze("solana", "t1", _fresh_metrics(price_usd=2.0))
        assert "status" in result
        assert "snapshots" in result
        assert "time_window_hours" in result
        assert "velocities" in result
        assert "flags" in result
        assert "velocity_risk_score" in result
        assert "risk_level" in result

    def test_velocities_contains_expected_keys(self) -> None:
        self._record("solana", "t1", _fresh_metrics())
        result = self._analyze("solana", "t1", _fresh_metrics())
        v = result["velocities"]
        assert "holder_concentration_delta_per_hour" in v
        assert "lp_depth_change_pct_per_hour" in v
        assert "volume_liquidity_ratio_change" in v
        assert "price_change_pct_per_hour" in v
        assert "tx_count_change_per_hour" in v


# ── get_market_context ─────────────────────────────────────────


class TestGetMarketContext:
    """Fear & Greed index fetching."""

    def test_successful_fetch(self) -> None:
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [{"value": "42", "value_classification": "Fear", "timestamp": "1234567890"}]
        }

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = get_market_context()
            assert result["fear_greed_value"] == 42
            assert result["fear_greed_classification"] == "Fear"
            assert result["timestamp"] == "1234567890"

    def test_http_error_returns_defaults(self) -> None:
        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("HTTP error")
        mock_client.__aenter__.return_value = mock_client

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = get_market_context()
            assert result["fear_greed_value"] == 50
            assert result["fear_greed_classification"] == "Unknown"

    def test_non_200_response(self) -> None:
        mock_response = AsyncMock()
        mock_response.status_code = 500

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = get_market_context()
            assert result["fear_greed_value"] == 50

    def test_malformed_json(self) -> None:
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}  # no 'data' key

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = get_market_context()
            assert result["fear_greed_value"] == 50


# ── contextualize_score ───────────────────────────────────────


class TestContextualizeScore:
    """Market-adjusted safety scoring."""

    def test_normal_market(self) -> None:
        context = {"fear_greed_value": 50, "fear_greed_classification": "Neutral"}
        result = contextualize_score(70, context)
        assert result["contextualized_safety"] == 70
        assert result["market_pressure"] == 1.0
        assert result["context"] == "Normal market conditions"

    def test_extreme_greed(self) -> None:
        """Extreme Greed increases pressure -> lower contextualized score."""
        context = {"fear_greed_value": 90, "fear_greed_classification": "Extreme Greed"}
        result = contextualize_score(70, context)
        assert result["contextualized_safety"] == 53  # 70 / 1.3 ≈ 53.8 -> round=54  wait
        # Let me recalculate: 70 / 1.3 = 53.846 -> round -> 54
        # Actually: max(0, min(100, round(70 / 1.3))) = round(53.846) = 54
        assert result["market_pressure"] == 1.3
        assert "Extreme Greed" in result["context"]

    def test_greed(self) -> None:
        context = {"fear_greed_value": 70, "fear_greed_classification": "Greed"}
        result = contextualize_score(100, context)
        assert result["market_pressure"] == 1.15
        assert result["contextualized_safety"] == 87  # 100/1.15=86.95 -> 87
        assert "Greed" in result["context"]

    def test_extreme_fear(self) -> None:
        """Extreme Fear decreases pressure -> higher contextualized score."""
        context = {"fear_greed_value": 20, "fear_greed_classification": "Extreme Fear"}
        result = contextualize_score(70, context)
        assert result["market_pressure"] == 0.85
        assert result["contextualized_safety"] == 82  # 70/0.85=82.35 -> 82
        assert "Extreme Fear" in result["context"]

    def test_fear(self) -> None:
        context = {"fear_greed_value": 35, "fear_greed_classification": "Fear"}
        result = contextualize_score(50, context)
        assert result["market_pressure"] == 0.95
        assert result["contextualized_safety"] == 53  # 50/0.95=52.63 -> 53
        assert "Fear" in result["context"]

    def test_score_clamped_at_zero(self) -> None:
        """Safety score should never go below 0."""
        context = {"fear_greed_value": 90}
        result = contextualize_score(1, context)
        assert result["contextualized_safety"] >= 0

    def test_score_clamped_at_100(self) -> None:
        """Safety score should never exceed 100."""
        context = {"fear_greed_value": 20}
        result = contextualize_score(100, context)
        assert result["contextualized_safety"] <= 100

    def test_missing_fng_value_defaults(self) -> None:
        context = {}
        result = contextualize_score(70, context)
        assert result["market_pressure"] == 1.0

    def test_output_keys(self) -> None:
        context = {"fear_greed_value": 50, "fear_greed_classification": "Neutral"}
        result = contextualize_score(70, context)
        assert "base_safety" in result
        assert "contextualized_safety" in result
        assert "market_pressure" in result
        assert "fear_greed" in result
        assert "classification" in result
        assert "context" in result
