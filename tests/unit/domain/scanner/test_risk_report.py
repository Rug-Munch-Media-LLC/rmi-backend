"""Tests for the structured RiskReport — dataclass, ~25 factors, cluster
detection, engine wiring and formatter.

The builder (``build_risk_report``) is pure: it only reads already-fetched
``ScanResult`` fields plus the optional own-engine payloads, so the synthetic
scan below exercises the FULL report without any network / ClickHouse access.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.domains.scanner.engines.risk_cluster import (
    build_funder_tree,
    detect_cross_token_clusters,
    detect_cross_token_funder_graphs,
    detect_mev_bundles,
    detect_shared_deployer_networks,
    detect_sniper_bot_patterns,
    detect_temporal_clusters,
    detect_wash_trading_clusters,
    score_cluster_risk,
)
from app.domains.scanner.unified.model import ScanResult
from app.domains.scanner.unified.risk_report import (
    RiskFactor,
    RiskReport,
    build_risk_report,
    format_risk_report,
)

# ── Fixtures ────────────────────────────────────────────────────────────


def _synthetic_scan() -> ScanResult:
    """A realistic synthetic ScanResult for a bundled, dev-heavy launch."""
    return ScanResult(
        token_address="0xabcdef1234567890abcdef1234567890abcdef12",
        chain="ethereum",
        scanned_at=datetime.now(UTC).isoformat(),
        tier="free",
        name="Synthetic Rug Token",
        symbol="SRUG",
        price_usd=0.0001,
        market_cap_usd=1_200_000,
        liquidity_usd=18_000,
        volume_24h_usd=95_000,
        security={
            "is_honeypot": False,
            "buy_tax_pct": 12.0,
            "sell_tax_pct": 18.0,
            "is_mintable": True,
            "ownership_renounced": False,
            "owner_address": "0xdeployer0000000000000000000000000000000000",
            "checks": {"HP01": {"status": "pass"}, "CR01": {"status": "fail"}},
        },
        holders={
            "holder_count": 412,
            "top_holders": [
                {"rank": i, "address": f"0xholder{i:040x}", "percentage": pct}
                for i, pct in enumerate(
                    [25.0, 12.0, 9.0, 7.0, 6.0, 5.0, 4.0, 3.0, 2.5, 2.0, 1.5, 1.0],
                    start=1,
                )
            ],
            "top_10_pct": 75.5,
            "gini_coefficient": 0.86,
        },
        holder_health={"gini": 0.86, "hhi": 1200},
        funding_analysis={
            "result": "scanned",
            "exchange_funded_pct": 8.0,
            "fresh_wallet_pct": 55.0,
            "mixer_funded_pct": 2.0,
            "holders_analyzed": 25,
            "top_funders": [{"address": "0xdeployer0000000000000000000000000000000000", "funded_holders": 8}],
            "holder_details": [
                {
                    "holder": f"0xholder{i:040x}",
                    "funder": "0xdeployer0000000000000000000000000000000000",
                    "funding_type": "fresh_wallet",
                }
                for i in range(1, 12)
            ],
            "risk_assessment": "HIGH",
        },
        wallet_metrics={
            "result": "scanned",
            "holders_analyzed": 25,
            "fresh_wallet_pct": 55.0,
            "cex_funded_pct": 8.0,
            "first_tx_pct": 30.0,
        },
        wallet_freshness={"result": "scanned", "fresh_wallet_pct": 60.0, "analyzed": 10},
        holder_overlap={"result": "scanned", "max_overlap_pct": 41.0, "worst_token_is_scam": True},
        bundle_analysis={
            "check": "bundle_launch",
            "result": "scanned",
            "is_bundled": True,
            "confidence": 82.0,
            "launch_cluster_score": 74.0,
            "first_time_buyer_pct": 62.0,
            "deployer_funded_pct": 38.0,
            "common_funder_score": 55.0,
            "deploy_block": 19_400_000,
            "cluster_blocks": [
                {"block_number": 19_400_001, "buyer_count": 7},
                {"block_number": 19_400_002, "buyer_count": 4},
            ],
            "launch_buyers": [
                {"address": "0xdeployer0000000000000000000000000000000000", "block_number": 19_400_001},
                {"address": "0xholder1" + "0" * 34, "block_number": 19_400_001},
            ],
        },
        liquidity={"locked_pct": 12.0, "is_locked": False, "is_burned": False, "source": "own"},
        volume_auth={"fake_volume_pct": 62.0},
        dev_reputation={"risk_score": 82, "total_deployments": 6, "rug_rate": 0.7},
        sentinel={
            "free": {
                "holder_analysis": {"hhi": 1200, "total_holders": 412, "risk_level": "high"},
                "contract_authority": {
                    "can_mint": True,
                    "can_freeze": True,
                    "is_ownership_renounced": False,
                    "owner_address": "0xdeployer0000000000000000000000000000000000",
                },
            },
            "premium": {
                "honeypot_detection": {
                    "is_honeypot": False,
                    "can_sell": True,
                    "buy_tax": 12.0,
                    "sell_tax": 18.0,
                },
            },
            "premium_unlocked": True,
            "modules_run": ["holder_analysis", "contract_authority", "honeypot_detection"],
        },
        modules_run=["token_security", "holder_data", "funding_analysis", "bundle_analysis", "risk_report"],
        enrichment_sources=["wallet_analysis:own", "bundle_analysis:own"],
    )


def _synthetic_cluster_graph() -> dict[str, object]:
    """Synthetic risk_cluster engine output (all 8 structures populated)."""
    return {
        "check": "risk_cluster",
        "result": "scanned",
        "source": "own",
        "deployer": "0xdeployer0000000000000000000000000000000000",
        "holders_analyzed": 11,
        "funder_tree": [
            {
                "funder": "0xdeployer0000000000000000000000000000000000",
                "funder_is_deployer": True,
                "holders": ["0xholder1" + "0" * 34, "0xholder2" + "0" * 34, "0xholder3" + "0" * 34],
                "holder_count": 3,
                "share_pct": 27.3,
                "evidence": ["0xtx1"],
            }
        ],
        "shared_deployer_network": [
            {
                "deployer": "0xdeployer0000000000000000000000000000000000",
                "tokens": ["0xtokena", "0xtokenb"],
                "launch_count": 3,
                "shared_wallets": ["0xholder1" + "0" * 34, "0xholder2" + "0" * 34],
                "overlap": 100.0,
            }
        ],
        "cross_token_clusters": [
            {
                "token": "0xothertoken0000000000000000000000000000000000",
                "wallets": ["0xholder1" + "0" * 34, "0xholder2" + "0" * 34],
                "members": 2,
            }
        ],
        "cross_token_funders": [
            {
                "funder": "0xmasterfunder0000000000000000000000000000000",
                "tokens": ["0xta", "0xtb", "0xtc"],
                "launch_count": 3,
                "funded_wallet_count": 4,
                "funded_wallets": ["0xholder1" + "0" * 34, "0xholder2" + "0" * 34],
                "note": "funder seeded wallets in 3 other launches",
            }
        ],
        "temporal_clusters": [
            {
                "block": 19_400_001,
                "wallets": ["0xholder1" + "0" * 34, "0xholder2" + "0" * 34, "0xholder3" + "0" * 34],
                "members": 3,
                "span_blocks": 0,
                "rapid_sequence": False,
                "early_launch": True,
                "note": "3 wallets entered in the same block",
            }
        ],
        "sniper_patterns": [
            {
                "pattern": "same_block_snipe",
                "block": 19_400_001,
                "wallets": ["0xholder1" + "0" * 34, "0xholder2" + "0" * 34, "0xholder3" + "0" * 34],
                "members": 3,
                "detail": "3 wallets bought in the same launch block",
            },
            {
                "pattern": "uniform_bot_amounts",
                "cv": 0.12,
                "wallets": ["0xholder1" + "0" * 34, "0xholder2" + "0" * 34],
                "members": 2,
                "detail": "launch buys are uniform",
            },
            {
                "pattern": "fresh_wallet_bots",
                "wallets": ["0xholder1" + "0" * 34, "0xholder2" + "0" * 34, "0xholder3" + "0" * 34],
                "members": 3,
                "detail": "3 launch buyers have no on-chain history",
            },
        ],
        "mev_bundles": [
            {
                "pattern": "atomic_bundle_buy",
                "block": 19_400_001,
                "wallets": ["0xholder1" + "0" * 34, "0xholder2" + "0" * 34],
                "members": 2,
                "tx_hashes": ["0xtx1", "0xtx2"],
                "detail": "2 wallets bought in one block with uniform amounts",
            },
            {
                "pattern": "buy_then_distribute",
                "block": 19_400_001,
                "wallet": "0xholder1" + "0" * 34,
                "receivers": ["0xholder2" + "0" * 34, "0xholder3" + "0" * 34],
                "members": 2,
                "detail": "bought then split to 2 wallets",
            },
        ],
        "wash_trading": {
            "cycles": [
                {
                    "wallets": ["0xholder1" + "0" * 34, "0xholder2" + "0" * 34],
                    "cycle_length": 2,
                    "combined_volume": 5000.0,
                    "time_span_hours": 0.2,
                    "confidence": 0.9,
                    "pattern_type": "simple_roundtrip",
                    "evidence": ["2-wallet simple_roundtrip"],
                }
            ],
            "self_swaps": [{"wallet": "0xholder3" + "0" * 34, "count": 2, "volume": 1500.0}],
            "total_transfer_volume": 40_000.0,
            "cycle_volume": 5000.0,
            "cycle_volume_ratio": 0.125,
            "wash_score": 25,
        },
        "cluster_risk_score": 92,
        "cluster_risk_label": "CRITICAL",
        "cluster_risk_description": "Extreme wallet coordination.",
        "component_scores": {
            "funder_tree": 30.0,
            "shared_deployer_network": 25.0,
            "cross_token_clusters": 15.0,
            "cross_token_funders": 20.0,
            "temporal_clusters": 18.0,
            "sniper_patterns": 50.0,
            "mev_bundles": 50.0,
            "wash_trading": 12.0,
            "bundle": 32.0,
        },
    }


def _synthetic_fingerprint() -> dict[str, object]:
    """Synthetic deployer_fingerprint engine output (repeat + rugged)."""
    return {
        "check": "deployer_fingerprint",
        "result": "scanned",
        "source": "own",
        "deployer": "0xdeployer0000000000000000000000000000000000",
        "chain": "ethereum",
        "risk_bucket": "REPEAT_RUG",
        "risk_score": 87,
        "total_deployments": 6,
        "success_rate": 0.17,
        "median_lifespan_hours": 22.0,
        "first_seen": "2026-08-01T00:00:00Z",
        "funder_profile": {
            "funder_address": "0xfunder000000000000000000000000000000000000",
            "funder_is_cex": False,
            "cex_label": None,
            "funder_is_fresh": True,
            "same_funder_multiple": True,
        },
        "evidence": [{"kind": "token", "token_address": "0xoldtoken", "survived_7d": False}],
    }


def _synthetic_holder_clusters() -> dict[str, object]:
    """Synthetic holder_clusters engine output (BubbleMaps-style)."""
    rows = [
        (25.0, "deployer", "funder:0"),
        (12.0, "team", "funder:0"),
        (9.0, "team", "funder:0"),
        (7.0, "sniper", "block:0"),
        (6.0, "sniper", "block:0"),
        (5.0, "normal", None),
        (4.0, "normal", None),
        (3.0, "normal", None),
        (2.5, "normal", None),
        (2.0, "normal", None),
        (1.5, "normal", None),
    ]
    holders = [
        {
            "address": f"0xholder{i:040x}",
            "balance": 1000.0 - i * 30.0,
            "balance_pct": pct,
            "label": label,
            "cluster_id": cid,
            "first_seen": None,
        }
        for i, (pct, label, cid) in enumerate(rows, start=1)
    ]
    return {
        "check": "holder_clusters",
        "result": "scanned",
        "source": "own",
        "deployer": "0xdeployer0000000000000000000000000000000000",
        "holders": holders,
        "metrics": {"top10_pct": 76.0, "gini": 0.86, "hhi": 1200.0, "class": "HIGH", "total_holders": 412},
        "clusters": [
            {
                "id": "funder:0",
                "addresses": ["0xholder1" + "0" * 34, "0xholder2" + "0" * 34],
                "reason": "deployer_funded",
                "confidence": 0.8,
            },
            {
                "id": "block:0",
                "addresses": ["0xholder4" + "0" * 34, "0xholder5" + "0" * 34],
                "reason": "same_block_buy",
                "confidence": 0.6,
            },
        ],
        "concentration": {"level": "HIGH", "top10_pct": 76.0, "top3_pct": 46.0, "hhi": 1200.0},
        "confidence": 0.8,
        "active_24h": True,
    }


# ── RiskReport builder ─────────────────────────────────────────────────


class TestBuildRiskReport:
    """The full synthetic scan must produce the complete RiskReport."""

    def test_full_report_sections(self) -> None:
        r = _synthetic_scan()
        report = build_risk_report(
            r,
            deployer_fingerprint=_synthetic_fingerprint(),
            holder_clusters=_synthetic_holder_clusters(),
            cluster_graph=_synthetic_cluster_graph(),
        )

        assert isinstance(report, RiskReport)
        assert report.token_address == r.token_address
        assert report.chain == "ethereum"

        # All 10 requested sections present.
        assert report.deployer["address"] == "0xdeployer0000000000000000000000000000000000"
        assert report.deployer["repeat_deployer"] is True
        assert report.deployer["previously_rugged"] is True
        assert report.holders["concentration"] == "HIGH"
        assert report.holders["dev_team_share"] is not None
        assert report.first_buyer["address"] is not None
        assert len(report.top_25_holders) >= 10
        assert report.fresh_wallet_pct is not None
        assert report.fresh_wallet_pct > 40
        assert report.cex_funded_wallet_pct is not None
        assert report.bundle["is_bundled"] is True
        assert report.bundle["sniper_first_buyers"] == 11
        assert report.honeypot["is_honeypot"] is False
        assert report.honeypot["mintable"] is True
        assert report.liquidity["locked_pct"] == 12.0

        # Cluster section carries the beyond-market payload.
        assert report.cluster.cluster_risk_score == 92
        assert report.cluster.cluster_risk_label == "CRITICAL"
        assert len(report.cluster.funder_tree) == 1
        assert report.cluster.funder_tree[0]["funder_is_deployer"] is True
        assert len(report.cluster.shared_deployer_network) == 1
        assert len(report.cluster.cross_token_clusters) == 1
        # Deepened cluster structures all surface on the report.
        assert len(report.cluster.cross_token_funders) == 1
        assert report.cluster.cross_token_funders[0]["launch_count"] == 3
        assert len(report.cluster.temporal_clusters) == 1
        assert report.cluster.temporal_clusters[0]["members"] == 3
        assert len(report.cluster.sniper_patterns) == 3
        assert len(report.cluster.mev_bundles) == 2
        assert len(report.cluster.wash_clusters.get("cycles") or []) == 1
        assert report.cluster.wash_clusters.get("cycle_volume_ratio") == 0.125

    def test_all_25_factors_present(self) -> None:
        r = _synthetic_scan()
        report = build_risk_report(
            r,
            deployer_fingerprint=_synthetic_fingerprint(),
            holder_clusters=_synthetic_holder_clusters(),
            cluster_graph=_synthetic_cluster_graph(),
        )

        keys = {f.key for f in report.factors}
        assert len(report.factors) == 30, f"expected 30 factors, got {len(report.factors)}"
        assert len(keys) == 30, "factor keys must be unique"

        # Every factor has a WHY-it's-risky explanation.
        for f in report.factors:
            assert isinstance(f, RiskFactor)
            assert f.explanation, f"factor {f.key} has empty explanation"

        # Data quality: with a fully-populated scan everything is known.
        assert report.data_quality["factors_total"] == 30
        assert report.data_quality["factors_unknown"] == 0
        assert report.data_quality["coverage_pct"] == 100.0

    def test_composite_and_determination(self) -> None:
        r = _synthetic_scan()
        report = build_risk_report(
            r,
            deployer_fingerprint=_synthetic_fingerprint(),
            holder_clusters=_synthetic_holder_clusters(),
            cluster_graph=_synthetic_cluster_graph(),
        )

        assert report.composite_score is not None
        assert 0 <= report.composite_score <= 100
        # Repeated-rugged deployer + bundle + mint + unlocked LP => at least "rug".
        assert report.determination in ("rug", "scam")
        assert report.summary

    def test_honeypot_forces_scam(self) -> None:
        r = _synthetic_scan()
        r.security = {"is_honeypot": True}
        report = build_risk_report(r)
        assert report.determination == "scam"
        hp_factor = next(f for f in report.factors if f.key == "honeypot")
        assert hp_factor.risk_score == 95.0
        assert hp_factor.status == "critical"

    def test_unknown_data_degrades_gracefully(self) -> None:
        """An empty ScanResult must produce a report full of unknowns — no crash."""
        r = ScanResult(token_address="0xempty0000000000000000000000000000000000", chain="solana")
        report = build_risk_report(r)

        assert isinstance(report, RiskReport)
        assert report.composite_score is None
        assert report.determination == "unknown"
        for f in report.factors:
            assert f.status == "unknown"
            assert f.risk_score is None
        assert report.data_quality["factors_known"] == 0
        assert report.cluster.cluster_risk_score is None
        assert report.honeypot["is_honeypot"] is None
        assert report.bundle["is_bundled"] is None

    def test_to_dict_json_safe(self) -> None:
        r = _synthetic_scan()
        report = build_risk_report(
            r,
            deployer_fingerprint=_synthetic_fingerprint(),
            holder_clusters=_synthetic_holder_clusters(),
            cluster_graph=_synthetic_cluster_graph(),
        )
        d = report.to_dict()
        assert d["composite_score"] is not None
        assert len(d["factors"]) == 30
        assert d["cluster"]["cluster_risk_score"] == 92
        assert d["determination"] in ("rug", "scam")
        # JSON-safe: only primitives / lists / dicts.
        import json

        json.dumps(d)

    def test_from_dict_round_trip(self) -> None:
        """``from_dict(to_dict(x))`` must reproduce the report (consumer wiring).

        The bot formatter and API clients receive the serialized dict; the
        rehydrator is the canonical way to rebuild the typed report without
        re-running the scanner. Round-trips the composite, every factor and
        the cluster payload exactly.
        """
        r = _synthetic_scan()
        report = build_risk_report(
            r,
            deployer_fingerprint=_synthetic_fingerprint(),
            holder_clusters=_synthetic_holder_clusters(),
            cluster_graph=_synthetic_cluster_graph(),
        )
        restored = RiskReport.from_dict(report.to_dict())

        assert restored.token_address == report.token_address
        assert restored.chain == report.chain
        assert restored.composite_score == report.composite_score
        assert restored.determination == report.determination
        assert restored.summary == report.summary
        assert len(restored.factors) == len(report.factors)
        for a, b in zip(restored.factors, report.factors, strict=True):
            assert a.key == b.key
            assert a.label == b.label
            assert a.risk_score == b.risk_score
            assert a.status == b.status
            assert a.explanation == b.explanation
        assert restored.cluster.cluster_risk_score == report.cluster.cluster_risk_score
        assert restored.cluster.cluster_risk_label == report.cluster.cluster_risk_label
        assert len(restored.cluster.funder_tree) == len(report.cluster.funder_tree)
        assert restored.honeypot == report.honeypot
        assert restored.liquidity == report.liquidity
        assert restored.bundle == report.bundle
        # Truncated payload degrades defensively (never raises).
        partial = RiskReport.from_dict({"token_address": "0xabc", "chain": "solana"})
        assert partial.determination == "unknown"
        assert partial.factors == []


# ── Formatter ──────────────────────────────────────────────────────────


class TestFormatter:
    def test_formatted_output_contains_all_sections(self) -> None:
        r = _synthetic_scan()
        report = build_risk_report(
            r,
            deployer_fingerprint=_synthetic_fingerprint(),
            holder_clusters=_synthetic_holder_clusters(),
            cluster_graph=_synthetic_cluster_graph(),
        )
        text = format_risk_report(report)
        for section in (
            "COMPOSITE RISK SCORE",
            "DETERMINATION",
            "DEPLOYER",
            "HOLDERS",
            "FIRST BUYER",
            "TOP 25 HOLDERS",
            "BUNDLE / SNIPING",
            "CLUSTER DETECTION",
            "HONEYPOT + RUG VECTORS",
            "LIQUIDITY",
            "RISK FACTORS (~25)",
        ):
            assert section in text, f"missing section {section} in formatter output"
        # Deeper analysis only appears when requested.
        assert "DEEPER-LEVEL ANALYSIS" not in text

    def test_detailed_formatter_includes_deeper_analysis(self) -> None:
        r = _synthetic_scan()
        report = build_risk_report(r)
        text = format_risk_report(report, detailed=True)
        assert "DEEPER-LEVEL ANALYSIS" in text
        assert "deployer" in text.lower()


# ── Honest output (insufficient data / determinations) ──────────────────


class TestHonestOutput:
    """A composite built from too few known factors must NOT be presented as a
    clean "0/100 OK" verdict — that self-contradictory headline is what made a
    thin scan's output misleading. When < ``_MIN_KNOWN_FOR_DETERMINATION``
    factors are known, the formatter omits the verdict block and says the scan
    is pending. A confirmed honeypot still forces the SCAM verdict (it is a
    decisive signal even on a thin scan)."""

    def test_composite_does_not_declare_ok_from_thin_data(self) -> None:
        from app.domains.scanner.unified.risk_report import _composite_and_determination

        factors = [
            RiskFactor(key="honeypot", label="Honeypot (cannot sell)", risk_score=None),
            RiskFactor(key="mintable", label="Mintable", risk_score=None),
            RiskFactor(key="bundle", label="Bundled launch", risk_score=None),
            RiskFactor(key="holder_concentration", label="Concentration", risk_score=10.0),
            RiskFactor(key="liquidity", label="Liquidity", risk_score=None),
        ]
        _composite, determination, summary = _composite_and_determination(
            factors, honeypot={}, deployer_section={}, bundle={}
        )
        # Only ONE known factor -> cannot safely declare "ok".
        assert determination == "unknown"
        # The summary must not present a composite score as a verdict.
        assert "Composite risk" not in summary
        assert "Insufficient data" in summary

    def test_honeypot_forces_scam_on_thin_scan(self) -> None:
        from app.domains.scanner.unified.risk_report import _composite_and_determination

        factors = [
            RiskFactor(key="honeypot", label="Honeypot (cannot sell)", risk_score=None),
            RiskFactor(key="mintable", label="Mintable", risk_score=None),
        ]
        _composite, determination, summary = _composite_and_determination(
            factors, honeypot={"is_honeypot": True}, deployer_section={}, bundle={}
        )
        assert determination == "scam"
        assert "SCAM" in summary

    def test_formatter_omits_verdict_block_when_thin(self) -> None:
        report = RiskReport(
            token_address="0xthin000000000000000000000000000000000000",
            chain="solana",
            composite_score=0,
            determination="unknown",
            summary="Insufficient data — 1/30 factors known, a verdict needs at least 6. Scan pending; re-run or use a deeper tier.",
            factors=[
                RiskFactor(key="only_one", label="Only one", risk_score=0.0, status="ok"),
            ],
            data_quality={"factors_known": 1, "factors_total": 30, "coverage_pct": 3.3},
            honeypot={"is_honeypot": None},
        )
        text = format_risk_report(report)
        # The self-contradictory verdict block must be gone.
        assert "COMPOSITE RISK SCORE" not in text
        assert "DETERMINATION        :" not in text
        assert "INSUFFICIENT DATA — SCAN PENDING" in text

    def test_formatter_keeps_verdict_block_when_enough(self) -> None:
        report = RiskReport(
            token_address="0xfull000000000000000000000000000000000000",
            chain="ethereum",
            composite_score=12,
            determination="ok",
            summary="Composite risk 12/100 from 30/30 known factors. DETERMINATION: OK — no dominant scam/rug signal detected.",
            factors=[RiskFactor(key=f"f{i}", label=f"F{i}", risk_score=0.0) for i in range(30)],
            data_quality={"factors_known": 30, "factors_total": 30, "coverage_pct": 100.0},
            honeypot={"is_honeypot": False},
        )
        text = format_risk_report(report)
        assert "COMPOSITE RISK SCORE" in text
        assert "DETERMINATION        : OK" in text

    def test_formatter_renders_honeypot_honestly(self) -> None:
        def _text(hp: bool | None) -> str:
            report = RiskReport(
                token_address="0xh00000000000000000000000000000000000000",
                chain="ethereum",
                composite_score=0,
                determination="unknown",
                factors=[],
                data_quality={"factors_known": 0, "factors_total": 30, "coverage_pct": 0.0},
                honeypot={"is_honeypot": hp},
            )
            return format_risk_report(report)

        # Never a bare bool/"✗" — always an honest label.
        assert "🚨 HONEYPOT" in _text(True)
        assert "Not a honeypot" in _text(False)
        assert "unknown" in _text(None)
        # The raw truthy/bare-✗ forms must never leak through.
        assert "\u2717" not in _text(True)


# ── Cluster pure functions ─────────────────────────────────────────────


class TestClusterPure:
    def test_build_funder_tree(self) -> None:
        rows = [
            {"holder": "0xa", "funder": "0xdev", "first_tx": "0xt1"},
            {"holder": "0xb", "funder": "0xdev", "first_tx": "0xt2"},
            {"holder": "0xc", "funder": "0xdev", "first_tx": "0xt3"},
            {"holder": "0xd", "funder": "0xpool", "first_tx": "0xt4"},
            {"holder": "0xe", "funder": "0xpool", "first_tx": "0xt5"},
            {"holder": "0xf", "funder": "0xsolo", "first_tx": "0xt6"},
        ]
        tree = build_funder_tree(rows, deployer="0xdev")
        assert len(tree) == 2
        dev_root = next(c for c in tree if c["funder"] == "0xdev")
        assert dev_root["funder_is_deployer"] is True
        assert dev_root["holder_count"] == 3
        assert dev_root["share_pct"] == 50.0
        assert dev_root["evidence"] == ["0xt1", "0xt2", "0xt3"]

    def test_build_funder_tree_tuple_rows(self) -> None:
        rows = [("0xa", "0xdev", "0xt1"), ("0xb", "0xdev", "0xt2"), ("0xc", "0xdev", "0xt3")]
        tree = build_funder_tree(rows, deployer="0xdev")
        assert len(tree) == 1
        assert tree[0]["holder_count"] == 3

    def test_funder_tree_requires_min_two(self) -> None:
        rows = [{"holder": "0xa", "funder": "0xdev", "first_tx": "0xt1"}]
        assert build_funder_tree(rows, deployer="0xdev") == []

    def test_detect_shared_deployer_networks(self) -> None:
        rows = [
            {"token_address": "0xt1", "deployer": "0xdev", "wallets": ["0xa", "0xb", "0xc"]},
            {"token_address": "0xt2", "deployer": "0xdev", "wallets": ["0xa", "0xb", "0xd"]},
        ]
        nets = detect_shared_deployer_networks(rows, deployer="0xdev")
        assert len(nets) == 1
        assert nets[0]["launch_count"] == 2
        assert set(nets[0]["shared_wallets"]) == {"0xa", "0xb"}

    def test_detect_cross_token_clusters(self) -> None:
        rows = [
            {"token_address": "0xt1", "wallets": ["0xa", "0xb"]},
            {"token_address": "0xt2", "wallets": ["0xc"]},
        ]
        clusters = detect_cross_token_clusters(rows)
        assert len(clusters) == 1
        assert clusters[0]["members"] == 2

    def test_score_cluster_risk(self) -> None:
        deployer_tree = [{"funder_is_deployer": True, "holder_count": 3}]
        nets = [{"shared_wallets": ["0xa", "0xb"], "launch_count": 2}]
        cross = [{"members": 2}]
        score, label, desc, components = score_cluster_risk(deployer_tree, nets, cross, bundle_score=80.0)
        assert 0 <= score <= 100
        assert label in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
        assert desc
        assert isinstance(components, dict)
        assert components["funder_tree"] == 30.0
        # Deployer-funded tree alone is decisive.
        s2, _, _, _ = score_cluster_risk(deployer_tree, [], [])
        assert s2 >= 30

    def test_score_cluster_risk_empty_is_low(self) -> None:
        score, label, _, components = score_cluster_risk([], [], [])
        assert score == 0
        assert label == "LOW"
        # All component contributions are present but zero.
        assert components == {
            "funder_tree": 0.0,
            "shared_deployer_network": 0.0,
            "cross_token_clusters": 0.0,
            "cross_token_funders": 0.0,
            "temporal_clusters": 0.0,
            "sniper_patterns": 0.0,
            "mev_bundles": 0.0,
            "wash_trading": 0.0,
        }

    def test_score_cluster_risk_deep_structures_add_points(self) -> None:
        """Each deepened structure contributes to the composite cluster score."""
        score, label, _, components = score_cluster_risk(
            [],
            [],
            [],
            cross_token_funders=[{"funder": "0xf", "launch_count": 3, "funded_wallet_count": 4}],
            temporal_clusters=[{"block": 1, "wallets": ["0xa", "0xb"], "members": 2}],
            sniper_patterns=[
                {"pattern": "same_block_snipe", "members": 3},
                {"pattern": "uniform_bot_amounts", "members": 2},
            ],
            mev_bundles=[{"pattern": "atomic_bundle_buy", "members": 2}],
            wash_clusters={
                "cycles": [{"combined_volume": 100.0}],
                "cycle_volume_ratio": 0.25,
            },
        )
        assert score >= 80  # 20 + 18 + 35 + 25 + 27 = 125 → capped 100
        assert label == "CRITICAL"
        assert components["cross_token_funders"] == 20.0
        assert components["temporal_clusters"] == 18.0
        assert components["sniper_patterns"] == 35.0
        assert components["mev_bundles"] == 25.0
        assert components["wash_trading"] == 27.0

    def test_detect_cross_token_funder_graphs(self) -> None:
        rows = [
            {"funder": "0xf1", "token_address": "0xt1", "wallets": ["0xa", "0xb"]},
            {"funder": "0xf1", "token_address": "0xt2", "wallets": ["0xc"]},
            {"funder": "0xf1", "token_address": "0xt3", "wallets": ["0xd"]},
            {"funder": "0xf2", "token_address": "0xt1", "wallets": ["0xe"]},
        ]
        graphs = detect_cross_token_funder_graphs(rows)
        assert len(graphs) == 1  # f1 appears in 3 launches; f2 in only 1
        assert graphs[0]["funder"] == "0xf1"
        assert graphs[0]["launch_count"] == 3
        assert graphs[0]["funded_wallet_count"] == 4

    def test_detect_cross_token_funder_graphs_tuple_rows(self) -> None:
        rows = [
            ("0xf1", "0xt1", "0xa", "0xb"),
            ("0xf1", "0xt2", "0xc"),
        ]
        graphs = detect_cross_token_funder_graphs(rows)
        assert len(graphs) == 1
        assert graphs[0]["launch_count"] == 2
        assert set(graphs[0]["funded_wallets"]) == {"0xa", "0xb", "0xc"}

    def test_detect_temporal_clusters_same_block(self) -> None:
        rows = [
            {"wallet": "0xa", "block": 100},
            {"wallet": "0xb", "block": 100},
            {"wallet": "0xc", "block": 101},
        ]
        clusters = detect_temporal_clusters(rows, deploy_block=95)
        same_block = [c for c in clusters if not c["rapid_sequence"]]
        assert len(same_block) == 1
        assert same_block[0]["members"] == 2
        assert same_block[0]["block"] == 100
        assert same_block[0]["early_launch"] is True

    def test_detect_temporal_clusters_rapid_sequence(self) -> None:
        rows = [
            ("0xa", 100),
            ("0xb", 101),
            ("0xc", 102),
        ]
        clusters = detect_temporal_clusters(rows, deploy_block=98)
        rapid = [c for c in clusters if c["rapid_sequence"]]
        assert len(rapid) == 1
        assert rapid[0]["members"] == 3
        assert rapid[0]["span_blocks"] == 2

    def test_detect_temporal_clusters_empty(self) -> None:
        assert detect_temporal_clusters([]) == []

    def test_detect_sniper_bot_patterns(self) -> None:
        rows = [
            {"wallet": "0xa", "amount": 100.0, "block": 1000},
            {"wallet": "0xb", "amount": 102.0, "block": 1000},
            {"wallet": "0xc", "amount": 99.0, "block": 1000},
            {"wallet": "0xd", "amount": 101.0, "block": 1001},
            {"wallet": "0xe", "amount": 100.5, "block": 1001},
        ]
        patterns = detect_sniper_bot_patterns(
            rows,
            deploy_block=995,
            wallets_with_prior_tx={"0xd", "0xe"},
        )
        kinds = {p["pattern"] for p in patterns}
        assert "same_block_snipe" in kinds  # 3 wallets in block 1000
        assert "uniform_bot_amounts" in kinds  # CV ~0.01
        assert "fresh_wallet_bots" in kinds  # 0xa/0xb/0xc fresh
        assert "sniper_wave" in kinds  # 5 wallets within launch window

    def test_detect_sniper_bot_patterns_no_signals(self) -> None:
        rows = [
            {"wallet": "0xa", "amount": 10.0, "block": 100},
            {"wallet": "0xb", "amount": 500.0, "block": 200},
        ]
        patterns = detect_sniper_bot_patterns(rows, deploy_block=90, wallets_with_prior_tx={"0xa", "0xb"})
        assert patterns == []

    def test_detect_mev_bundles_atomic(self) -> None:
        rows = [
            {"block": 1000, "tx": "0xt1", "from": "0xpool", "to": "0xa", "amount": 50.0},
            {"block": 1000, "tx": "0xt2", "from": "0xpool", "to": "0xb", "amount": 51.0},
            {"block": 1001, "tx": "0xt3", "from": "0xpool", "to": "0xc", "amount": 500.0},
        ]
        bundles = detect_mev_bundles(rows)
        kinds = {b["pattern"] for b in bundles}
        assert "atomic_bundle_buy" in kinds
        atomic = next(b for b in bundles if b["pattern"] == "atomic_bundle_buy")
        assert set(atomic["wallets"]) == {"0xa", "0xb"}

    def test_detect_mev_bundles_identical_tip(self) -> None:
        rows = [
            {"block": 1000, "tx": "0xt1", "from": "0xpool", "to": "0xa", "amount": 10.0, "tip": 0.001},
            {"block": 1000, "tx": "0xt2", "from": "0xpool", "to": "0xb", "amount": 11.0, "tip": 0.001},
        ]
        bundles = detect_mev_bundles(rows)
        kinds = {b["pattern"] for b in bundles}
        assert "identical_tip_bundle" in kinds

    def test_detect_mev_bundles_empty(self) -> None:
        assert detect_mev_bundles([]) == []

    def test_detect_wash_trading_clusters_roundtrip(self) -> None:
        base = 1_700_000_000_000
        rows = [
            {"from": "0xa", "to": "0xb", "amount": 100.0, "timestamp": base},
            {"from": "0xb", "to": "0xa", "amount": 95.0, "timestamp": base + 60_000},
            {"from": "0xc", "to": "0xd", "amount": 10.0, "timestamp": base + 120_000},
        ]
        result = detect_wash_trading_clusters(rows, lookback_hours=72)
        assert len(result["cycles"]) == 1
        cycle = result["cycles"][0]
        assert cycle["pattern_type"] == "simple_roundtrip"
        assert cycle["cycle_length"] == 2
        assert cycle["confidence"] == 0.9
        assert result["cycle_volume_ratio"] > 0

    def test_detect_wash_trading_clusters_multi_hop(self) -> None:
        base = 1_700_000_000_000
        rows = [
            ("0xa", "0xb", 100.0, base),
            ("0xb", "0xc", 98.0, base + 30_000),
            ("0xc", "0xa", 96.0, base + 60_000),
        ]
        result = detect_wash_trading_clusters(rows, lookback_hours=72)
        assert len(result["cycles"]) == 1
        assert result["cycles"][0]["pattern_type"] == "multi_hop"
        assert result["cycles"][0]["cycle_length"] == 3

    def test_detect_wash_trading_clusters_self_swaps(self) -> None:
        base = 1_700_000_000_000
        rows = [
            {"from": "0xa", "to": "0xa", "amount": 50.0, "timestamp": base},
            {"from": "0xa", "to": "0xa", "amount": 25.0, "timestamp": base + 1_000},
        ]
        result = detect_wash_trading_clusters(rows, lookback_hours=72)
        assert result["cycles"] == []
        assert len(result["self_swaps"]) == 1
        assert result["self_swaps"][0]["count"] == 2
        assert result["self_swaps"][0]["volume"] == 75.0

    def test_detect_wash_trading_clusters_stale_cycle_rejected(self) -> None:
        base = 1_700_000_000_000
        rows = [
            {"from": "0xa", "to": "0xb", "amount": 100.0, "timestamp": base},
            {"from": "0xb", "to": "0xa", "amount": 95.0, "timestamp": base + 100 * 3600 * 1000},  # +100h
        ]
        result = detect_wash_trading_clusters(rows, lookback_hours=72)
        assert result["cycles"] == []

    def test_detect_wash_trading_clusters_empty(self) -> None:
        result = detect_wash_trading_clusters([])
        assert result["cycles"] == []
        assert result["self_swaps"] == []
        assert result["wash_score"] == 0


# ── Solana query branches (pump.fun cluster detection) ─────────────────


class _StubCH:
    """Minimal clickhouse-driver stub: captures SQL + params, returns rows."""

    def __init__(self, rows: list[Any] | None = None) -> None:
        self.rows = rows or []
        self.queries: list[str] = []
        self.params: list[dict[str, Any]] = []

    def execute(self, query: str, params: dict[str, Any] | None = None, **kwargs: Any) -> list[Any]:
        self.queries.append(query)
        self.params.append(params or {})
        return self.rows


class TestRiskClusterSolanaBranches:
    """Solana branches hit the solana tables, not evm_transfers."""

    @pytest.fixture(autouse=True)
    def stub_ch(self, monkeypatch: pytest.MonkeyPatch) -> _StubCH:
        stub = _StubCH([("0xsolholder",)])
        monkeypatch.setattr("app.domains.scanner.engines.risk_cluster._get_ch_client", lambda: stub)
        return stub

    def test_first_funders_solana_uses_trace_tables(self, stub_ch: _StubCH) -> None:
        from app.domains.scanner.engines.risk_cluster import _query_first_funders

        _query_first_funders("mint123", "solana")
        sql = stub_ch.queries[0].lower()
        assert "solana_spl_balance_changes" in sql
        assert "solana_transfers" in sql
        assert "evm_transfers" not in sql
        assert stub_ch.params[0]["token"] == "mint123"

    def test_top_holders_solana_uses_balance_deltas(self, stub_ch: _StubCH) -> None:
        from app.domains.scanner.engines.risk_cluster import _query_top_holders

        _query_top_holders("mint123", "solana", 50)
        sql = stub_ch.queries[0].lower()
        assert "solana_spl_balance_changes" in sql
        assert "evm_transfers" not in sql

    def test_deployer_solana_uses_launches(self, stub_ch: _StubCH) -> None:
        from app.domains.scanner.engines.risk_cluster import _query_deployer

        _query_deployer("mint123", "solana")
        sql = stub_ch.queries[0].lower()
        assert "solana_launches" in sql
        assert "deployer_events" not in sql

    def test_first_buy_blocks_solana_uses_slot(self, stub_ch: _StubCH) -> None:
        from app.domains.scanner.engines.risk_cluster import _query_first_buy_blocks

        _query_first_buy_blocks("mint123", "solana", 50)
        sql = stub_ch.queries[0].lower()
        assert "solana_spl_balance_changes" in sql
        assert "slot" in sql
        assert "dex_events" not in sql

    def test_launch_window_solana_uses_slot_window(self, stub_ch: _StubCH) -> None:
        from app.domains.scanner.engines.risk_cluster import _query_launch_window_transfers

        _query_launch_window_transfers("mint123", "solana", anchor_block=100, window_blocks=30, limit=200)
        sql = stub_ch.queries[0].lower()
        assert "solana_spl_balance_changes" in sql
        assert "slot" in sql
        assert "evm_transfers" not in sql

    def test_wash_transfers_solana_uses_trace_table(self, stub_ch: _StubCH) -> None:
        from app.domains.scanner.engines.risk_cluster import _query_token_transfers

        _query_token_transfers("mint123", "solana", lookback_hours=72, limit=500)
        sql = stub_ch.queries[0].lower()
        assert "solana_transfers" in sql
        assert "evm_transfers" not in sql

    def test_prior_tx_solana_uses_balance_changes(self, stub_ch: _StubCH) -> None:
        from app.domains.scanner.engines.risk_cluster import _query_wallets_with_prior_tx

        _query_wallets_with_prior_tx(["0xa"], "solana", cutoff_ts=1_700_000_000)
        sql = stub_ch.queries[0].lower()
        assert "solana_spl_balance_changes" in sql
        assert "evm_transfers" not in sql


# ── Engine wiring (async phase) ────────────────────────────────────────


class TestRiskReportPhaseWiring:
    """The scanner phase delegate must wire engines and degrade gracefully."""

    async def test_phase_with_all_engines(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.domains.scanner.unified import risk_report as rr

        r = _synthetic_scan()

        async def _fp(deployer: str, chain: str) -> dict[str, Any]:
            return _synthetic_fingerprint()

        async def _hc(token: str, chain: str) -> dict[str, Any]:
            return _synthetic_holder_clusters()

        async def _cg(token: str, chain: str, **kw: Any) -> dict[str, Any]:
            return _synthetic_cluster_graph()

        monkeypatch.setattr(rr, "fingerprint_deployer", _fp, raising=False)
        # The phase lazy-imports engines from their modules; patch via the
        # engine modules so the lazy import inside the phase sees them.
        import app.domains.scanner.engines.deployer_fingerprint as dfp_mod
        import app.domains.scanner.engines.holder_clusters as hc_mod
        import app.domains.scanner.engines.risk_cluster as rc_mod

        monkeypatch.setattr(dfp_mod, "fingerprint_deployer", _fp)
        monkeypatch.setattr(hc_mod, "analyze_holder_clusters", _hc)
        monkeypatch.setattr(rc_mod, "analyze_wallet_clusters", _cg)

        await rr.run_risk_report_phase(r, r.token_address, r.chain)

        assert r.risk_report is not None
        assert r.risk_report["composite_score"] is not None
        assert r.risk_report["cluster"]["cluster_risk_score"] == 92
        assert "risk_report" in r.modules_run
        assert "risk_report:structured" in r.enrichment_sources

    async def test_phase_without_engines_does_not_crash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No ClickHouse / no engines → report attached with unknown factors."""
        from app.domains.scanner.unified import risk_report as rr

        r = ScanResult(token_address="0xempty0000000000000000000000000000000000", chain="solana")

        async def _boom(*args: Any, **kwargs: Any) -> None:
            raise ConnectionError("clickhouse down")

        import app.domains.scanner.engines.deployer_fingerprint as dfp_mod
        import app.domains.scanner.engines.holder_clusters as hc_mod
        import app.domains.scanner.engines.risk_cluster as rc_mod

        monkeypatch.setattr(dfp_mod, "fingerprint_deployer", _boom)
        monkeypatch.setattr(hc_mod, "analyze_holder_clusters", _boom)
        monkeypatch.setattr(rc_mod, "analyze_wallet_clusters", _boom)

        await rr.run_risk_report_phase(r, r.token_address, r.chain)

        assert r.risk_report is not None
        assert r.risk_report["determination"] == "unknown"
        assert all(f["status"] == "unknown" for f in r.risk_report["factors"])

    async def test_scanner_delegate_wires_phase(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``UnifiedTokenScanner._run_risk_report`` calls the phase runner."""
        from app.domains.scanner.unified.scanner import UnifiedTokenScanner

        scanner = UnifiedTokenScanner()
        r = _synthetic_scan()
        calls: list[str] = []

        async def _fake_phase(result: Any, address: str, chain: str) -> None:
            calls.append("phase")
            result.risk_report = {"token_address": address, "chain": chain, "factors": []}

        # scanner._run_risk_report imports the phase lazily from the module,
        # so patching the module attribute is what the delegate sees.
        import app.domains.scanner.unified.risk_report as rr_mod

        monkeypatch.setattr(rr_mod, "run_risk_report_phase", _fake_phase)

        await scanner._run_risk_report(r, "0xabc", "ethereum")
        assert calls == ["phase"]
        assert r.risk_report == {"token_address": "0xabc", "chain": "ethereum", "factors": []}
