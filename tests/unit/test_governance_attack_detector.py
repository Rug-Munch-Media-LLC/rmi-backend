"""
Tests for Governance Attack & Concentration Risk Detector
==========================================================
Tests holder concentration analysis, governance parameter extraction,
flash-loan feasibility assessment, and risk scoring.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.governance_attack_detector import (
    DEXSCREENER_API,
    LOW_QUORUM_PCT,
    TOP_10_CRITICAL_PCT,
    TOP_HOLDER_CRITICAL_PCT,
    GovernanceParams,
    _parse_holders,
    _score_governance_risk,
)


class TestHolderParsing:
    """Holder data parsing tests."""

    def test_parse_empty(self):
        holders = _parse_holders([])
        assert holders == []

    def test_parse_single_holder(self):
        raw = [{"address": "0xabc", "percentage": "50.5"}]
        holders = _parse_holders(raw)
        assert len(holders) == 1
        assert holders[0].address == "0xabc"
        assert holders[0].percentage == 50.5

    def test_parse_exchange_label(self):
        raw = [
            {"address": "0xbinance1", "percentage": "10.0", "label": "Binance"},
            {"address": "0xrandom", "percentage": "5.0", "label": ""},
        ]
        holders = _parse_holders(raw)
        assert holders[0].is_exchange is True
        assert holders[1].is_exchange is False

    def test_parse_sorted(self):
        raw = [
            {"address": "0xsmall", "percentage": "1.0"},
            {"address": "0xbig", "percentage": "50.0"},
            {"address": "0xmedium", "percentage": "10.0"},
        ]
        holders = _parse_holders(raw)
        assert holders[0].address == "0xbig"
        assert holders[1].address == "0xmedium"
        assert holders[2].address == "0xsmall"

    def test_parse_etherscan_format(self):
        raw = [{"TokenHolderAddress": "0xdef", "TokenHolderQuantity": "25.0"}]
        holders = _parse_holders(raw)
        assert len(holders) == 1
        assert holders[0].address == "0xdef"
        assert holders[0].percentage == 25.0

    def test_parse_contract_flag(self):
        raw = [{"address": "0xcontract", "percentage": 30, "is_contract": True}]
        holders = _parse_holders(raw)
        assert holders[0].is_contract is True

    def test_parse_malformed_pct(self):
        raw = [{"address": "0xbad", "percentage": "N/A"}]
        holders = _parse_holders(raw)
        assert holders[0].percentage == 0.0


class TestRiskScoring:
    """Governance risk scoring tests."""

    def test_no_holders_no_gov(self):
        score, level, _flags = _score_governance_risk(0.0, 0.0, None)
        assert score >= 0  # No risk with no holders
        assert level in ("LOW", "MEDIUM")

    def test_critical_top_holder(self):
        score, level, flags = _score_governance_risk(51.0, 60.0, None)
        assert score >= 35
        assert level == "CRITICAL" or level == "HIGH"
        assert any("CRITICAL" in f for f in flags)

    def test_high_top_holder(self):
        score, level, _flags = _score_governance_risk(35.0, 55.0, None)
        assert score >= 20
        assert level == "HIGH" or level == "MEDIUM"

    def test_top_10_cartel(self):
        score, _level, flags = _score_governance_risk(10.0, 85.0, None)
        assert score >= 20
        assert any("cartel" in f.lower() for f in flags)

    def test_no_timelock_critical(self):
        params = GovernanceParams(is_governance_contract=True, has_timelock=False)
        score, _level, flags = _score_governance_risk(5.0, 10.0, params)
        assert score >= 25
        assert any("CRITICAL" in f or "No timelock" in f for f in flags)

    def test_low_quorum(self):
        params = GovernanceParams(
            is_governance_contract=True,
            has_timelock=True,
            quorum_threshold_pct=0.5,
            timelock_delay_seconds=86400,
        )
        score, _level, flags = _score_governance_risk(5.0, 10.0, params)
        assert score >= 20
        assert any("quorum" in f.lower() for f in flags)
        assert any("flash" in f.lower() for f in flags)

    def test_safe_governance(self):
        params = GovernanceParams(
            is_governance_contract=True,
            has_timelock=True,
            quorum_threshold_pct=4.0,
            timelock_delay_seconds=172800,
            voting_period_blocks=50000,
            proposal_threshold_pct=1.0,
        )
        score, level, _flags = _score_governance_risk(5.0, 20.0, params)
        assert score < 30
        assert level == "LOW" or level == "MEDIUM"

    def test_very_short_voting(self):
        params = GovernanceParams(
            is_governance_contract=True,
            has_timelock=True,
            voting_period_blocks=50,
            quorum_threshold_pct=5.0,
            timelock_delay_seconds=86400,
        )
        score, _level, flags = _score_governance_risk(5.0, 10.0, params)
        assert score >= 15
        assert any("voting period" in f.lower() for f in flags)

    def test_short_timelock(self):
        params = GovernanceParams(
            is_governance_contract=True,
            has_timelock=True,
            timelock_delay_seconds=3600,  # 1 hour
            quorum_threshold_pct=5.0,
        )
        _score, _level, flags = _score_governance_risk(5.0, 20.0, params)
        assert any("timelock" in f.lower() for f in flags)

    def test_flash_loan_flag(self):
        params = GovernanceParams(
            is_governance_contract=True,
            has_timelock=True,
            quorum_threshold_pct=0.3,
            timelock_delay_seconds=86400,
        )
        score, _level, flags = _score_governance_risk(5.0, 15.0, params)
        # Should have flash-loan governance attack flag
        assert any("flash-loan" in f.lower() for f in flags)
        assert score >= 30  # Flash-loan governance attack detected

    def test_score_capped_at_100(self):
        params = GovernanceParams(
            is_governance_contract=True,
            has_timelock=False,
            quorum_threshold_pct=0.1,
            timelock_delay_seconds=0,
            voting_period_blocks=50,
            proposal_threshold_pct=0.01,
        )
        score, level, _flags = _score_governance_risk(
            TOP_HOLDER_CRITICAL_PCT + 10, TOP_10_CRITICAL_PCT + 10, params
        )
        assert score <= 100
        assert level == "CRITICAL"


class TestGovernanceParams:
    """Governance parameters data class tests."""

    def test_defaults(self):
        p = GovernanceParams()
        assert p.has_timelock is False
        assert p.timelock_delay_seconds == 0
        assert p.quorum_threshold_pct == 0.0
        assert p.voting_period_blocks == 0

    def test_governance_contract_detected(self):
        p = GovernanceParams(
            is_governance_contract=True,
            quorum_threshold_pct=4.0,
            voting_period_blocks=10000,
        )
        assert p.is_governance_contract is True
        assert p.quorum_threshold_pct > 0
        assert p.voting_period_blocks > 0


class TestConstants:
    """Constant threshold tests."""

    def test_low_quorum_under_1_pct(self):
        assert LOW_QUORUM_PCT == 1.0

    def test_critical_holder_50_pct(self):
        assert TOP_HOLDER_CRITICAL_PCT == 50.0

    def test_top_10_critical_80_pct(self):
        assert TOP_10_CRITICAL_PCT == 80.0


class TestEndpointReferences:
    """Verify API endpoint constants are well-formed."""

    def test_dexscreener_url(self):
        assert "{}" in DEXSCREENER_API
        assert DEXSCREENER_API.startswith("https://")
