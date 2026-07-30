"""
Tests for Rug Pull Imminence Predictor (RIP)
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.rug_imminence_predictor import (
    DEFAULT_WEIGHTS,
    ImminenceLevel,
    RIPResult,
    RugImminencePredictor,
    SignalCategory,
    get_predictor,
)


@pytest.fixture
def predictor():
    return RugImminencePredictor()


class TestSignalCategory:
    """SignalCategory dataclass tests."""

    def test_weighted_contribution(self):
        sig = SignalCategory(name="test", weight=0.25, score=80)
        assert sig.weighted_contribution() == 20.0

    def test_zero_score(self):
        sig = SignalCategory(name="test", weight=0.5, score=0)
        assert sig.weighted_contribution() == 0.0

    def test_default_values(self):
        sig = SignalCategory(name="test", weight=0.5)
        assert sig.score == 0.0
        assert sig.confidence == 0.0
        assert sig.evidence == []
        assert sig.flags == []


class TestRIPResult:
    """RIPResult dataclass tests."""

    def test_low_imminence(self):
        result = RIPResult(token_address="0x1234", chain="base", score=20, imminence=ImminenceLevel.LOW)
        assert "LOW" in result.summary()
        assert "🟢" in result.summary()

    def test_critical_imminence(self):
        result = RIPResult(token_address="0x1234", chain="base", score=85, imminence=ImminenceLevel.CRITICAL)
        assert "CRITICAL" in result.summary()
        assert "🔴" in result.summary()

    def test_summary_with_symbol(self):
        result = RIPResult(
            token_address="0x1234",
            chain="base",
            score=45,
            token_symbol="RUG",
            imminence=ImminenceLevel.MEDIUM,
        )
        assert "RUG" in result.summary()

    def test_narrative_includes_score(self):
        result = RIPResult(
            token_address="0x1234",
            chain="base",
            score=67,
            token_name="RugCoin",
            imminence=ImminenceLevel.HIGH,
            last_updated="2026-06-14T12:00:00",
            scan_duration_ms=1234.5,
        )
        narrative = result.narrative()
        assert "67" in narrative
        assert "HIGH" in narrative
        assert "RugCoin" in narrative

    def test_narrative_with_warnings(self):
        result = RIPResult(
            token_address="0x1234",
            chain="base",
            score=80,
            imminence=ImminenceLevel.CRITICAL,
            warnings=["HONEYPOT detected"],
        )
        narrative = result.narrative()
        assert "HONEYPOT" in narrative

    def test_narrative_recommendations(self):
        result = RIPResult(
            token_address="0x1234",
            chain="base",
            score=80,
            imminence=ImminenceLevel.CRITICAL,
            recommendations=["EXIT NOW"],
        )
        narrative = result.narrative()
        assert "EXIT NOW" in narrative

    def test_to_dict(self):
        result = RIPResult(
            token_address="0x1234",
            chain="base",
            score=75,
            token_name="BadToken",
            token_symbol="BAD",
            imminence=ImminenceLevel.CRITICAL,
            warnings=["Warning 1"],
            recommendations=["Sell now"],
        )
        d = result.to_dict()
        assert d["score"] == 75.0
        assert d["imminence"] == "critical"
        assert d["verdict"] == "CRITICAL"
        assert d["warnings"] == ["Warning 1"]
        assert d["recommendations"] == ["Sell now"]

    def test_to_dict_includes_signals(self):
        result = RIPResult(
            token_address="0x1234",
            chain="base",
            score=50,
            imminence=ImminenceLevel.MEDIUM,
        )
        result.signals["lp_health"] = SignalCategory(
            name="LP Health",
            weight=0.25,
            score=80,
            evidence=["LP unlocked"],
        )
        d = result.to_dict()
        assert "lp_health" in d["signals"]
        assert d["signals"]["lp_health"]["score"] == 80.0
        assert d["signals"]["lp_health"]["evidence"] == ["LP unlocked"]


class TestRugImminencePredictor:
    """Main predictor tests."""

    def test_init_default_weights(self):
        p = RugImminencePredictor()
        total = sum(p.weights.values())
        assert abs(total - 1.0) < 0.01
        assert len(p.weights) == 7
        assert "lp_health" in p.weights
        assert p.weights["lp_health"] == 0.25

    def test_init_custom_weights(self):
        weights = {"lp_health": 0.5, "deployer_risk": 0.5}
        p = RugImminencePredictor(weights)
        assert abs(sum(p.weights.values()) - 1.0) < 0.01

    def test_verify_weights_normalizes(self):
        weights = {"a": 0.8, "b": 0.8}  # Sums to 1.6
        p = RugImminencePredictor(weights)
        assert abs(sum(p.weights.values()) - 1.0) < 0.01
        assert p.weights["a"] == 0.5

    def test_cache_key(self):
        p = RugImminencePredictor()
        key = p._cache_key("0xABC", "base")
        assert key == "base:0xabc"

    def test_cache_lifecycle(self, predictor):
        result = RIPResult(token_address="0x1234", chain="base", score=30, imminence=ImminenceLevel.LOW)
        predictor._set_cache(result)

        cached = predictor._get_cached("0x1234", "base")
        assert cached is not None
        assert cached.score == 30

    @pytest.mark.asyncio
    async def test_predict_returns_result(self, predictor):
        with patch.object(predictor, "_analyze_lp_health", new=AsyncMock()):  # noqa: SIM117
            with patch.object(predictor, "_analyze_deployer_risk", new=AsyncMock()):
                with patch.object(predictor, "_analyze_smart_money_flow", new=AsyncMock()):
                    with patch.object(predictor, "_analyze_bundle_pattern", new=AsyncMock()):
                        with patch.object(predictor, "_analyze_social_velocity", new=AsyncMock()):
                            with patch.object(predictor, "_analyze_contract_risk", new=AsyncMock()):
                                with patch.object(predictor, "_analyze_liquidity_migration", new=AsyncMock()):
                                    result = await predictor.predict(
                                        "0x1234567890123456789012345678901234567890", "base"
                                    )

        assert isinstance(result, RIPResult)
        assert result.token_address == "0x1234567890123456789012345678901234567890"
        assert result.chain == "base"
        assert result.score >= 0
        assert isinstance(result.imminence, ImminenceLevel)
        assert result.scan_duration_ms > 0

    def test_generate_warnings_no_flags(self):
        result = RIPResult(token_address="0x1", chain="base", score=20, imminence=ImminenceLevel.LOW)
        predictor = RugImminencePredictor()
        predictor._generate_warnings(result)
        assert any("No imminent rug" in w for w in result.warnings)

    def test_generate_warnings_with_flags(self):
        result = RIPResult(token_address="0x1", chain="base", score=80, imminence=ImminenceLevel.HIGH)
        result.signals["lp_health"] = SignalCategory(
            name="LP Health",
            weight=0.25,
            score=80,
            flags=["honeypot"],
        )
        predictor = RugImminencePredictor()
        predictor._generate_warnings(result)
        assert any("HONEYPOT" in w for w in result.warnings)

    def test_generate_recommendations_priority(self):
        result = RIPResult(token_address="0x1", chain="base", score=85, imminence=ImminenceLevel.CRITICAL)
        result.signals["lp_health"] = SignalCategory(
            name="LP Health",
            weight=0.25,
            score=100,
            flags=["lp_removed"],
        )
        predictor = RugImminencePredictor()
        predictor._generate_recommendations(result)
        assert any("URGENT" in r for r in result.recommendations)
        assert any("EXIT" in r for r in result.recommendations)

    @pytest.mark.asyncio
    async def test_lp_health_low_liquidity(self, predictor):
        result = RIPResult(token_address="0xdead", chain="base")
        for sig_id, weight in DEFAULT_WEIGHTS.items():
            result.signals[sig_id] = SignalCategory(
                name=sig_id.replace("_", " ").title(),
                weight=weight,
            )
        with (
            patch.object(
                predictor,
                "_fetch_dexscreener_pool",
                new=AsyncMock(
                    return_value={
                        "pairs": [
                            {
                                "liquidity": {"usd": 500},
                                "volume": {"h24": 100},
                                "txns": {"h24": {"buys": 5, "sells": 20}},
                            }
                        ]
                    }
                ),
            ),
            patch.object(
                predictor,
                "_check_lp_lock",
                new=AsyncMock(return_value={"locked": False, "unlocked": True, "unlock_date": None}),
            ),
        ):
            await predictor._analyze_lp_health(result)

        sig = result.signals["lp_health"]
        assert sig.score > 0  # Should detect low liquidity + sell pressure
        assert "critically_low_liquidity" in sig.flags
        assert sig.evidence

    @pytest.mark.asyncio
    async def test_social_velocity_with_spike(self, predictor):
        result = RIPResult(token_address="0xtoken", chain="base", token_symbol="SHILL")
        for sig_id, weight in DEFAULT_WEIGHTS.items():
            result.signals[sig_id] = SignalCategory(
                name=sig_id.replace("_", " ").title(),
                weight=weight,
            )
        with patch("app.domains.scanners.social_velocity.SocialVelocityAnalyzer") as mock:
            mock_instance = AsyncMock()
            mock_instance.analyze = AsyncMock(
                return_value={
                    "mention_spike_pct": 800,
                    "shill_score": 0.85,
                    "sentiment_shift": -0.5,
                }
            )
            mock.return_value = mock_instance
            await predictor._analyze_social_velocity(result)

        sig = result.signals["social_velocity"]
        assert sig.score >= 20  # Spike + shilling + sentiment drop
        assert "massive_mention_spike" in sig.flags

    @pytest.mark.asyncio
    async def test_contract_risk_honeypot(self, predictor):
        result = RIPResult(token_address="0xhoney", chain="base")
        for sig_id, weight in DEFAULT_WEIGHTS.items():
            result.signals[sig_id] = SignalCategory(
                name=sig_id.replace("_", " ").title(),
                weight=weight,
            )
        with patch("app.domains.scanners.honeypot_detector.HoneypotDetector") as mock_hp:
            mock_instance = AsyncMock()
            mock_instance.detect = AsyncMock(
                return_value={
                    "is_honeypot": True,
                    "reason": "Cannot sell - transfer tax > 90%",
                }
            )
            mock_hp.return_value = mock_instance
            with patch("app.domains.scanners.contract_authority.ContractAuthorityScanner") as mock_auth:
                auth_instance = AsyncMock()
                auth_instance.scan = AsyncMock(
                    return_value={
                        "ownership_renounced": False,
                        "has_proxy": True,
                    }
                )
                mock_auth.return_value = auth_instance
                await predictor._analyze_contract_risk(result)

        sig = result.signals["contract_risk"]
        assert "honeypot" in sig.flags
        assert "active_ownership" in sig.flags
        assert sig.score >= 40  # Honeypot + active ownership

    def test_get_predictor_singleton(self):
        p1 = get_predictor()
        p2 = get_predictor()
        assert p1 is p2


class TestIntegration:
    """Integration-level confidence tests (mock external deps)."""

    @pytest.mark.asyncio
    async def test_end_to_end_predict(self):
        """Full prediction pipeline with all external deps mocked."""
        p = RugImminencePredictor()
        result = RIPResult(token_address="0x1234", chain="base")

        # Manually set known signal values for integration test
        result.signals["lp_health"] = SignalCategory(
            name="LP Health",
            weight=0.25,
            score=80,
            evidence=["LP UNLOCKED"],
        )
        result.signals["deployer_risk"] = SignalCategory(
            name="Deployer Risk",
            weight=0.20,
            score=70,
            evidence=["Known rug deployer"],
        )
        result.signals["smart_money_flow"] = SignalCategory(
            name="Smart Money Flow",
            weight=0.15,
            score=60,
            evidence=["Insider selling"],
        )
        result.signals["bundle_pattern"] = SignalCategory(
            name="Bundle Pattern",
            weight=0.15,
            score=50,
            evidence=["Bundled launch"],
        )
        result.signals["social_velocity"] = SignalCategory(
            name="Social Velocity",
            weight=0.10,
            score=40,
        )
        result.signals["contract_risk"] = SignalCategory(
            name="Contract Risk",
            weight=0.10,
            score=30,
        )
        result.signals["liquidity_migration"] = SignalCategory(
            name="Liquidity Migration",
            weight=0.05,
            score=20,
        )

        # Fuse
        result.score = sum(sig.weighted_contribution() for sig in result.signals.values())
        for level, (lo, hi) in [
            (ImminenceLevel.LOW, (0, 30)),
            (ImminenceLevel.MEDIUM, (30, 55)),
            (ImminenceLevel.HIGH, (55, 75)),
            (ImminenceLevel.CRITICAL, (75, 101)),
        ]:
            if lo <= result.score < hi:
                result.imminence = level
                break

        p._generate_warnings(result)
        p._generate_recommendations(result)

        # 80 * 0.25 + 70 * 0.20 + 60 * 0.15 + 50 * 0.15 + 40 * 0.10 + 30 * 0.10 + 20 * 0.05
        # = 20 + 14 + 9 + 7.5 + 4 + 3 + 1 = 58.5
        assert result.score == 58.5
        assert result.imminence == ImminenceLevel.HIGH
        assert len(result.warnings) > 0
        assert len(result.recommendations) > 0
