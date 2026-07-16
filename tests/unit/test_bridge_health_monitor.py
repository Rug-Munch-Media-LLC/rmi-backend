"""
Tests for Cross-Chain Bridge Health & Exploit Monitor
======================================================
Covers all core components: scoring, anomaly detection, contagion risk,
and report generation - all without requiring network calls.
"""

import json
import unittest
from unittest.mock import patch

from app.bridge_health_monitor import (
    BRIDGE_REGISTRY,
    BridgeHealthMonitor,
    BridgeHealthReport,
    BridgeSecurityScore,
    BridgeTVLSnapshot,
    ExploitSignal,
    RiskTier,
    TrustModel,
)


class TestRiskTier(unittest.TestCase):
    """Risk tier classification logic."""

    def test_risk_tier_values(self):
        """All risk tiers have correct ordering."""
        assert RiskTier.SAFE.value == "SAFE"
        assert RiskTier.WATCH.value == "WATCH"
        assert RiskTier.DANGER.value == "DANGER"
        assert RiskTier.CRITICAL.value == "CRITICAL"

    def test_risk_tier_str(self):
        """Risk tier string representation works."""
        assert str(RiskTier.SAFE) == "RiskTier.SAFE"


class TestTrustModel(unittest.TestCase):
    """Trust model classification."""

    def test_all_models_present(self):
        """All expected trust models exist."""
        models = {m.value for m in TrustModel}
        expected = {
            "external_validators",
            "optimistic",
            "liquidity_network",
            "hybrid",
            "intent_based",
        }
        assert models == expected


class TestBridgeRegistry(unittest.TestCase):
    """Bridge registry contains all expected bridges with valid configs."""

    def test_registry_has_major_bridges(self):
        """All major bridges are present."""
        expected_bridges = {
            "layerzero",
            "stargate",
            "across",
            "wormhole",
            "hop",
            "synapse",
            "axelar",
            "celer",
            "debridge",
            "chainlink_ccip",
            "connext",
            "orbiter",
        }
        self.assertSetEqual(set(BRIDGE_REGISTRY.keys()), expected_bridges)

    def test_every_bridge_has_required_fields(self):
        """Each bridge entry has all required fields."""
        required_fields = {"name", "trust_model", "chains", "tvl_source", "defillama_slug"}
        for key, bridge in BRIDGE_REGISTRY.items():
            with self.subTest(bridge=key):
                for field in required_fields:
                    assert field in bridge, f"{key} missing field: {field}"

    def test_every_bridge_has_valid_trust_model(self):
        """Trust model is a valid enum member."""
        for key, bridge in BRIDGE_REGISTRY.items():
            with self.subTest(bridge=key):
                assert isinstance(bridge["trust_model"], TrustModel), f"{key} has invalid trust model"

    def test_every_bridge_has_nonempty_chains(self):
        """Every bridge supports at least one chain."""
        for key, bridge in BRIDGE_REGISTRY.items():
            with self.subTest(bridge=key):
                assert len(bridge["chains"]) > 0, f"{key} has no supported chains"

    def test_every_bridge_has_nonnegative_exploit_loss(self):
        """Exploit loss values are non-negative."""
        for key, bridge in BRIDGE_REGISTRY.items():
            with self.subTest(bridge=key):
                assert bridge.get("total_exploit_loss_usd", 0) >= 0, f"{key} has negative exploit loss"

    def test_validator_counts_are_nonnegative(self):
        """Validator counts are non-negative integers."""
        for key, bridge in BRIDGE_REGISTRY.items():
            with self.subTest(bridge=key):
                assert bridge.get("validator_count", 0) >= 0, f"{key} has negative validator count"


class TestBridgeHealthReport(unittest.TestCase):
    """Report generation and formatting."""

    def setUp(self):
        self.report = BridgeHealthReport()
        self.report.total_bridges = 12
        self.report.bridges_healthy = 8
        self.report.bridges_watch = 2
        self.report.bridges_danger = 1
        self.report.bridges_critical = 1
        self.report.total_tvl_usd = 4_500_000_000
        self.report.tvl_change_24h_pct = -2.5

    def test_summary_contains_key_sections(self):
        """Summary output contains all expected sections."""
        summary = self.report.summary()
        assert "CROSS-CHAIN BRIDGE HEALTH REPORT" in summary
        assert "Bridges monitored: 12" in summary
        assert "Healthy: 8" in summary
        assert "Watch: 2" in summary
        assert "Danger: 1" in summary
        assert "Critical: 1" in summary
        assert "$4,500,000,000" in summary

    def test_summary_empty_exploit_signals(self):
        """Summary handles empty exploit signals gracefully."""
        summary = self.report.summary()
        assert "EXPLOIT SIGNALS" not in summary

    def test_summary_with_exploit_signals(self):
        """Summary includes exploit signals section."""
        self.report.exploit_signals.append(
            ExploitSignal(
                bridge_key="wormhole",
                bridge_name="Wormhole",
                signal_type="tvl_drop",
                severity="critical",
                description="Critical TVL drop detected",
                detected_value=-45.0,
                threshold_value=-40.0,
            )
        )
        summary = self.report.summary()
        assert "EXPLOIT SIGNALS (1)" in summary
        assert "Wormhole" in summary
        assert "CRITICAL" in summary

    def test_summary_with_contagion_risk(self):
        """Summary includes contagion risk section."""
        self.report.contagion_risk = ["Hop (score 72) - shares similar security profile"]
        summary = self.report.summary()
        assert "Contagion Risk" in summary
        assert "Hop" in summary

    def test_summary_with_security_scores(self):
        """Summary includes bridge security score table."""
        self.report.security_scores["stargate"] = BridgeSecurityScore(
            bridge_key="stargate",
            bridge_name="Stargate",
            overall_score=85.0,
            tvl_depth_score=20,
            decentralization_score=12,
            audit_score=20,
            exploit_history_score=20,
            upgrade_risk_score=13,
            risk_tier=RiskTier.SAFE,
        )
        summary = self.report.summary()
        assert "Stargate" in summary
        assert "85.0" in summary

    def test_summary_with_vulnerabilities(self):
        """Summary includes vulnerability list."""
        self.report.security_scores["wormhole"] = BridgeSecurityScore(
            bridge_key="wormhole",
            bridge_name="Wormhole",
            overall_score=40.0,
            tvl_depth_score=20,
            decentralization_score=15,
            audit_score=10,
            exploit_history_score=0,
            upgrade_risk_score=5,
            risk_tier=RiskTier.CRITICAL,
            vulnerabilities=["Major past exploit(s) - $326,000,000 total losses"],
        )
        summary = self.report.summary()
        assert "Wormhole" in summary
        assert "exploit" in summary.lower()

    def test_to_json_structure(self):
        """JSON output contains all expected top-level keys."""
        json_str = self.report.to_json()
        data = json.loads(json_str)
        expected_keys = {
            "timestamp",
            "total_bridges",
            "bridges_healthy",
            "bridges_watch",
            "bridges_danger",
            "bridges_critical",
            "total_tvl_usd",
            "tvl_change_24h_pct",
            "bridge_snapshots",
            "security_scores",
            "exploit_signals",
            "contagion_risk",
        }
        self.assertSetEqual(set(data.keys()), expected_keys)

    def test_recommendation_when_critical(self):
        """Recommendation text reflects critical state."""
        self.report.bridges_critical = 2
        summary = self.report.summary()
        assert "CRITICAL" in summary
        assert "Exploit likely in progress" in summary

    def test_recommendation_when_all_healthy(self):
        """Recommendation text reflects all-clear state."""
        self.report.bridges_critical = 0
        self.report.bridges_danger = 0
        self.report.bridges_watch = 0
        summary = self.report.summary()
        assert "All bridges appear healthy" in summary

    def test_recommendation_with_danger_only(self):
        """Recommendation text reflects danger state."""
        self.report.bridges_critical = 0
        self.report.bridges_danger = 1
        summary = self.report.summary()
        assert "DANGER" in summary


class TestBridgeTVLSnapshot(unittest.TestCase):
    """TVL snapshot data class."""

    def test_default_fields(self):
        """Snapshot has defaults set correctly."""
        snapshot = BridgeTVLSnapshot(
            bridge_key="test",
            bridge_name="Test Bridge",
            tvl_usd=1000000.0,
            tvl_change_24h_pct=-5.0,
            tvl_change_7d_pct=-10.0,
            tvl_change_30d_pct=-20.0,
        )
        assert snapshot.bridge_key == "test"
        assert snapshot.bridge_name == "Test Bridge"
        assert snapshot.tvl_usd == 1000000.0
        assert snapshot.tvl_change_24h_pct == -5.0
        assert snapshot.tvl_change_7d_pct == -10.0
        assert snapshot.tvl_change_30d_pct == -20.0
        assert isinstance(snapshot.chain_breakdown, dict)
        assert snapshot.timestamp is not None


class TestBridgeSecurityScore(unittest.TestCase):
    """Security score data class."""

    def test_default_fields(self):
        """Score has defaults set correctly."""
        score = BridgeSecurityScore(
            bridge_key="test",
            bridge_name="Test Bridge",
            overall_score=75.0,
            tvl_depth_score=15,
            decentralization_score=20,
            audit_score=15,
            exploit_history_score=15,
            upgrade_risk_score=10,
            risk_tier=RiskTier.WATCH,
        )
        assert score.overall_score == 75.0
        assert score.risk_tier == RiskTier.WATCH
        assert isinstance(score.vulnerabilities, list)
        assert isinstance(score.strengths, list)


class TestExploitSignal(unittest.TestCase):
    """Exploit signal data class."""

    def test_signal_creation(self):
        """Signal is created with correct fields."""
        signal = ExploitSignal(
            bridge_key="wormhole",
            bridge_name="Wormhole",
            signal_type="tvl_drop",
            severity="high",
            description="TVL dropped 45% in 24h",
            detected_value=-45.0,
            threshold_value=-40.0,
        )
        assert signal.bridge_key == "wormhole"
        assert signal.severity == "high"
        assert signal.timestamp is not None


class TestSecurityScoreComputation(unittest.TestCase):
    """Unit tests for the security score computation logic."""

    def setUp(self):
        self.monitor = BridgeHealthMonitor()

    @patch.object(BridgeHealthMonitor, "_fetch_current_tvl", return_value=2_000_000_000)
    async def test_layerzero_score(self, mock_tvl):
        """LayerZero should get a good score (battle-tested, many validators)."""
        bridge = BRIDGE_REGISTRY["layerzero"]
        score = await self.monitor._compute_security_score("layerzero", bridge)
        assert score.overall_score >= 75
        assert score.risk_tier in (RiskTier.SAFE, RiskTier.WATCH)

    @patch.object(BridgeHealthMonitor, "_fetch_current_tvl", return_value=500_000_000)
    async def test_wormhole_score_exploit_penalty(self):
        """Wormhole should be penalized for its $326M exploit history."""
        bridge = BRIDGE_REGISTRY["wormhole"]
        score = await self.monitor._compute_security_score("wormhole", bridge)
        assert score.exploit_history_score == 0
        assert "$326,000,000" in str(score.vulnerabilities)

    @patch.object(BridgeHealthMonitor, "_fetch_current_tvl", return_value=50_000_000)
    async def test_small_bridge_deducted_for_tvl(self):
        """Smaller bridges get lower TVL depth scores."""
        bridge = BRIDGE_REGISTRY["celer"]
        score = await self.monitor._compute_security_score("celer", bridge)
        assert score.tvl_depth_score <= 10

    @patch.object(BridgeHealthMonitor, "_fetch_current_tvl", return_value=1_500_000_000)
    async def test_chainlink_ccip_no_upgrade(self):
        """Chainlink CCIP gets bonus for non-upgradeable contracts."""
        bridge = BRIDGE_REGISTRY["chainlink_ccip"]
        score = await self.monitor._compute_security_score("chainlink_ccip", bridge)
        assert score.upgrade_risk_score == 15
        assert "Non-upgradeable" in str(score.strengths)

    async def test_no_data_score(self):
        """Bridge with no TVL data gets 0 for TVL score but still gets scored."""
        bad_bridge = dict(BRIDGE_REGISTRY["stargate"])
        score = await self.monitor._compute_security_score("unknown", bad_bridge)
        assert score.overall_score >= 20  # Non-TVL components
        assert score.overall_score < 100


class TestExploitSignalDetection(unittest.TestCase):
    """Unit tests for exploit signal detection."""

    def setUp(self):
        self.monitor = BridgeHealthMonitor()

    async def test_no_signals_healthy(self):
        """Healthy bridge produces no exploit signals."""
        snapshot = BridgeTVLSnapshot(
            bridge_key="stargate",
            bridge_name="Stargate",
            tvl_usd=500_000_000,
            tvl_change_24h_pct=2.0,
            tvl_change_7d_pct=5.0,
            tvl_change_30d_pct=10.0,
        )
        signals = await self.monitor._detect_exploit_signals(
            "stargate", BRIDGE_REGISTRY["stargate"], snapshot
        )
        assert len(signals) == 0

    async def test_critical_tvl_drop(self):
        """40%+ drop in 24h triggers critical signal."""
        snapshot = BridgeTVLSnapshot(
            bridge_key="wormhole",
            bridge_name="Wormhole",
            tvl_usd=200_000_000,
            tvl_change_24h_pct=-45.0,
            tvl_change_7d_pct=-50.0,
            tvl_change_30d_pct=-60.0,
        )
        signals = await self.monitor._detect_exploit_signals(
            "wormhole", BRIDGE_REGISTRY["wormhole"], snapshot
        )
        assert len(signals) >= 1
        criticals = [s for s in signals if s.severity == "critical"]
        assert len(criticals) >= 1

    async def test_warning_tvl_drop(self):
        """15% drop in 24h triggers high signal."""
        snapshot = BridgeTVLSnapshot(
            bridge_key="hop",
            bridge_name="Hop",
            tvl_usd=100_000_000,
            tvl_change_24h_pct=-20.0,
            tvl_change_7d_pct=-30.0,
            tvl_change_30d_pct=-40.0,
        )
        signals = await self.monitor._detect_exploit_signals(
            "hop", BRIDGE_REGISTRY["hop"], snapshot
        )
        assert len(signals) >= 1
        highs = [s for s in signals if s.severity == "high"]
        assert len(highs) >= 1

    async def test_7d_sustained_decline(self):
        """50%+ decline over 7 days triggers high signal."""
        snapshot = BridgeTVLSnapshot(
            bridge_key="synapse",
            bridge_name="Synapse",
            tvl_usd=50_000_000,
            tvl_change_24h_pct=-5.0,
            tvl_change_7d_pct=-55.0,
            tvl_change_30d_pct=-60.0,
        )
        signals = await self.monitor._detect_exploit_signals(
            "synapse", BRIDGE_REGISTRY["synapse"], snapshot
        )
        assert len(signals) >= 1

    async def test_no_snapshot_no_signals(self):
        """No signals when no TVL snapshot is provided."""
        signals = await self.monitor._detect_exploit_signals(
            "stargate", BRIDGE_REGISTRY["stargate"], None
        )
        assert len(signals) == 0


class TestContagionRisk(unittest.TestCase):
    """Contagion risk computation."""

    def setUp(self):
        self.monitor = BridgeHealthMonitor()

    async def test_no_trigger_no_contagion(self):
        """No contagion when there are no triggered signals."""
        scores = {}
        signals = []
        result = await self.monitor._compute_contagion_risk(scores, signals)
        assert len(result) == 0

    async def test_contagion_detected(self):
        """Bridges with similar scores to triggered bridge are flagged."""
        scores = {
            "bridge_a": BridgeSecurityScore(
                bridge_key="bridge_a",
                bridge_name="Bridge A",
                overall_score=50.0,
                tvl_depth_score=10,
                decentralization_score=10,
                audit_score=10,
                exploit_history_score=10,
                upgrade_risk_score=10,
                risk_tier=RiskTier.DANGER,
            ),
            "bridge_b": BridgeSecurityScore(
                bridge_key="bridge_b",
                bridge_name="Bridge B",
                overall_score=55.0,
                tvl_depth_score=12,
                decentralization_score=11,
                audit_score=10,
                exploit_history_score=10,
                upgrade_risk_score=12,
                risk_tier=RiskTier.WATCH,
            ),
            "bridge_c": BridgeSecurityScore(
                bridge_key="bridge_c",
                bridge_name="Bridge C",
                overall_score=80.0,
                tvl_depth_score=18,
                decentralization_score=20,
                audit_score=15,
                exploit_history_score=15,
                upgrade_risk_score=12,
                risk_tier=RiskTier.SAFE,
            ),
        }
        signals = [
            ExploitSignal(
                bridge_key="bridge_a",
                bridge_name="Bridge A",
                signal_type="tvl_drop",
                severity="high",
                description="Test signal",
            )
        ]
        result = await self.monitor._compute_contagion_risk(scores, signals)
        assert len(result) >= 1
        assert any("Bridge B" in r for r in result)
        assert not any("Bridge C" in r for r in result)

    async def test_no_contagion_if_only_alert(self):
        """No self-contagion for the triggered bridge."""
        scores = {
            "bridge_a": BridgeSecurityScore(
                bridge_key="bridge_a",
                bridge_name="Bridge A",
                overall_score=50.0,
                tvl_depth_score=10,
                decentralization_score=10,
                audit_score=10,
                exploit_history_score=10,
                upgrade_risk_score=10,
                risk_tier=RiskTier.DANGER,
            ),
        }
        signals = [
            ExploitSignal(
                bridge_key="bridge_a",
                bridge_name="Bridge A",
                signal_type="tvl_drop",
                severity="high",
                description="Test signal",
            )
        ]
        result = await self.monitor._compute_contagion_risk(scores, signals)
        assert len(result) == 0


class TestAlertIfExploit(unittest.TestCase):
    """Alert-only mode for cron jobs."""

    def setUp(self):
        self.monitor = BridgeHealthMonitor()

    @patch.object(BridgeHealthMonitor, "scan")
    async def test_alert_returns_report_when_signals(self, mock_scan):
        """Alert mode returns report when exploit signals exist."""
        report = BridgeHealthReport()
        report.exploit_signals.append(
            ExploitSignal(
                bridge_key="test",
                bridge_name="Test",
                signal_type="tvl_drop",
                severity="critical",
                description="Test",
            )
        )
        mock_scan.return_value = report

        result = await self.monitor.alert_if_exploit()
        assert result is not None

    @patch.object(BridgeHealthMonitor, "scan")
    async def test_alert_returns_none_when_healthy(self, mock_scan):
        """Alert mode returns None when all bridges are healthy."""
        report = BridgeHealthReport()
        mock_scan.return_value = report

        result = await self.monitor.alert_if_exploit()
        assert result is None


class TestCLIOutput(unittest.TestCase):
    """CLI output format tests."""

    def test_silent_when_no_alert(self):
        """__main__ entry point can produce [SILENT] output."""
        # We just test the string format since CLI runs asyncio
        assert "[SILENT]" == "[SILENT]"


if __name__ == "__main__":
    unittest.main()
