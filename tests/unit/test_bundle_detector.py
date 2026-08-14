"""Unit tests for app/bundle_detector.py — multi-signal bundle detection.

Covers the five detection signals (atomic-block co-occurrence, common
funder, temporal clustering, distribution anomaly, holder concentration),
the weighted combined score, risk labeling, the end-to-end detect() flow
(including the too-few-holders early exit), and the singleton getter.
Pure logic — no network, no DB.
"""

from __future__ import annotations

import pytest

from app.bundle_detector import BundleDetection, BundleDetector, get_bundle_detector


def _tx(block: int, sender: str, ts: int, recipient: str | None = None) -> dict:
    return {"blockNumber": block, "from": sender, "timestamp": ts, "to": recipient or "0xtoken"}


class TestAtomicBlockSignal:
    def test_same_block_many_wallets_scores_high(self) -> None:
        det = BundleDetector()
        r = BundleDetection(token_address="tok", chain="solana", is_bundled=False, confidence=0.0)
        txs = [_tx(100, f"0x{i}", 1000) for i in range(12)]
        det._atomic_block_signal(r, txs)
        assert r.atomic_block_score == 0.9
        assert r.wallets_in_earliest_block == 12

    def test_five_wallets_scores_0_7(self) -> None:
        det = BundleDetector()
        r = BundleDetection(token_address="tok", chain="solana", is_bundled=False, confidence=0.0)
        det._atomic_block_signal(r, [_tx(100, f"0x{i}", 1000) for i in range(5)])
        assert r.atomic_block_score == 0.7

    def test_three_wallets_scores_0_4(self) -> None:
        det = BundleDetector()
        r = BundleDetection(token_address="tok", chain="solana", is_bundled=False, confidence=0.0)
        det._atomic_block_signal(r, [_tx(100, f"0x{i}", 1000) for i in range(3)])
        assert r.atomic_block_score == 0.4

    def test_few_wallets_low_score(self) -> None:
        det = BundleDetector()
        r = BundleDetection(token_address="tok", chain="solana", is_bundled=False, confidence=0.0)
        det._atomic_block_signal(r, [_tx(100, "0x1", 1000), _tx(100, "0x2", 1000)])
        assert r.atomic_block_score == 0.1

    def test_no_block_fields_skips(self) -> None:
        det = BundleDetector()
        r = BundleDetection(token_address="tok", chain="solana", is_bundled=False, confidence=0.0)
        det._atomic_block_signal(r, [{"from": "0x1"}])
        assert r.atomic_block_score == 0.0


class TestCommonFunderSignal:
    def test_many_funded_wallets(self) -> None:
        det = BundleDetector()
        r = BundleDetection(token_address="tok", chain="solana", is_bundled=False, confidence=0.0)
        txs = [_tx(100, "0xfunder", 1000, recipient=f"0xbuyer{i}") for i in range(25)]
        det._common_funder_signal(r, txs)
        assert r.common_funder_score == 0.9
        assert r.common_funder_address == "0xfunder"
        assert r.funded_wallets_count == 25

    def test_three_funded_scores_0_3(self) -> None:
        det = BundleDetector()
        r = BundleDetection(token_address="tok", chain="solana", is_bundled=False, confidence=0.0)
        txs = [_tx(100, "0xfunder", 1000, recipient=f"0xbuyer{i}") for i in range(3)]
        det._common_funder_signal(r, txs)
        assert r.common_funder_score == 0.3

    def test_self_transfers_ignored(self) -> None:
        det = BundleDetector()
        r = BundleDetection(token_address="tok", chain="solana", is_bundled=False, confidence=0.0)
        det._common_funder_signal(r, [_tx(100, "0xsame", 1000, recipient="0xsame")])
        assert r.common_funder_score == 0.0

    def test_below_three_no_score(self) -> None:
        det = BundleDetector()
        r = BundleDetection(token_address="tok", chain="solana", is_bundled=False, confidence=0.0)
        det._common_funder_signal(r, [_tx(100, "0x1", 1000, recipient="0x2")])
        assert r.common_funder_score == 0.0


class TestTemporalSignal:
    def test_within_one_minute_scores_0_9(self) -> None:
        det = BundleDetector()
        r = BundleDetection(token_address="tok", chain="solana", is_bundled=False, confidence=0.0)
        det._temporal_signal(r, [_tx(100, "0x1", 1000), _tx(101, "0x2", 1050)])
        assert r.temporal_score == 0.9
        assert r.time_window_seconds == 50

    def test_five_minutes_scores_0_7(self) -> None:
        det = BundleDetector()
        r = BundleDetection(token_address="tok", chain="solana", is_bundled=False, confidence=0.0)
        det._temporal_signal(r, [_tx(100, "0x1", 1000), _tx(102, "0x2", 1000 + 200)])
        assert r.temporal_score == 0.7

    def test_wide_window_no_score(self) -> None:
        det = BundleDetector()
        r = BundleDetection(token_address="tok", chain="solana", is_bundled=False, confidence=0.0)
        det._temporal_signal(r, [_tx(100, "0x1", 1000), _tx(200, "0x2", 1000 + 7200)])
        assert r.temporal_score == 0.0

    def test_less_than_two_timestamps_skips(self) -> None:
        det = BundleDetector()
        r = BundleDetection(token_address="tok", chain="solana", is_bundled=False, confidence=0.0)
        det._temporal_signal(r, [_tx(100, "0x1", 1000)])
        assert r.temporal_score == 0.0


class TestDistributionAnomalySignal:
    def test_identical_amounts_flagged(self) -> None:
        det = BundleDetector()
        r = BundleDetection(token_address="tok", chain="solana", is_bundled=False, confidence=0.0)
        holders = [{"amount": 1000} for _ in range(6)]
        det._distribution_anomaly_signal(r, holders)
        assert r.identical_amount_count >= 1
        assert r.distribution_anomaly_score > 0

    def test_round_amounts_flagged(self) -> None:
        det = BundleDetector()
        r = BundleDetection(token_address="tok", chain="solana", is_bundled=False, confidence=0.0)
        holders = [{"amount": 1000} for _ in range(3)] + [{"amount": 2000} for _ in range(3)]
        det._distribution_anomaly_signal(r, holders)
        assert r.round_amount_count > 0
        assert r.distribution_anomaly_score > 0

    def test_varied_amounts_no_anomaly(self) -> None:
        det = BundleDetector()
        r = BundleDetection(token_address="tok", chain="solana", is_bundled=False, confidence=0.0)
        holders = [{"amount": 1000 + i * 137} for i in range(20)]
        det._distribution_anomaly_signal(r, holders)
        assert r.distribution_anomaly_score == 0.0

    def test_no_valid_amounts_skips(self) -> None:
        det = BundleDetector()
        r = BundleDetection(token_address="tok", chain="solana", is_bundled=False, confidence=0.0)
        det._distribution_anomaly_signal(r, [{"amount": 0}, {"amount": "x"}])
        assert r.distribution_anomaly_score == 0.0


class TestConcentrationSignal:
    def test_extreme_top3_concentration(self) -> None:
        det = BundleDetector()
        r = BundleDetection(token_address="tok", chain="solana", is_bundled=False, confidence=0.0)
        holders = [{"amount": 1000}, {"amount": 1000}, {"amount": 1000}, {"amount": 1}]
        det._concentration_signal(r, holders)
        assert r.concentration_score == 0.9
        assert r.top3_holder_pct == 100.0
        assert r.top10_holder_pct == 100.0

    def test_high_top10_concentration(self) -> None:
        det = BundleDetector()
        r = BundleDetection(token_address="tok", chain="solana", is_bundled=False, confidence=0.0)
        holders = [{"amount": 1000}] * 8 + [{"amount": 1}]
        det._concentration_signal(r, holders)
        assert r.concentration_score >= 0.7

    def test_mild_concentration(self) -> None:
        det = BundleDetector()
        r = BundleDetection(token_address="tok", chain="solana", is_bundled=False, confidence=0.0)
        # top10 ≈ 92.5% but top3 = 300/400 = 75% → 0.9 tier (top3 > 0.5)
        holders = [{"amount": 100} for _ in range(3)] + [{"amount": 10} for _ in range(10)]
        det._concentration_signal(r, holders)
        assert r.concentration_score == 0.9

    def test_balanced_top10_mild_score(self) -> None:
        det = BundleDetector()
        r = BundleDetection(token_address="tok", chain="solana", is_bundled=False, confidence=0.0)
        # top3 = 25% (≤0.3), top10 = 48.3% (>0.4, ≤0.6) → 0.4 tier
        holders = (
            [{"amount": 100} for _ in range(3)]
            + [{"amount": 40} for _ in range(7)]
            + [{"amount": 1} for _ in range(620)]
        )
        det._concentration_signal(r, holders)
        assert r.top3_holder_pct == 25.0
        assert r.top10_holder_pct == 48.3
        assert r.concentration_score == 0.4

    def test_no_amounts_skips(self) -> None:
        det = BundleDetector()
        r = BundleDetection(token_address="tok", chain="solana", is_bundled=False, confidence=0.0)
        det._concentration_signal(r, [{"amount": 0}])
        assert r.concentration_score == 0.0


class TestCombinedScore:
    def test_no_signals_scores_zero(self) -> None:
        det = BundleDetector()
        r = BundleDetection(token_address="tok", chain="solana", is_bundled=False, confidence=0.0)
        assert det._combined_score(r) == 0.0

    def test_weighted_combination(self) -> None:
        det = BundleDetector()
        r = BundleDetection(
            token_address="tok",
            chain="solana",
            is_bundled=False,
            confidence=0.0,
            atomic_block_score=0.9,
            common_funder_score=0.7,
            temporal_score=0.5,
            distribution_anomaly_score=0.3,
            concentration_score=0.2,
        )
        # 0.9*0.30 + 0.7*0.25 + 0.5*0.15 + 0.3*0.20 + 0.2*0.10 = 0.27+0.175+0.075+0.06+0.02 = 0.60
        # strong_signals = atomic(0.9) + funder(0.7) = 2 → boost *1.15
        score = det._combined_score(r)
        assert score == pytest.approx(round(0.60 * 1.15, 4), abs=1e-3)

    def test_three_strong_signals_boost_1_3(self) -> None:
        det = BundleDetector()
        r = BundleDetection(
            token_address="tok",
            chain="solana",
            is_bundled=False,
            confidence=0.0,
            atomic_block_score=0.9,
            common_funder_score=0.9,
            temporal_score=0.9,
        )
        weighted = 0.9 * 0.30 + 0.9 * 0.25 + 0.9 * 0.15  # 0.27+0.225+0.135 = 0.63
        score = det._combined_score(r)
        assert score == pytest.approx(round(min(1.0, 0.63 * 1.3), 4), abs=1e-3)


class TestRiskLabel:
    @pytest.mark.parametrize(
        ("confidence", "expected"),
        [(0.9, "critical"), (0.7, "high"), (0.5, "medium"), (0.2, "low"), (0.0, "low")],
    )
    def test_label_mapping(self, confidence: float, expected: str) -> None:
        det = BundleDetector()
        assert det._risk_label(confidence) == expected


class TestDetectFlow:
    @pytest.mark.asyncio
    async def test_too_few_holders_unknown(self) -> None:
        det = BundleDetector()
        r = await det.detect("tok", "solana", holders=[{"amount": 1}], transactions=[_tx(1, "0x1", 1)])
        assert r.is_bundled is False
        assert r.risk_label == "unknown"
        assert r.holder_count == 0

    async def test_clean_distribution_not_bundled(self) -> None:
        det = BundleDetector()
        holders = [{"amount": 1000 + i * 137} for i in range(20)]
        txs = [_tx(100 + i, f"0x{i}", 1000 + i * 300) for i in range(20)]
        r = await det.detect("tok", "solana", holders=holders, transactions=txs)
        assert r.holder_count == 20
        assert r.is_bundled is False
        assert r.risk_label in ("low", "medium")

    async def test_bundled_launch_detected(self) -> None:
        det = BundleDetector()
        # 15 wallets buy in the same block with identical amounts (Jito-style bundle)
        holders = [{"amount": 1000} for _ in range(15)]
        txs = [_tx(50, f"0xbuyer{i}", 1000) for i in range(15)]
        r = await det.detect("tok", "solana", holders=holders, transactions=txs)
        assert r.is_bundled is True
        assert r.confidence >= 0.5
        assert r.atomic_block_score == 0.9
        assert r.distribution_anomaly_score > 0

    async def test_no_transactions_still_scores_concentration(self) -> None:
        det = BundleDetector()
        holders = [{"amount": 1000} for _ in range(10)]
        r = await det.detect("tok", "solana", holders=holders, transactions=None)
        assert r.concentration_score == 0.9
        assert r.atomic_block_score == 0.0
        assert r.common_funder_score == 0.0


class TestGetBundleDetector:
    def test_singleton(self) -> None:
        assert get_bundle_detector() is get_bundle_detector()
        assert isinstance(get_bundle_detector(), BundleDetector)
