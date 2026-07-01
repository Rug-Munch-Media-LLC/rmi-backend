"""Tests for rug_pull_predictor - Exit Scam Forecaster."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.rug_pull_predictor import (
    RiskLevel,
    SignalResult,
    analyze,
    is_valid_address,
    predict_rug_pull,
)


def test_validator() -> None:
    """Address validation works correctly."""
    assert is_valid_address("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2") is True
    assert is_valid_address("So11111111111111111111111111111111111111112") is True
    assert is_valid_address("not-an-address") is False
    assert is_valid_address("") is False


def test_risk_level_enum() -> None:
    """RiskLevel enum has all expected values."""
    assert RiskLevel.SAFE.value == "safe"
    assert RiskLevel.LOW.value == "low"
    assert RiskLevel.MEDIUM.value == "medium"
    assert RiskLevel.HIGH.value == "high"
    assert RiskLevel.CRITICAL.value == "critical"


def test_signal_weighted_score() -> None:
    """SignalResult weighted_score calculation is correct."""
    s = SignalResult(signal="test", score=80.0, weight=0.25)
    assert s.weighted_score == 20.0  # 80 * 0.25
    assert s.level == RiskLevel.CRITICAL  # 80 >= 80 threshold

    s2 = SignalResult(signal="test2", score=79.9, weight=0.5)
    assert s2.level == RiskLevel.HIGH


def test_analyze_structure() -> None:
    """Analyze returns expected dict structure."""
    result = analyze(
        "So11111111111111111111111111111111111111112",
        chain="solana",
    )
    assert isinstance(result, dict)
    assert "rug_score" in result
    assert "risk_level" in result
    assert "signals" in result
    assert "summary" in result
    assert "recommendations" in result
    assert isinstance(result["rug_score"], float)
    assert 0 <= result["rug_score"] <= 100


def test_analyze_live() -> None:
    """End-to-end test with real data (WSOL)."""
    result = analyze(
        "So11111111111111111111111111111111111111112",
        chain="solana",
    )
    print(f"\nWSOL Rug Score: {result['rug_score']} ({result['risk_level']})")
    print(f"Signals: {len(result['signals'])}")
    for sig in result["signals"]:
        print(f"  {sig['name']}: {sig['score']} ({sig['level']})")
    # WSOL should be low risk
    assert result["risk_level"] in ("safe", "low")


def test_invalid_address() -> None:
    """Invalid address raises ValueError."""
    try:
        asyncio.run(predict_rug_pull("not-an-address"))
        raise AssertionError("Should have raised ValueError")
    except ValueError:
        pass


def test_analyze_exported() -> None:
    """analyze is exported in __all__."""
    from app.rug_pull_predictor import __all__

    assert "analyze" in __all__


if __name__ == "__main__":
    test_validator()
    test_risk_level_enum()
    test_signal_weighted_score()
    test_invalid_address()
    test_analyze_exported()
    test_analyze_structure()
    test_analyze_live()
    print("\nAll tests passed!")
