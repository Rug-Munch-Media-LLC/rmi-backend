"""
Tests: Bridge Health Monitor
=============================
Tests the x402 bridge health endpoint and the core BridgeHealthMonitor module.
"""

from unittest.mock import AsyncMock

import pytest

# ── Core Module Tests ────────────────────────────────────────────


class TestBridgeRegistry:
    """Verify bridge registry has expected bridges and their configurations."""

    def test_registry_has_all_bridges(self):
        from app.bridge_health_monitor import BRIDGE_REGISTRY

        expected = {
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
        assert set(BRIDGE_REGISTRY.keys()) == expected, (
            f"Missing bridges: {expected - set(BRIDGE_REGISTRY.keys())}"
        )
        assert len(BRIDGE_REGISTRY) == 12, "Expected exactly 12 bridges"

    def test_each_bridge_has_required_fields(self):
        from app.bridge_health_monitor import BRIDGE_REGISTRY

        required = {"name", "trust_model", "chains", "tvl_source", "audit_recency_days"}
        for key, bridge in BRIDGE_REGISTRY.items():
            for field in required:
                assert field in bridge, f"Bridge {key} missing field: {field}"
            assert isinstance(bridge["chains"], list), f"Bridge {key}: chains must be a list"
            assert len(bridge["chains"]) >= 1, f"Bridge {key}: must have at least 1 chain"


class TestSecurityScore:
    """Test the security scoring logic."""

    @pytest.mark.asyncio
    async def test_compute_security_score_high_tvl(self):
        from app.bridge_health_monitor import BRIDGE_REGISTRY, BridgeHealthMonitor

        monitor = BridgeHealthMonitor()
        monitor._fetch_current_tvl = AsyncMock(return_value=2_000_000_000)  # $2B

        score = await monitor._compute_security_score("layerzero", BRIDGE_REGISTRY["layerzero"])

        assert score.overall_score >= 70, f"Score too low: {score.overall_score}"
        assert score.tvl_depth_score >= 15, "TVL depth score too low for $2B TVL"
        assert "High TVL" in " ".join(score.strengths), "Expected TVL strength"

    @pytest.mark.asyncio
    async def test_compute_security_score_low_tvl(self):
        from app.bridge_health_monitor import BRIDGE_REGISTRY, BridgeHealthMonitor

        monitor = BridgeHealthMonitor()
        monitor._fetch_current_tvl = AsyncMock(return_value=100_000)  # $100K

        score = await monitor._compute_security_score("stargate", BRIDGE_REGISTRY["stargate"])

        assert score.overall_score <= 70, f"Score too high for low TVL: {score.overall_score}"
        assert score.tvl_depth_score <= 10, "TVL depth score should be low"

    @pytest.mark.asyncio
    async def test_wormhole_exploit_penalty(self):
        """Wormhole had a $326M exploit - should be reflected in scoring."""
        from app.bridge_health_monitor import BRIDGE_REGISTRY, BridgeHealthMonitor

        monitor = BridgeHealthMonitor()
        monitor._fetch_current_tvl = AsyncMock(return_value=500_000_000)

        score = await monitor._compute_security_score("wormhole", BRIDGE_REGISTRY["wormhole"])

        assert score.exploit_history_score == 0, "Wormhole should have 0 exploit score ($326M loss)"
        assert any("exploit" in v.lower() for v in score.vulnerabilities), (
            "Expected exploit mention in vulnerabilities"
        )

    @pytest.mark.asyncio
    async def test_chainlink_ccip_immutability_bonus(self):
        """CCIP is non-upgradeable - should get a bonus."""
        from app.bridge_health_monitor import BRIDGE_REGISTRY, BridgeHealthMonitor

        monitor = BridgeHealthMonitor()
        monitor._fetch_current_tvl = AsyncMock(return_value=800_000_000)

        score = await monitor._compute_security_score(
            "chainlink_ccip", BRIDGE_REGISTRY["chainlink_ccip"]
        )

        assert score.upgrade_risk_score >= 15, "CCIP non-upgradeable should score high"
        assert any("immutable" in s.lower() for s in score.strengths), (
            "Expected immutability strength"
        )


class TestExploitDetection:
    """Test exploit signal detection logic."""

    @pytest.mark.asyncio
    async def test_critical_tvl_drop_detection(self):
        from app.bridge_health_monitor import (
            BRIDGE_REGISTRY,
            BridgeHealthMonitor,
            BridgeTVLSnapshot,
        )

        monitor = BridgeHealthMonitor()

        # Simulate a -50% TVL drop (exceeds critical threshold of -40%)
        snapshot = BridgeTVLSnapshot(
            bridge_key="stargate",
            bridge_name="Stargate",
            tvl_usd=50_000_000,
            tvl_change_24h_pct=-50.0,
            tvl_change_7d_pct=-55.0,
            tvl_change_30d_pct=-60.0,
        )

        signals = await monitor._detect_exploit_signals(
            "stargate", BRIDGE_REGISTRY["stargate"], snapshot
        )

        assert len(signals) >= 1, "Expected exploit signal for critical TVL drop"
        assert any(s.severity == "critical" for s in signals), "Expected critical severity"


# ── API Endpoint Tests ──────────────────────────────────────────


class TestBridgeHealthEndpoint:
    """Test the x402 bridge health API endpoint."""

    @pytest.mark.asyncio
    async def test_endpoint_registration(self):
        """Verify the router is properly configured."""
        from app.routers.x402_bridge_health import router

        assert router.prefix == "/api/v1/x402-tools"
        paths = [str(getattr(r, "path", "")) for r in router.routes]
        assert any("/bridge_health" in p for p in paths), "bridge_health endpoint not registered"

    @pytest.mark.asyncio
    async def test_response_model(self):
        """Verify response model structure."""
        from app.routers.x402_bridge_health import BridgeHealthResponse

        resp = BridgeHealthResponse(
            timestamp="2026-06-15T02:00:00",
            total_bridges=12,
            bridges_healthy=10,
            bridges_watch=1,
            bridges_danger=1,
            bridges_critical=0,
            total_tvl_usd=10_000_000_000,
            tvl_change_24h_pct=-2.5,
            bridges=[],
            exploit_signals=[],
            contagion_risk=[],
            remaining_trials=2,
            pricing={"price_usd": 0.10, "is_trial": True, "trial_free": 2, "trials_remaining": 2},
        )

        assert resp.tool == "Cross-Chain Bridge Health & Exploit Monitor"
        assert resp.version == "1.0"
        assert resp.total_bridges == 12
        assert resp.total_tvl_usd == 10_000_000_000

    @pytest.mark.asyncio
    async def test_price_in_canonical(self):
        """Verify bridge_health is registered in canonical_tools with correct pricing."""
        from app.canonical_tools import CANONICAL_TOOL_PRICES

        assert "bridge_health" in CANONICAL_TOOL_PRICES
        pricing = CANONICAL_TOOL_PRICES["bridge_health"]
        assert pricing["price_usd"] == 0.10
        assert pricing["category"] == "security"
        assert pricing["trial_free"] == 2

    def test_valid_bridge_passes_validation(self):
        """Valid bridge keys should pass validation."""
        from app.routers.x402_bridge_health import BridgeHealthRequest

        req = BridgeHealthRequest(bridge="stargate", wallet=None)
        assert req.bridge == "stargate"

        req2 = BridgeHealthRequest(
            bridge="wormhole", wallet="0x1234567890abcdef1234567890abcdef12345678"
        )
        assert req2.bridge == "wormhole"

    def test_invalid_bridge_rejected(self):
        """Invalid bridge key should raise validation error."""
        from pydantic import ValidationError

        from app.routers.x402_bridge_health import BridgeHealthRequest

        with pytest.raises(ValidationError) as exc:
            BridgeHealthRequest(bridge="nonexistent_bridge")
        assert "Invalid bridge" in str(exc.value)

    def test_valid_evm_wallet_passes(self):
        """Valid EVM address should pass validation."""
        from app.routers.x402_bridge_health import BridgeHealthRequest

        req = BridgeHealthRequest(wallet="0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18")
        assert req.wallet is not None

    def test_invalid_evm_wallet_rejected(self):
        """Invalid EVM address should be rejected."""
        from pydantic import ValidationError

        from app.routers.x402_bridge_health import BridgeHealthRequest

        with pytest.raises(ValidationError):
            BridgeHealthRequest(wallet="0x1234")  # Too short

    @pytest.mark.asyncio
    async def test_valid_bridges_set(self):
        """Verify the valid bridges set matches the registry."""
        from app.bridge_health_monitor import BRIDGE_REGISTRY
        from app.routers.x402_bridge_health import VALID_BRIDGES

        assert set(BRIDGE_REGISTRY.keys()) == VALID_BRIDGES, (
            f"VALID_BRIDGES mismatch! Extra in set: {VALID_BRIDGES - set(BRIDGE_REGISTRY.keys())}. "
            f"Missing: {set(BRIDGE_REGISTRY.keys()) - VALID_BRIDGES}"
        )
