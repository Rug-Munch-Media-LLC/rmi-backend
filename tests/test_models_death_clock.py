"""DeathClockResult contract — moved out of test_models*.py (size cap).

The three cases below live in one place: the response model must expose the
prediction, the risk band and direction words, and must never serialize the
weights, the intercept or a numeric per-factor contribution.
"""

from __future__ import annotations


class TestDeathClockModel:
    """DeathClockResult model from death_clock.py."""

    def test_valid_result(self):
        from app.routers.death_clock import DeathClockResult

        result = DeathClockResult(
            token_address="0xabc",
            chain="solana",
            symbol="TEST",
            predicted_days=30.0,
            risk_level="HIGH",
            confidence=0.85,
            reasons=["liquidity is thin for the current holder base"],
            factors={"liquidity_usd": "shortens", "age_days": "neutral"},
            explanation="Test explanation",
        )
        assert result.token_address == "0xabc"
        assert result.risk_level == "HIGH"
        assert result.confidence == 0.85
        # Contract: `factors` is a feature -> direction map, never a contribution.
        assert result.factors["liquidity_usd"] == "shortens"
        assert set(result.factors.values()) <= {"shortens", "extends", "neutral"}
        dumped = result.model_dump()
        assert "weights" not in dumped
        assert "intercept" not in dumped

    def test_death_clock_boundary_confidence(self):
        from app.routers.death_clock import DeathClockResult

        result = DeathClockResult(
            token_address="0xabc",
            chain="solana",
            symbol="TEST",
            predicted_days=0.0,
            risk_level="LOW",
            confidence=0.0,
            reasons=[],
            factors={},
            explanation="",
        )
        assert result.predicted_days == 0.0
        assert result.confidence == 0.0
        assert result.factors == {}, "no features scored means no directions, not a bench of zeros"
        dumped = result.model_dump()
        assert "weights" not in dumped
        assert "intercept" not in dumped

    def test_death_clock_high_confidence(self):
        from app.routers.death_clock import DeathClockResult

        result = DeathClockResult(
            token_address="0xabc",
            chain="solana",
            symbol="TEST",
            predicted_days=365.0,
            risk_level="CRITICAL",
            confidence=0.99,
            reasons=["honeypot behaviour reported: holders may be unable to sell"],
            factors={"liquidity_usd": "shortens", "holder_count": "extends"},
            explanation="Multiple high-risk factors detected",
        )
        assert result.confidence == 0.99
        # Directions only: the sign of a contribution is allowed out, its size is not.
        assert result.factors["liquidity_usd"] == "shortens"
        assert result.factors["holder_count"] == "extends"
        assert set(result.factors.values()) <= {"shortens", "extends", "neutral"}
        assert result.risk_level == "CRITICAL"
