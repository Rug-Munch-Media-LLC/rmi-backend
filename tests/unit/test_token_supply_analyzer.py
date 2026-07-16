"""
Tests for TokenSupplyAnalyzer
"""

import unittest
from datetime import UTC, datetime

from app.token_supply_analyzer import (
    LockStatus,
    SupplyRisk,
    TokenSupplyProfile,
    _analyze_concentration,
    _analyze_liquidity,
    _analyze_supply_mechanics,
    _classify_risk,
    _compute_overall_risk,
    _compute_pair_age_days,
    _detect_chain,
    _normalize_address,
)


class TestSupplyScoring(unittest.TestCase):
    """Test the supply mechanics analysis engine."""

    def test_healthy_supply(self) -> None:
        """A token with normal supply metrics should score low risk."""
        profile = TokenSupplyProfile(address="0xtest123")
        profile.total_supply = 1_000_000_000
        profile.circulating_supply = 800_000_000
        profile.max_supply = 1_000_000_000
        profile.burned_supply = 50_000_000
        profile.has_mint_function = False
        profile.is_deflationary = True

        _analyze_supply_mechanics(profile)
        assert profile.supply_risk_score < 30, f"Healthy supply should score low, got {profile.supply_risk_score}"

    def test_unlimited_mint_supply(self) -> None:
        """A token with no max supply and mint function should score high."""
        profile = TokenSupplyProfile(address="0xevil123")
        profile.total_supply = 1_000_000
        profile.circulating_supply = 100_000
        profile.max_supply = 0
        profile.has_mint_function = True
        profile.mint_function_type = "unlimited"

        _analyze_supply_mechanics(profile)
        assert profile.supply_risk_score > 50, f"Unlimited mint should score high, got {profile.supply_risk_score}"
        assert "unlimited_mint_function" in str(profile.patterns_detected)

    def test_supply_exceeds_max(self) -> None:
        """A token where total > max supply should be critical."""
        profile = TokenSupplyProfile(address="0xoverflow")
        profile.total_supply = 2_000_000_000
        profile.circulating_supply = 1_500_000_000
        profile.max_supply = 1_000_000_000

        _analyze_supply_mechanics(profile)
        assert profile.supply_risk_score >= 40, f"Supply overflow should score high, got {profile.supply_risk_score}"
        assert "supply_exceeds_max" in str(profile.patterns_detected)

    def test_dilution_risk(self) -> None:
        """A token with <30% circ/total ratio should flag dilution."""
        profile = TokenSupplyProfile(address="0xdilute")
        profile.total_supply = 10_000_000
        profile.circulating_supply = 2_000_000

        _analyze_supply_mechanics(profile)
        assert "high_dilution_risk" in str(profile.patterns_detected)

    def test_large_mint_headroom(self) -> None:
        """A token with >50% of max supply remaining should flag."""
        profile = TokenSupplyProfile(address="0xmintroom")
        profile.total_supply = 100_000
        profile.circulating_supply = 80_000
        profile.max_supply = 1_000_000
        profile.has_mint_function = True

        _analyze_supply_mechanics(profile)
        assert "large_mint_headroom" in str(profile.patterns_detected)


class TestConcentrationScoring(unittest.TestCase):
    """Test holder concentration analysis."""

    def test_extreme_concentration(self) -> None:
        """Top 10 holding >99% should be critical."""
        profile = TokenSupplyProfile(address="0xconcentrated")
        profile.top_10_holder_pct = 99.5
        profile.top_50_holder_pct = 99.9

        _analyze_concentration(profile)
        assert "extreme_concentration:top_10_holds_>99%" in str(profile.patterns_detected)
        assert profile.concentration_risk in (SupplyRisk.HIGH, SupplyRisk.CRITICAL)

    def test_deployer_holds_majority(self) -> None:
        """Deployer holding >50% should be critical."""
        profile = TokenSupplyProfile(address="0xdeployer_owns")
        profile.deployer_hold_pct = 75.0
        profile.top_10_holder_pct = 85.0

        _analyze_concentration(profile)
        assert "deployer_holds_majority" in str(profile.patterns_detected)

    def test_no_concentration(self) -> None:
        """No significant concentration should score low."""
        profile = TokenSupplyProfile(address="0xfair_dist")
        profile.deployer_hold_pct = 2.0
        profile.top_10_holder_pct = 15.0
        profile.top_50_holder_pct = 30.0

        _analyze_concentration(profile)
        assert profile.concentration_score < 20, f"Fair distribution should score low, got {profile.concentration_score}"


class TestLiquidityScoring(unittest.TestCase):
    """Test liquidity analysis."""

    def test_liquidity_removed(self) -> None:
        """Removed liquidity should be critical."""
        profile = TokenSupplyProfile(address="0xrugpull")
        profile.lock_status = LockStatus.REMOVED

        _analyze_liquidity(profile)
        assert "liquidity_removed" in str(profile.patterns_detected)
        assert profile.liquidity_score > 40

    def test_liquidity_locked(self) -> None:
        """Locked liquidity should reduce risk."""
        profile = TokenSupplyProfile(address="0xsafe")
        profile.lock_status = LockStatus.LOCKED
        profile.liquidity_lock_expiry = "2027-01-01"
        profile.pair_age_days = 60
        profile.pair_liquidity_usd = 200_000

        _analyze_liquidity(profile)
        assert "liquidity_locked" in str(profile.patterns_detected)
        assert profile.liquidity_score < 20

    def test_very_new_pair(self) -> None:
        """Very new pair should be flagged."""
        profile = TokenSupplyProfile(address="0xnew_token")
        profile.pair_age_days = 0.5
        profile.pair_liquidity_usd = 500

        _analyze_liquidity(profile)
        assert "very_new_pair" in str(profile.patterns_detected)
        assert "very_low_liquidity" in str(profile.patterns_detected)


class TestOverallRisk(unittest.TestCase):
    """Test overall risk computation."""

    def test_critical_token(self) -> None:
        """A token with multiple red flags should be critical."""
        profile = TokenSupplyProfile(address="0xscam_token")
        profile.has_mint_function = True
        profile.mint_function_type = "unlimited"
        profile.has_dynamic_tax = True
        profile.has_blacklist = True
        profile.buy_tax_pct = 15.0
        profile.sell_tax_pct = 25.0
        profile.lock_status = LockStatus.REMOVED
        profile.top_10_holder_pct = 99.0
        profile.is_renounced = False
        profile.has_proxy_admin = True

        _analyze_supply_mechanics(profile)
        _analyze_concentration(profile)
        _analyze_liquidity(profile)
        _compute_overall_risk(profile)
        assert profile.overall_risk in (SupplyRisk.HIGH, SupplyRisk.CRITICAL), f"Scam token should be high/critical, got {profile.overall_risk}"

    def test_safe_token(self) -> None:
        """A token with good practices should be safe/low."""
        profile = TokenSupplyProfile(address="0xgood_token")
        profile.total_supply = 1_000_000
        profile.circulating_supply = 950_000
        profile.max_supply = 1_000_000
        profile.burned_supply = 50_000
        profile.has_mint_function = False
        profile.is_deflationary = True
        profile.buy_tax_pct = 1.0
        profile.sell_tax_pct = 1.0
        profile.lock_status = LockStatus.LOCKED
        profile.is_renounced = True
        profile.top_10_holder_pct = 20.0
        profile.top_50_holder_pct = 35.0
        profile.deployer_hold_pct = 3.0
        profile.pair_age_days = 120
        profile.pair_liquidity_usd = 500_000

        _analyze_supply_mechanics(profile)
        _analyze_concentration(profile)
        _analyze_liquidity(profile)
        _compute_overall_risk(profile)
        assert profile.overall_risk in (SupplyRisk.SAFE, SupplyRisk.LOW), f"Safe token should be safe/low, got {profile.overall_risk}"


class TestUtilities(unittest.TestCase):
    """Test utility functions."""

    def test_detect_chain(self) -> None:
        """Chain detection should work for common formats."""
        assert _detect_chain("0xabcd1234") == "ethereum"
        assert _detect_chain("AbCdEf1234567890AbCdEf1234567890AbCdEf1234567890AbCdEf1234567890") == "solana"
        assert _detect_chain("unknown") == "unknown"

    def test_normalize_address(self) -> None:
        """Address normalization should lowercase and strip."""
        assert _normalize_address("0xABC123") == "0xabc123"
        assert _normalize_address("  0xABC  ") == "0xabc"

    def test_risk_classification(self) -> None:
        """Risk classification boundaries."""
        assert _classify_risk(0) == SupplyRisk.SAFE
        assert _classify_risk(10) == SupplyRisk.SAFE
        assert _classify_risk(11) == SupplyRisk.LOW
        assert _classify_risk(25) == SupplyRisk.LOW
        assert _classify_risk(26) == SupplyRisk.MEDIUM
        assert _classify_risk(50) == SupplyRisk.MEDIUM
        assert _classify_risk(51) == SupplyRisk.HIGH
        assert _classify_risk(75) == SupplyRisk.HIGH
        assert _classify_risk(76) == SupplyRisk.CRITICAL

    def test_pair_age_computation(self) -> None:
        """Pair age from Unix ms timestamp."""
        now = datetime.now(tz=UTC)
        one_day_ago = int((now.timestamp() - 86400) * 1000)
        age = _compute_pair_age_days(one_day_ago)
        self.assertAlmostEqual(age, 1.0, delta=0.1)

        # Zero timestamp
        assert _compute_pair_age_days(0) == 0.0

        # Future timestamp
        future = int((now.timestamp() + 86400) * 1000)
        assert _compute_pair_age_days(future) == 0.0


class TestOwnershipAndTax(unittest.TestCase):
    """Test ownership and tax analysis in overall risk."""

    def test_proxy_admin_risk(self) -> None:
        """Proxy admin should flag upgrade risk."""
        profile = TokenSupplyProfile(address="0xproxy")
        profile.has_proxy_admin = True
        profile.is_renounced = False

        _compute_overall_risk(profile)
        assert "proxy_admin:contract_can_be_upgraded" in str(profile.patterns_detected)

    def test_multisig_benefit(self) -> None:
        """Multi-sig ownership should reduce risk."""
        profile = TokenSupplyProfile(address="0xmultisig")
        profile.has_multisig = True
        profile.is_renounced = False

        _compute_overall_risk(profile)
        assert "multisig_owner" in str(profile.patterns_detected)

    def test_dynamic_tax_detection(self) -> None:
        """Dynamic tax should be flagged."""
        profile = TokenSupplyProfile(address="0xtaxy")
        profile.has_dynamic_tax = True
        profile.has_blacklist = True

        _compute_overall_risk(profile)
        assert "dynamic_tax:tax_can_change" in str(profile.patterns_detected)
        assert "blacklist_function" in str(profile.patterns_detected)

    def test_high_sell_tax(self) -> None:
        """Very high sell tax should flag honeypot risk."""
        profile = TokenSupplyProfile(address="0xhoneypot")
        profile.sell_tax_pct = 99.0

        _compute_overall_risk(profile)
        assert "very_high_sell_tax:99.0%" in str(profile.patterns_detected)


if __name__ == "__main__":
    unittest.main()
