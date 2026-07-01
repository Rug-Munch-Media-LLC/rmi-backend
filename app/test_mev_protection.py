"""
Tests for mev_protection.py — MEV Shield Analysis
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.mev_protection import (
    MevFactor,
    MevRiskLevel,
    MevShieldResult,
    _check_gas_price_risk,
    _check_historical_mev,
    _check_mempool_risk,
    _check_pool_liquidity,
    _check_slippage_risk,
    _check_timing_risk,
    analyze_transaction_risk,
    mev_shield_analysis,
)


async def test_mempool_risk() -> None:
    """Test mempool activity risk assessment."""
    print("🧪 test_mempool_risk")
    result = await _check_mempool_risk("ethereum")
    assert isinstance(result, MevFactor)
    assert 0.0 <= result.score <= 1.0
    assert result.name == "mempool_activity"
    print(f"  Score: {result.score:.2f} — {result.finding}")
    print(f"  Suggestion: {result.suggestion}")
    print("  ✅ PASS")


async def test_slippage_risk() -> None:
    """Test slippage tolerance assessment."""
    print("\n🧪 test_slippage_risk")

    # Low slippage (0.5%)
    low = await _check_slippage_risk(50)
    assert low.score <= 0.3
    print(f"  Low (0.5%): score={low.score:.2f} — {low.finding}")

    # Medium slippage (1%)
    med = await _check_slippage_risk(100)
    assert 0.3 <= med.score <= 0.7
    print(f"  Med (1.0%): score={med.score:.2f} — {med.finding}")

    # High slippage (3%+)
    high = await _check_slippage_risk(500)
    assert high.score >= 0.7
    print(f"  High (5%): score={high.score:.2f} — {high.finding}")

    print("  ✅ PASS")


async def test_gas_price_risk() -> None:
    """Test gas price risk assessment."""
    print("\n🧪 test_gas_price_risk")

    # Low slippage (0.5%)
    low = await _check_gas_price_risk(5.0)
    assert low.score <= 0.3
    print(f"  Low (5 gwei): score={low.score:.2f} — {low.finding}")

    med = await _check_gas_price_risk(25.0)
    assert 0.3 <= med.score <= 0.6
    print(f"  Med (25 gwei): score={med.score:.2f} — {med.finding}")

    high = await _check_gas_price_risk(75.0)
    assert high.score >= 0.6
    print(f"  High (75 gwei): score={high.score:.2f} — {high.finding}")

    print("  ✅ PASS")


async def test_timing_risk() -> None:
    """Test timing-based risk assessment."""
    print("\n🧪 test_timing_risk")
    result = await _check_timing_risk()
    assert isinstance(result, MevFactor)
    assert 0.0 <= result.score <= 1.0
    print(f"  Score: {result.score:.2f} — {result.finding}")
    print(f"  Suggestion: {result.suggestion}")
    print("  ✅ PASS")


async def test_historical_mev() -> None:
    """Test historical MEV check (fallback mode, no actual detector loaded)."""
    print("\n🧪 test_historical_mev (fallback — no real detector)")
    result = await _check_historical_mev("ethereum")
    assert isinstance(result, MevFactor)
    assert result.score > 0  # Should have default chain risk score
    print(f"  Score: {result.score:.2f} — {result.finding}")
    print("  ✅ PASS")


async def test_liquidity_risk() -> None:
    """Test pool liquidity assessment (no real pair — should return unknown)."""
    print("\n🧪 test_liquidity_risk (no pair address)")
    result = await _check_pool_liquidity(None, "ethereum")
    assert isinstance(result, MevFactor)
    assert result.name == "pool_liquidity"
    print(f"  Score: {result.score:.2f} — {result.finding}")
    print("  ✅ PASS")


async def test_full_analysis() -> None:
    """Test full MEV Shield analysis with default params."""
    print("\n🧪 test_full_analysis")
    result = await analyze_transaction_risk(
        chain="ethereum",
        slippage_bps=100,
        gas_price_gwei=15.0,
    )
    assert isinstance(result, MevShieldResult)
    assert isinstance(result.risk_level, MevRiskLevel)
    assert 0.0 <= result.overall_score <= 1.0
    assert len(result.factors) == 6
    assert len(result.protection_strategies) > 0
    assert result.estimated_loss_bps > 0

    print(f"  Risk Level: {result.risk_level.value.upper()}")
    print(f"  Overall Score: {result.overall_score:.2f}")
    print(f"  Est. Loss: {result.estimated_loss_bps} bps")

    for f in result.factors:
        print(f"    {f.name:25s} {f.score:.2f} — {f.finding}")

    print("  ✅ PASS")


async def test_full_high_risk() -> None:
    """Test full analysis with high-risk params."""
    print("\n🧪 test_full_high_risk")
    result = await analyze_transaction_risk(
        chain="ethereum",
        slippage_bps=500,  # 5% — reckless
        gas_price_gwei=200.0,  # Premium gas
    )
    assert result.risk_level in (MevRiskLevel.HIGH, MevRiskLevel.CRITICAL)
    assert len(result.protection_strategies) >= 2
    print(f"  Risk Level: {result.risk_level.value.upper()}")
    print(f"  Overall Score: {result.overall_score:.2f}")
    print("  ✅ PASS")


async def test_sync_wrapper() -> None:
    """Test the synchronous wrapper function."""
    print("\n🧪 test_sync_wrapper")
    result = mev_shield_analysis(
        chain="ethereum",
        slippage_bps=100,
        gas_price_gwei=10.0,
    )
    assert isinstance(result, dict)
    assert "risk_level" in result
    assert "factors" in result
    assert "protection_strategies" in result
    assert "estimated_loss_bps" in result
    print(f"  Risk Level: {result['risk_level']}")
    print(f"  Loss (bps): {result['estimated_loss_bps']}")
    print("  ✅ PASS")


async def test_result_to_dict() -> None:
    """Test serialization."""
    print("\n🧪 test_result_to_dict")
    result = await analyze_transaction_risk(
        chain="bsc",
        slippage_bps=200,
        gas_price_gwei=5.0,
    )
    d = result.to_dict()
    assert isinstance(d, dict)
    assert json.dumps(d)  # Must be JSON-serializable
    assert "factors" in d
    assert len(d["factors"]) == 6
    print(f"  JSON output: {json.dumps(d, indent=2)[:200]}...")
    print("  ✅ PASS")


async def main() -> bool:
    print("=" * 60)
    print("MEV Shield Analysis — Test Suite")
    print("=" * 60)

    tests = [
        test_mempool_risk,
        test_slippage_risk,
        test_gas_price_risk,
        test_timing_risk,
        test_historical_mev,
        test_liquidity_risk,
        test_full_analysis,
        test_full_high_risk,
        test_sync_wrapper,
        test_result_to_dict,
    ]

    passed = 0
    failed = 0

    for test_fn in tests:
        try:
            await test_fn()
            passed += 1
        except Exception as e:
            print(f"  ❌ FAIL: {e}")
            import traceback

            traceback.print_exc()
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"Results: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
