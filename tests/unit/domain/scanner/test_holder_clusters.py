"""Tests for holder_clusters — C2 BubbleMaps-style holder clustering.

Tests the pure-logic helpers (cluster passes, holder classification,
concentration metrics) without requiring ClickHouse. Query functions are
thin wrappers over ClickHouse and are covered by integration tests.

Covered:
  - _build_funder_clusters — shared-funder pass (groups >2, pool/zero/deployer exclusions)
  - _build_same_block_clusters — same-block buy pass
  - _build_bundle_clusters — bundle pass (own_bundle_detect output)
  - _classify_holder — CEX/DEX/bridge/mixer/team/deployer/sniper/bot/whale/normal
  - _classify_concentration — LOW/MEDIUM/HIGH/EXTREME thresholds
  - _compute_hhi — Herfindahl-Hirschman Index
  - _compute_gini — Gini coefficient
"""

from __future__ import annotations

from typing import Any

from app.domains.scanner.engines import holder_clusters as hc

ZERO = "0x0000000000000000000000000000000000000000"
DEAD = "0x000000000000000000000000000000000000dead"
POOL = "0x00000000000000000000000000000000000000aa"
FUNDER = "0x00000000000000000000000000000000000000bb"
DEPLOYER = "0x00000000000000000000000000000000000000cc"
TOKEN = "0x00000000000000000000000000000000000000dd"


def _first_in(funder: str | None, first_block: int = 100, first_tx: str = "0x1") -> dict[str, Any]:
    """Build a first-in record for a holder."""
    return {
        "funder": funder,
        "first_tx": first_tx,
        "first_block": first_block,
        "first_seen": None,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Pass 1 — shared funder clusters
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildFunderClusters:
    """Shared-funder cluster detection (groups >2)."""

    def test_empty_input(self) -> None:
        assert hc._build_funder_clusters({}, None, set(), TOKEN) == []

    def test_group_of_two_is_not_cluster(self) -> None:
        """Spec: groups >2 are flagged — a pair sharing a funder is not."""
        first_in = {
            "0x1": _first_in(FUNDER),
            "0x2": _first_in(FUNDER),
        }
        assert hc._build_funder_clusters(first_in, None, set(), TOKEN) == []

    def test_group_of_three_is_cluster(self) -> None:
        first_in = {
            "0x1": _first_in(FUNDER, first_block=90),
            "0x2": _first_in(FUNDER, first_block=91),
            "0x3": _first_in(FUNDER, first_block=92, first_tx="0xabc"),
        }
        clusters = hc._build_funder_clusters(first_in, None, set(), TOKEN)
        assert len(clusters) == 1
        c = clusters[0]
        assert c["reason"] == "shared_funder"
        assert set(c["addresses"]) == {"0x1", "0x2", "0x3"}
        assert c["funder"] == FUNDER
        assert c["first_block"] == 90
        assert c["confidence"] > 0.5
        assert "0xabc" in c["evidence"]["first_tx_hashes"]

    def test_pool_funder_is_excluded(self) -> None:
        """Buyers funded by a DEX pool contract all cluster — not meaningful."""
        first_in = {
            "0x1": _first_in(POOL),
            "0x2": _first_in(POOL),
            "0x3": _first_in(POOL),
        }
        assert hc._build_funder_clusters(first_in, None, {POOL}, TOKEN) == []

    def test_zero_and_dead_funders_are_excluded(self) -> None:
        first_in = {
            "0x1": _first_in(ZERO),
            "0x2": _first_in(DEAD),
            "0x3": _first_in(ZERO),
        }
        assert hc._build_funder_clusters(first_in, None, set(), TOKEN) == []

    def test_token_contract_funder_is_excluded(self) -> None:
        first_in = {
            "0x1": _first_in(TOKEN),
            "0x2": _first_in(TOKEN),
            "0x3": _first_in(TOKEN),
        }
        assert hc._build_funder_clusters(first_in, None, set(), TOKEN) == []

    def test_deployer_funder_reason_and_confidence_boost(self) -> None:
        first_in = {
            "0x1": _first_in(DEPLOYER),
            "0x2": _first_in(DEPLOYER),
            "0x3": _first_in(DEPLOYER),
        }
        clusters = hc._build_funder_clusters(first_in, DEPLOYER, set(), TOKEN)
        assert len(clusters) == 1
        assert clusters[0]["reason"] == "deployer_funded"
        assert clusters[0]["evidence"]["funder_is_deployer"] is True


# ═══════════════════════════════════════════════════════════════════════════
# Pass 2 — same-block buy clusters
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildSameBlockClusters:
    """Same-block first-buy clustering."""

    def test_empty_input(self) -> None:
        assert hc._build_same_block_clusters({}) == []

    def test_two_buyers_same_block_not_cluster(self) -> None:
        first_buy = {
            "0x1": {"first_block": 100, "first_tx": "0xa"},
            "0x2": {"first_block": 100, "first_tx": "0xb"},
        }
        assert hc._build_same_block_clusters(first_buy) == []

    def test_three_buyers_same_block_is_cluster(self) -> None:
        first_buy = {
            "0x1": {"first_block": 100, "first_tx": "0xa"},
            "0x2": {"first_block": 100, "first_tx": "0xb"},
            "0x3": {"first_block": 100, "first_tx": "0xc"},
        }
        clusters = hc._build_same_block_clusters(first_buy)
        assert len(clusters) == 1
        c = clusters[0]
        assert c["reason"] == "same_block_buy"
        assert c["first_block"] == 100
        assert c["evidence"]["block_number"] == 100
        assert c["evidence"]["buyers"] == 3

    def test_scattered_buyers_ignored(self) -> None:
        first_buy = {
            "0x1": {"first_block": 100, "first_tx": "0xa"},
            "0x2": {"first_block": 101, "first_tx": "0xb"},
            "0x3": {"first_block": 102, "first_tx": "0xc"},
        }
        assert hc._build_same_block_clusters(first_buy) == []


# ═══════════════════════════════════════════════════════════════════════════
# Pass 3 — bundle clusters
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildBundleClusters:
    """Bundle-buy groups surfaced from own_bundle_detect output."""

    def test_none_input(self) -> None:
        assert hc._build_bundle_clusters(None, {}) == []

    def test_bundle_block_maps_to_holders(self) -> None:
        bundle = {
            "confidence": 80.0,
            "bundle_blocks": [{"block_number": 200, "buyer_count": 4, "total_buys": 4}],
        }
        first_in = {
            "0x1": _first_in(None, first_block=200),
            "0x2": _first_in(None, first_block=200),
            "0x3": _first_in(None, first_block=201),
        }
        clusters = hc._build_bundle_clusters(bundle, first_in)
        assert len(clusters) == 1
        c = clusters[0]
        assert c["reason"] == "bundle"
        assert set(c["addresses"]) == {"0x1", "0x2"}
        assert c["confidence"] == 0.8
        assert c["evidence"]["buyer_count"] == 4

    def test_bundle_block_without_holder_match_ignored(self) -> None:
        bundle = {
            "confidence": 80.0,
            "bundle_blocks": [{"block_number": 999, "buyer_count": 4, "total_buys": 4}],
        }
        assert hc._build_bundle_clusters(bundle, {}) == []


# ═══════════════════════════════════════════════════════════════════════════
# Holder labeling
# ═══════════════════════════════════════════════════════════════════════════


def _holder(
    address: str = "0x1",
    balance_pct: float = 1.0,
    labels: list[dict[str, Any]] | None = None,
    funder: str | None = None,
    first_seen: float | None = None,
) -> dict[str, Any]:
    return {
        "address": address,
        "balance_pct": balance_pct,
        "labels": labels or [],
        "funder": funder,
        "first_seen": first_seen,
    }


class TestClassifyHolder:
    """Holder entity classification."""

    def test_deployer(self) -> None:
        deploy = {"deployer": DEPLOYER, "deploy_block": 100, "deploy_ts": 1000.0}
        assert hc._classify_holder(_holder(DEPLOYER), deploy) == "deployer"

    def test_cex_label(self) -> None:
        labels = [{"label_category": "cex", "label_subtype": "", "label_name": "Binance 14"}]
        assert hc._classify_holder(_holder(labels=labels), None) == "CEX"

    def test_dex_label(self) -> None:
        labels = [{"label_category": "dex", "label_subtype": "", "label_name": "Uniswap V3 Router"}]
        assert hc._classify_holder(_holder(labels=labels), None) == "DEX"

    def test_bridge_label(self) -> None:
        labels = [{"label_category": "bridge", "label_subtype": "", "label_name": "Wormhole"}]
        assert hc._classify_holder(_holder(labels=labels), None) == "bridge"

    def test_mixer_label(self) -> None:
        labels = [{"label_category": "mixer", "label_subtype": "", "label_name": "Tornado Cash"}]
        assert hc._classify_holder(_holder(labels=labels), None) == "mixer"

    def test_sniper_by_first_seen_window(self) -> None:
        deploy = {"deployer": DEPLOYER, "deploy_block": 100, "deploy_ts": 5000.0}
        holder = _holder(first_seen=5010.0)  # 10s after deploy
        assert hc._classify_holder(holder, deploy) == "sniper"

    def test_not_sniper_outside_window(self) -> None:
        deploy = {"deployer": DEPLOYER, "deploy_block": 100, "deploy_ts": 5000.0}
        holder = _holder(first_seen=10000.0)
        assert hc._classify_holder(holder, deploy) == "normal"

    def test_cex_label_beats_sniper_timing(self) -> None:
        """Institutional labels are never classified as snipers."""
        deploy = {"deployer": DEPLOYER, "deploy_block": 100, "deploy_ts": 5000.0}
        labels = [{"label_category": "cex", "label_subtype": "", "label_name": "Coinbase"}]
        holder = _holder(labels=labels, first_seen=5005.0)
        assert hc._classify_holder(holder, deploy) == "CEX"

    def test_team_by_deployer_funding(self) -> None:
        deploy = {"deployer": DEPLOYER, "deploy_block": 100, "deploy_ts": 5000.0}
        holder = _holder(funder=DEPLOYER, first_seen=20000.0)
        assert hc._classify_holder(holder, deploy) == "team"

    def test_bot_keyword(self) -> None:
        labels = [{"label_category": "bot", "label_subtype": "", "label_name": "MEV bot"}]
        assert hc._classify_holder(_holder(labels=labels), None) == "bot"

    def test_whale_by_threshold(self) -> None:
        assert hc._classify_holder(_holder(balance_pct=12.0), None) == "whale"

    def test_normal_fallback(self) -> None:
        assert hc._classify_holder(_holder(balance_pct=0.5), None) == "normal"


# ═══════════════════════════════════════════════════════════════════════════
# Concentration metrics
# ═══════════════════════════════════════════════════════════════════════════


class TestClassifyConcentration:
    """Spec: LOW (<30% top10), MEDIUM (30-50%), HIGH (50-80%), EXTREME (>80%)."""

    def test_low(self) -> None:
        assert hc._classify_concentration(25.0) == "LOW"

    def test_medium(self) -> None:
        assert hc._classify_concentration(30.0) == "MEDIUM"
        assert hc._classify_concentration(45.0) == "MEDIUM"

    def test_high(self) -> None:
        assert hc._classify_concentration(50.0) == "HIGH"
        assert hc._classify_concentration(79.9) == "HIGH"

    def test_extreme(self) -> None:
        assert hc._classify_concentration(80.0) == "EXTREME"


class TestComputeHhi:
    """Herfindahl-Hirschman Index — 0 to 10,000."""

    def test_single_holder_is_10000(self) -> None:
        assert hc._compute_hhi([1000.0]) == 10000.0

    def test_two_equal_holders_is_5000(self) -> None:
        assert hc._compute_hhi([100.0, 100.0]) == 5000.0

    def test_well_distributed_is_low(self) -> None:
        hhi = hc._compute_hhi([1.0] * 100)
        assert hhi < 1000.0

    def test_empty_is_10000(self) -> None:
        assert hc._compute_hhi([]) == 10000.0


class TestComputeGini:
    """Gini coefficient — 0 (equality) to 1 (inequality)."""

    def test_equal_distribution_is_zero(self) -> None:
        assert hc._compute_gini([10.0, 10.0, 10.0]) == 0.0

    def test_single_holder_is_zero(self) -> None:
        assert hc._compute_gini([10.0]) == 0.0

    def test_skewed_distribution_is_high(self) -> None:
        gini = hc._compute_gini([0.1, 0.1, 0.1, 100.0])
        assert 0.5 < gini <= 1.0

    def test_empty_is_zero(self) -> None:
        assert hc._compute_gini([]) == 0.0
