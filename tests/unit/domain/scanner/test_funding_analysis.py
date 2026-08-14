"""Tests for funding_analysis — clever wallet analysis metrics.

Covers the pure-logic helpers and aggregation (no ClickHouse required):

  Metric 1 — funding sources:
    - _classify_funder — CEX / mixer / fresh-wallet / other classification
    - _assess_funding_risk — HIGH/MEDIUM/LOW thresholds
    - _build_top_funders — cross-referenced funders ranked by funded holders
    - _funder_display_label — known_entities/wallet_labels label resolution

  Metric 2 — wallet freshness:
    - _compute_freshness_score — age/tx/CEX weighting (0-100)
    - _classify_freshness — FRESH_SUSPICIOUS / FRESH_CLEAN / ESTABLISHED bands

  Metric 3 — holder overlap:
    - _compute_overlap — overlap % and risk assessment
    - _aggregate_scam_overlap — worst known-scam-token comparison

  Scoring integration:
    - compute_score emits HIGH_EXCHANGE_FUNDED / HIGH_FRESH_WALLET_PCT /
      HOLDER_OVERLAP_SCAM flags and applies holders-dimension penalties
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.domains.scanner.engines import _io, funding_analysis as fa
from app.domains.scanner.unified.model import ScanResult
from app.domains.scanner.unified.scoring import compute_score

ZERO = "0x0000000000000000000000000000000000000000"
DEAD = "0x000000000000000000000000000000000000dead"
BINANCE_HOT = "0x28C6c06298d514Db089934071355E5743bf21d60"  # known_entities
TORNADO = "0xA160cdAB225685dA1d56aa342Ad8841c3b53f291"  # known_entities mixer
UNKNOWN = "0x1111111111111111111111111111111111111111"


def _labels(name: str = "", category: str = "", subtype: str = "") -> list[dict]:
    """Build a single wallet_labels-style record list."""
    return [
        {
            "label_name": name,
            "label_category": category,
            "label_subtype": subtype,
            "source": "test",
            "is_sanctioned": False,
        }
    ]


# ═══════════════════════════════════════════════════════════════════════════
# Metric 1 — funding source classification
# ═══════════════════════════════════════════════════════════════════════════


class TestClassifyFunder:
    def test_known_cex_address_classified_exchange(self) -> None:
        """Static known_entities CEX wallet → exchange_funded."""
        funding_type, label = fa._classify_funder(BINANCE_HOT, [], 5, "ethereum")
        assert funding_type == "exchange_funded"
        assert label is not None
        assert "Binance" in label

    def test_known_mixer_classified_mixer(self) -> None:
        """Static known_entities mixer (Tornado Cash) → mixer_funded."""
        funding_type, label = fa._classify_funder(TORNADO, [], 5, "ethereum")
        assert funding_type == "mixer_funded"
        assert label is not None
        assert "Tornado" in label

    def test_label_keyword_cex_classified_exchange(self) -> None:
        funding_type, label = fa._classify_funder(UNKNOWN, _labels(name="Coinbase Hot"), 5, "ethereum")
        assert funding_type == "exchange_funded"
        assert label == "Coinbase Hot"

    def test_label_category_cex_classified_exchange(self) -> None:
        funding_type, _label = fa._classify_funder(UNKNOWN, _labels(category="exchange"), 5, "ethereum")
        assert funding_type == "exchange_funded"

    def test_label_keyword_mixer_classified_mixer(self) -> None:
        funding_type, label = fa._classify_funder(UNKNOWN, _labels(name="Tornado Cash 1 ETH"), 5, "ethereum")
        assert funding_type == "mixer_funded"
        assert label == "Tornado Cash 1 ETH"

    def test_unlabeled_low_tx_classified_fresh(self) -> None:
        """Unlabeled funder with <=10 txs → fresh_wallet."""
        funding_type, _label = fa._classify_funder(UNKNOWN, [], 10, "ethereum")
        assert funding_type == "fresh_wallet"

    def test_unlabeled_high_tx_classified_other(self) -> None:
        funding_type, _label = fa._classify_funder(UNKNOWN, [], 50, "ethereum")
        assert funding_type == "other"

    def test_cex_keyword_beats_fresh_tx_count(self) -> None:
        """A labeled CEX wallet is exchange_funded even with few txs."""
        funding_type, _label = fa._classify_funder(UNKNOWN, _labels(name="Binance 7"), 2, "ethereum")
        assert funding_type == "exchange_funded"


class TestAssessFundingRisk:
    def test_high_when_exchange_majority(self) -> None:
        assert fa._assess_funding_risk(55.0, 10.0, 0.0) == "HIGH"

    def test_high_when_fresh_wallet_farm(self) -> None:
        assert fa._assess_funding_risk(10.0, 50.0, 0.0) == "HIGH"

    def test_medium_when_elevated_fresh(self) -> None:
        assert fa._assess_funding_risk(10.0, 25.0, 0.0) == "MEDIUM"

    def test_medium_when_mixer_present(self) -> None:
        assert fa._assess_funding_risk(10.0, 10.0, 6.0) == "MEDIUM"

    def test_low_otherwise(self) -> None:
        assert fa._assess_funding_risk(10.0, 10.0, 0.0) == "LOW"


class TestBuildTopFunders:
    def test_funders_funding_multiple_holders_surface(self) -> None:
        details = [
            {"holder": "0x1", "funder": BINANCE_HOT, "funding_type": "exchange_funded"},
            {"holder": "0x2", "funder": BINANCE_HOT, "funding_type": "exchange_funded"},
            {"holder": "0x3", "funder": UNKNOWN, "funding_type": "other"},
        ]
        top = fa._build_top_funders(details, {}, "ethereum")
        assert len(top) == 1
        assert top[0]["address"] == BINANCE_HOT
        assert top[0]["funded_holders"] == 2

    def test_single_funded_holder_not_surfaced(self) -> None:
        details = [{"holder": "0x1", "funder": UNKNOWN, "funding_type": "other"}]
        assert fa._build_top_funders(details, {}, "ethereum") == []

    def test_known_entity_label_resolved(self) -> None:
        details = [
            {"holder": "0x1", "funder": BINANCE_HOT, "funding_type": "exchange_funded"},
            {"holder": "0x2", "funder": BINANCE_HOT, "funding_type": "exchange_funded"},
        ]
        top = fa._build_top_funders(details, {}, "ethereum")
        assert "Binance" in (top[0]["label"] or "")


# ═══════════════════════════════════════════════════════════════════════════
# Metric 2 — wallet freshness
# ═══════════════════════════════════════════════════════════════════════════


class TestComputeFreshnessScore:
    def test_brand_new_cex_funded_wallet_max(self) -> None:
        assert fa._compute_freshness_score(0, 1, True) == 100

    def test_brand_new_wallet_high(self) -> None:
        assert fa._compute_freshness_score(0, 1, False) == 75

    def test_week_old_wallet(self) -> None:
        assert fa._compute_freshness_score(5, 8, False) == 65

    def test_month_old_wallet(self) -> None:
        assert fa._compute_freshness_score(20, 15, False) == 40

    def test_established_wallet_low(self) -> None:
        assert fa._compute_freshness_score(200, 500, False) == 0

    def test_cex_funding_bonus(self) -> None:
        score_cex = fa._compute_freshness_score(30, 10, True)
        score_plain = fa._compute_freshness_score(30, 10, False)
        assert score_cex == score_plain + fa._CEX_FUNDED_PTS

    def test_score_capped_at_100(self) -> None:
        assert fa._compute_freshness_score(0, 0, True) == 100


class TestClassifyFreshness:
    def test_suspicious_band(self) -> None:
        assert fa._classify_freshness(85) == "FRESH_SUSPICIOUS"

    def test_suspicious_threshold(self) -> None:
        assert fa._classify_freshness(fa._FRESH_SUSPICIOUS_MIN) == "FRESH_SUSPICIOUS"

    def test_clean_band(self) -> None:
        assert fa._classify_freshness(50) == "FRESH_CLEAN"

    def test_clean_threshold(self) -> None:
        assert fa._classify_freshness(fa._FRESH_CLEAN_MIN) == "FRESH_CLEAN"

    def test_established_band(self) -> None:
        assert fa._classify_freshness(10) == "ESTABLISHED"


# ═══════════════════════════════════════════════════════════════════════════
# Metric 3 — holder overlap
# ═══════════════════════════════════════════════════════════════════════════


class TestComputeOverlap:
    @pytest.mark.asyncio
    async def test_high_overlap_with_scam_token(self) -> None:
        """>30% overlap with a known scam token → HIGH risk."""
        with (
            patch.object(
                _io,
                "run_sync",
                new=AsyncMock(
                    side_effect=overlap_run_sync(
                        {
                            "0xaa": [("0x1",), ("0x2",), ("0x3",), ("0x4",)],
                            "0xbb": [("0x1",), ("0x2",)],
                        }
                    )
                ),
            ),
            patch.object(_io, "_query_token_scam_status", new=AsyncMock(return_value=True)),
        ):
            result = await fa._compute_overlap("0xAA", "0xBB", "ethereum")

        assert result["result"] == "scanned"
        assert result["holders_a"] == 4
        assert result["common_holders"] == 2
        assert result["overlap_pct"] == 50.0
        assert result["token_b_is_scam"] is True
        assert result["risk_assessment"] == "HIGH"

    @pytest.mark.asyncio
    async def test_low_overlap_no_risk(self) -> None:
        with (
            patch.object(
                _io,
                "run_sync",
                new=AsyncMock(
                    side_effect=overlap_run_sync(
                        {
                            "0xaa": [("0x1",), ("0x2",), ("0x3",), ("0x4",)],
                            "0xbb": [("0x1",)],  # 1/4 = 25% overlap
                        }
                    )
                ),
            ),
            patch.object(_io, "_query_token_scam_status", new=AsyncMock(return_value=True)),
        ):
            result = await fa._compute_overlap("0xAA", "0xBB", "ethereum")

        assert result["overlap_pct"] == 25.0
        assert result["risk_assessment"] == "LOW"

    @pytest.mark.asyncio
    async def test_empty_holders_a_unavailable(self) -> None:
        with (
            patch.object(
                _io,
                "run_sync",
                new=AsyncMock(side_effect=overlap_run_sync({"0xAA": [], "0xBB": [("0x1",)]})),
            ),
            patch.object(_io, "_query_token_scam_status", new=AsyncMock(return_value=False)),
        ):
            result = await fa._compute_overlap("0xAA", "0xBB", "ethereum")

        assert result["result"] == "unavailable"


class TestAggregateScamOverlap:
    def test_worst_comparison_wins(self) -> None:
        comparisons = [
            {"token_b": "0xB1", "overlap_pct": 10.0, "token_b_is_scam": True},
            {"token_b": "0xB2", "overlap_pct": 45.0, "token_b_is_scam": True},
        ]
        result = fa._aggregate_scam_overlap("0xAA", "ethereum", comparisons)
        assert result["result"] == "scanned"
        assert result["max_overlap_pct"] == 45.0
        assert result["worst_token"] == "0xB2"
        assert result["risk_assessment"] == "HIGH"

    def test_no_comparisons_unavailable(self) -> None:
        result = fa._aggregate_scam_overlap("0xAA", "ethereum", [])
        assert result["result"] == "unavailable"

    def test_overlap_without_scam_is_medium(self) -> None:
        comparisons = [
            {"token_b": "0xB1", "overlap_pct": 45.0, "token_b_is_scam": False},
        ]
        result = fa._aggregate_scam_overlap("0xAA", "ethereum", comparisons)
        assert result["risk_assessment"] == "MEDIUM"


# ═══════════════════════════════════════════════════════════════════════════
# Scoring integration
# ═══════════════════════════════════════════════════════════════════════════


def _result_with(**overrides: object) -> ScanResult:
    defaults: dict[str, object] = {
        "token_address": "0xTOKEN",
        "chain": "ethereum",
    }
    defaults.update(overrides)
    return ScanResult(**defaults)  # type: ignore[arg-type]


class TestScoringFlags:
    def test_high_exchange_funded_flag(self) -> None:
        r = _result_with(
            funding_analysis={
                "result": "scanned",
                "holders_analyzed": 10,
                "exchange_funded_pct": 60.0,
                "fresh_wallet_pct": 5.0,
                "mixer_funded_pct": 0.0,
            }
        )
        compute_score(r)
        assert "HIGH_EXCHANGE_FUNDED" in r.risk_flags
        assert r.breakdown["holders"]["delta"] <= -10

    def test_high_fresh_wallet_pct_flag(self) -> None:
        r = _result_with(
            funding_analysis={
                "result": "scanned",
                "holders_analyzed": 10,
                "exchange_funded_pct": 10.0,
                "fresh_wallet_pct": 55.0,
                "mixer_funded_pct": 0.0,
            }
        )
        compute_score(r)
        assert "HIGH_FRESH_WALLET_PCT" in r.risk_flags

    def test_holder_overlap_scam_flag(self) -> None:
        r = _result_with(
            holder_overlap={
                "result": "scanned",
                "max_overlap_pct": 40.0,
                "worst_token": "0xSCAM",
                "worst_token_is_scam": True,
                "risk_assessment": "HIGH",
            }
        )
        compute_score(r)
        assert "HOLDER_OVERLAP_SCAM" in r.risk_flags

    def test_freshness_aggregate_flag(self) -> None:
        r = _result_with(
            wallet_freshness={
                "result": "scanned",
                "analyzed": 10,
                "fresh_wallet_pct": 60.0,
                "avg_freshness_score": 80.0,
            }
        )
        compute_score(r)
        assert "HIGH_FRESH_WALLET_PCT" in r.risk_flags

    def test_no_false_flags_when_clean(self) -> None:
        r = _result_with(
            funding_analysis={
                "result": "scanned",
                "holders_analyzed": 10,
                "exchange_funded_pct": 10.0,
                "fresh_wallet_pct": 5.0,
                "mixer_funded_pct": 0.0,
            },
            holder_overlap={
                "result": "scanned",
                "max_overlap_pct": 5.0,
                "worst_token_is_scam": False,
            },
        )
        compute_score(r)
        assert "HIGH_EXCHANGE_FUNDED" not in r.risk_flags
        assert "HIGH_FRESH_WALLET_PCT" not in r.risk_flags
        assert "HOLDER_OVERLAP_SCAM" not in r.risk_flags


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def overlap_run_sync(rows_by_token: dict[str, list[tuple]]) -> Any:
    """Build a run_sync AsyncMock dispatching on the query function + token.

    ``_compute_overlap`` calls ``run_sync(_query_holder_set, token, chain)``
    concurrently for both tokens, so the mock must dispatch deterministically
    on the token argument rather than call order.
    """

    async def _side_effect(fn, *args, **kwargs):
        if getattr(fn, "__name__", "") == "_query_holder_set" and args:
            return rows_by_token.get(str(args[0]).lower(), [])
        # Identity check first: the test also patches _query_token_scam_status
        # with an AsyncMock whose __name__ is "AsyncMock", which the name
        # check below would miss.
        if fn is _io._query_token_scam_status or getattr(fn, "__name__", "") == "_query_token_scam_status":
            return True
        return []

    return _side_effect
