"""Unit tests for bundle_detector behavior clustering.

Covers the pure-logic helpers in ``app.domains.scanners.bundle_detector``:

- ``_cluster_by_behavior`` — clusters early buyers by identical trading
  patterns (same trade size ±1%, same block / 1-block adjacency, same DEX
  route, same Solana compute budget / priority fee).
- ``_calculate_risk`` — behavior-cluster weighting (15pt cap, 25pt cap for
  high-confidence same_size + same_time clusters).
- ``BehaviorCluster`` / ``_EarlyBuyer`` dataclass shape.

No network access — all clustering paths are pure computation.
"""

from __future__ import annotations

from app.domains.scanners.bundle_detector import (
    BehaviorCluster,
    BundleDetector,
    _EarlyBuyer,
)

# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _detector() -> BundleDetector:
    """Construct a BundleDetector without __init__ side effects."""
    return BundleDetector.__new__(BundleDetector)


def _buyer(
    address: str,
    *,
    buy_amount: float = 0.0,
    first_buy_block: int = 0,
    compute_units: int = 0,
    priority_fee: float = 0.0,
    dex_route: str = "",
) -> _EarlyBuyer:
    return _EarlyBuyer(
        address=address,
        buy_amount=buy_amount,
        first_buy_block=first_buy_block,
        compute_units=compute_units,
        priority_fee=priority_fee,
        dex_route=dex_route,
    )


def _patterns(clusters: list[BehaviorCluster]) -> list[str]:
    return sorted(c.pattern for c in clusters)


def _wallets(clusters: list[BehaviorCluster]) -> list[list[str]]:
    return sorted((sorted(c.wallets) for c in clusters), key=lambda w: w[0])


# ═══════════════════════════════════════════════════════════════════════════
# _cluster_by_behavior
# ═══════════════════════════════════════════════════════════════════════════


class TestClusterByBehavior:
    def test_empty_buyers_returns_empty(self) -> None:
        assert _detector()._cluster_by_behavior([]) == []

    def test_single_buyer_returns_empty(self) -> None:
        assert _detector()._cluster_by_behavior([_buyer("a")]) == []

    def test_two_buyers_no_shared_signal_returns_empty(self) -> None:
        buyers = [
            _buyer("a", buy_amount=100.0, first_buy_block=100),
            _buyer("b", buy_amount=9000.0, first_buy_block=500),
        ]
        assert _detector()._cluster_by_behavior(buyers) == []

    def test_same_trade_size_clusters(self) -> None:
        buyers = [
            _buyer("a", buy_amount=1_000_000.0, first_buy_block=100),
            _buyer("b", buy_amount=1_000_000.0, first_buy_block=105),
        ]
        clusters = _detector()._cluster_by_behavior(buyers)
        assert _patterns(clusters) == ["same_size"]
        assert _wallets(clusters) == [["a", "b"]]
        assert clusters[0].combined_percentage == 100.0

    def test_trade_size_1pct_tolerance(self) -> None:
        """0.9% difference clusters; 3% difference does not (size dimension)."""
        buyers = [
            _buyer("a", buy_amount=1000.0, first_buy_block=100),
            _buyer("b", buy_amount=1009.0, first_buy_block=100),  # 0.9% off
            _buyer("c", buy_amount=1030.0, first_buy_block=105),  # 3% off
        ]
        clusters = _detector()._cluster_by_behavior(buyers)
        # a+b share size AND time; c is isolated
        assert _wallets(clusters) == [["a", "b"]]

    def test_same_block_adjacency_clusters(self) -> None:
        buyers = [
            _buyer("a", buy_amount=500_000.0, first_buy_block=100),
            _buyer("b", buy_amount=900_000.0, first_buy_block=101),
        ]
        clusters = _detector()._cluster_by_behavior(buyers)
        assert _patterns(clusters) == ["same_time"]
        assert _wallets(clusters) == [["a", "b"]]

    def test_non_adjacent_blocks_do_not_cluster(self) -> None:
        buyers = [
            _buyer("a", buy_amount=500_000.0, first_buy_block=100),
            _buyer("b", buy_amount=750_000.0, first_buy_block=103),  # 3 blocks away
        ]
        assert _detector()._cluster_by_behavior(buyers) == []

    def test_same_size_and_same_block_is_mixed(self) -> None:
        buyers = [
            _buyer("a", buy_amount=750_000.0, first_buy_block=100),
            _buyer("b", buy_amount=750_000.0, first_buy_block=100),
        ]
        clusters = _detector()._cluster_by_behavior(buyers)
        assert _patterns(clusters) == ["mixed"]
        assert clusters[0].confidence >= 0.8

    def test_same_dex_route_clusters(self) -> None:
        buyers = [
            _buyer("a", buy_amount=100.0, first_buy_block=100, dex_route="raydium"),
            _buyer("b", buy_amount=200.0, first_buy_block=200, dex_route="raydium"),
        ]
        clusters = _detector()._cluster_by_behavior(buyers)
        assert _patterns(clusters) == ["same_dex"]
        assert _wallets(clusters) == [["a", "b"]]

    def test_different_dex_routes_do_not_cluster(self) -> None:
        buyers = [
            _buyer("a", buy_amount=100.0, first_buy_block=100, dex_route="raydium"),
            _buyer("b", buy_amount=200.0, first_buy_block=200, dex_route="orca"),
        ]
        assert _detector()._cluster_by_behavior(buyers) == []

    def test_same_compute_budget_clusters(self) -> None:
        buyers = [
            _buyer("a", buy_amount=100.0, first_buy_block=100, compute_units=1_400_000, priority_fee=0.01),
            _buyer("b", buy_amount=150.0, first_buy_block=200, compute_units=1_400_000, priority_fee=0.01),
        ]
        clusters = _detector()._cluster_by_behavior(buyers)
        assert len(clusters) == 1
        assert _wallets(clusters) == [["a", "b"]]

    def test_merged_multi_signal_group(self) -> None:
        """a-b share size, b-c share time → one 3-wallet mixed cluster."""
        buyers = [
            _buyer("a", buy_amount=1000.0, first_buy_block=100),
            _buyer("b", buy_amount=1000.0, first_buy_block=100),
            _buyer("c", buy_amount=2000.0, first_buy_block=101),
        ]
        clusters = _detector()._cluster_by_behavior(buyers)
        assert len(clusters) == 1
        assert sorted(clusters[0].wallets) == ["a", "b", "c"]
        assert clusters[0].pattern in ("same_time", "mixed")

    def test_combined_percentage_share(self) -> None:
        buyers = [
            _buyer("a", buy_amount=3_000_000.0, first_buy_block=100),
            _buyer("b", buy_amount=3_000_000.0, first_buy_block=100),
            _buyer("c", buy_amount=4_000_000.0, first_buy_block=200),
        ]
        clusters = _detector()._cluster_by_behavior(buyers)
        assert len(clusters) == 1
        assert clusters[0].combined_percentage == 60.0  # 6M / 10M

    def test_confidence_increases_with_size(self) -> None:
        two = [
            _buyer("a", buy_amount=1000.0, first_buy_block=100),
            _buyer("b", buy_amount=1000.0, first_buy_block=100),
        ]
        four = [
            _buyer("a", buy_amount=1000.0, first_buy_block=100),
            _buyer("b", buy_amount=1000.0, first_buy_block=100),
            _buyer("c", buy_amount=1000.0, first_buy_block=100),
            _buyer("d", buy_amount=1000.0, first_buy_block=100),
        ]
        c2 = _detector()._cluster_by_behavior(two)[0].confidence
        c4 = _detector()._cluster_by_behavior(four)[0].confidence
        assert c4 > c2

    def test_clusters_sorted_by_percentage_desc(self) -> None:
        buyers = [
            _buyer("a", buy_amount=1_000.0, first_buy_block=100),
            _buyer("b", buy_amount=1_000.0, first_buy_block=100),  # 2000 total
            _buyer("c", buy_amount=10_000.0, first_buy_block=300),
            _buyer("d", buy_amount=10_000.0, first_buy_block=300),  # 20000 total
            _buyer("e", buy_amount=100_000.0, first_buy_block=500),
            _buyer("f", buy_amount=100_000.0, first_buy_block=500),  # 200000 total
        ]
        clusters = _detector()._cluster_by_behavior(buyers)
        pcts = [c.combined_percentage for c in clusters]
        assert pcts == sorted(pcts, reverse=True)
        assert pcts[0] > pcts[-1]


# ═══════════════════════════════════════════════════════════════════════════
# _calculate_risk — behavior cluster weighting
# ═══════════════════════════════════════════════════════════════════════════


def _cluster(pattern: str, confidence: float) -> BehaviorCluster:
    return BehaviorCluster(
        wallets=["a", "b"],
        pattern=pattern,
        combined_percentage=50.0,
        confidence=confidence,
    )


class TestCalculateRiskBehaviorClusters:
    def test_no_clusters_no_score(self) -> None:
        score, level, warnings = _detector()._calculate_risk([], [], [], 0, 0.0)
        assert score == 0
        assert level == "LOW"
        assert warnings == []

    def test_weak_cluster_caps_at_15(self) -> None:
        """same_dex / low-confidence clusters keep the 15pt cap."""
        weak = [_cluster("same_dex", 0.5) for _ in range(10)]
        score, _level, warnings = _detector()._calculate_risk([], [], weak, 0, 0.0)
        assert score == 15
        assert any("BEHAVIOR_CLUSTER" in w for w in warnings)

    def test_high_confidence_mixed_bumps_to_25(self) -> None:
        """same_size + same_time (mixed, high confidence) → 25pt cap."""
        strong = [_cluster("mixed", 0.9) for _ in range(10)]
        score, _level, warnings = _detector()._calculate_risk([], [], strong, 0, 0.0)
        assert score == 25
        assert any("high-confidence" in w for w in warnings)

    def test_same_size_high_confidence_bumps_to_25(self) -> None:
        strong = [_cluster("same_size", 0.8) for _ in range(10)]
        score, _level, _warnings = _detector()._calculate_risk([], [], strong, 0, 0.0)
        assert score == 25

    def test_same_dex_high_confidence_not_bumped(self) -> None:
        """same_dex alone is not a size/timing signal → stays at 15 cap."""
        weak = [_cluster("same_dex", 0.9) for _ in range(10)]
        score, _level, _warnings = _detector()._calculate_risk([], [], weak, 0, 0.0)
        assert score == 15

    def test_single_strong_cluster_scores_10(self) -> None:
        score, _level, _warnings = _detector()._calculate_risk([], [], [_cluster("mixed", 0.9)], 0, 0.0)
        assert score == 10

    def test_risk_level_escalates_with_clusters(self) -> None:
        score, level, _warnings = _detector()._calculate_risk([], [], [_cluster("mixed", 0.9)] * 10, 0, 0.0)
        assert score >= 25
        assert level in ("MEDIUM", "HIGH", "CRITICAL")
