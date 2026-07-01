"""
Tests for smart_contract_honeypot_detector.py
"""

import json
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.smart_contract_honeypot_detector import (
    HONEYPOT_THRESHOLDS,
    FindingSeverity,
    HoneypotAnalysisResult,
    HoneypotDetector,
    HoneypotType,
    SecurityFinding,
    TokenInfo,
)


def test_security_finding_creation() -> None:
    """Test SecurityFinding dataclass creation and serialization."""
    finding = SecurityFinding(
        finding_type=HoneypotType.SELL_RESTRICTION,
        severity=FindingSeverity.CRITICAL,
        description="Cannot sell tokens",
        detail="0 sells vs 100 buys in 24h",
        code_reference="dexscreener_tx_analysis",
    )
    d = finding.to_dict()
    assert d["type"] == "sell_restriction"
    assert d["severity"] == "critical"
    assert d["description"] == "Cannot sell tokens"
    assert d["detail"] == "0 sells vs 100 buys in 24h"


def test_security_finding_defaults() -> None:
    """Test SecurityFinding with default values."""
    finding = SecurityFinding(
        finding_type=HoneypotType.HIGH_SELL_TAX,
        severity=FindingSeverity.HIGH,
        description="High sell tax",
    )
    assert finding.detail == ""
    assert finding.code_reference == ""


def test_finding_severity_enum() -> None:
    """Test FindingSeverity enum values."""
    assert FindingSeverity.CRITICAL.value == "critical"
    assert FindingSeverity.HIGH.value == "high"
    assert FindingSeverity.MEDIUM.value == "medium"
    assert FindingSeverity.LOW.value == "low"
    assert FindingSeverity.INFO.value == "info"


def test_honeypot_type_enum() -> None:
    """Test HoneypotType enum values."""
    assert HoneypotType.SELL_RESTRICTION.value == "sell_restriction"
    assert HoneypotType.BLACKLIST.value == "blacklist"
    assert HoneypotType.PROXY_UPGRADE.value == "proxy_upgrade"
    assert HoneypotType.SELFDESTRUCT.value == "selfdestruct"
    assert HoneypotType.LIQUIDITY_DRAIN.value == "liquidity_drain"
    assert HoneypotType.OWNER_MINT.value == "owner_mint"


def test_token_info_creation() -> None:
    """Test TokenInfo creation and serialization."""
    info = TokenInfo(
        address="0x1234567890abcdef1234567890abcdef12345678",
        chain="ethereum",
        name="Test Token",
        symbol="TEST",
        decimals=18,
        price_usd=0.01,
        liquidity_usd=50000,
        chain_data_sources=["dexscreener", "explorer:ethereum"],
    )
    d = info.to_dict()
    assert d["address"] == "0x1234567890abcdef1234567890abcdef12345678"
    assert d["chain"] == "ethereum"
    assert d["name"] == "Test Token"
    assert d["symbol"] == "TEST"
    assert d["price_usd"] == 0.01
    assert d["liquidity_usd"] == 50000
    assert len(d["chain_data_sources"]) == 2


def test_token_info_defaults() -> None:
    """Test TokenInfo default values."""
    info = TokenInfo(address="0x0", chain="bsc")
    assert info.name == ""
    assert info.symbol == ""
    assert info.decimals == 18
    assert info.price_usd == 0.0
    assert info.is_verified is False
    assert info.ownership_renounced is False
    assert info.is_proxy is False
    assert info.chain_data_sources == []


def test_analysis_result_empty() -> None:
    """Test HoneypotAnalysisResult with no findings."""
    token = TokenInfo(
        address="0xabc",
        chain="ethereum",
        name="Safe Token",
        symbol="SAFE",
    )
    result = HoneypotAnalysisResult(
        token=token,
        risk_score=0,
        risk_label="safe",
    )
    assert not result.has_critical_findings()
    assert not result.has_high_findings()
    assert result.findings_by_severity(FindingSeverity.CRITICAL) == []
    assert result.findings_by_severity(FindingSeverity.MEDIUM) == []
    assert result.risk_score == 0
    assert result.risk_label == "safe"


def test_analysis_result_with_findings() -> None:
    """Test HoneypotAnalysisResult with findings."""
    token = TokenInfo(address="0xdef", chain="bsc")
    result = HoneypotAnalysisResult(
        token=token,
        risk_score=65,
        risk_label="high",
        findings=[
            SecurityFinding(
                finding_type=HoneypotType.SELL_RESTRICTION,
                severity=FindingSeverity.CRITICAL,
                description="Cannot sell",
            ),
            SecurityFinding(
                finding_type=HoneypotType.OWNER_MINT,
                severity=FindingSeverity.HIGH,
                description="Owner can mint",
            ),
            SecurityFinding(
                finding_type=HoneypotType.COOLDOWN,
                severity=FindingSeverity.MEDIUM,
                description="Trading cooldown",
            ),
        ],
    )
    assert result.has_critical_findings()
    assert result.has_high_findings()
    assert len(result.findings_by_severity(FindingSeverity.CRITICAL)) == 1
    assert len(result.findings_by_severity(FindingSeverity.HIGH)) == 1
    assert len(result.findings_by_severity(FindingSeverity.MEDIUM)) == 1
    assert len(result.findings_by_severity(FindingSeverity.LOW)) == 0


def test_analysis_result_summary() -> None:
    """Test result summary format."""
    token = TokenInfo(
        address="0xdead00000000000000000000000000000000beef",
        chain="polygon",
        symbol="SCAM",
    )
    result = HoneypotAnalysisResult(
        token=token,
        risk_score=80,
        risk_label="critical",
        findings=[
            SecurityFinding(
                finding_type=HoneypotType.SELL_RESTRICTION,
                severity=FindingSeverity.CRITICAL,
                description="Cannot sell",
            ),
        ],
        buy_tax_pct=5.0,
        sell_tax_pct=50.0,
        liquidity_locked_pct=0.0,
    )
    summary = result.summary()
    assert "CRITICAL" in summary
    assert "SCAM" in summary
    assert "80/100" in summary
    assert "Buy tax: 5.0%" in summary
    assert "Sell tax: 50.0%" in summary
    assert "LP locked: 0%" in summary


def test_analysis_result_report_text() -> None:
    """Test text report format."""
    token = TokenInfo(
        address="0xabc123",
        chain="ethereum",
        symbol="TEST",
        price_usd=0.05,
        liquidity_usd=10000,
    )
    result = HoneypotAnalysisResult(
        token=token,
        risk_score=25,
        risk_label="medium",
        findings=[
            SecurityFinding(
                finding_type=HoneypotType.COOLDOWN,
                severity=FindingSeverity.MEDIUM,
                description="Trading cooldown enabled",
            ),
        ],
        buy_tax_pct=3.0,
        sell_tax_pct=12.0,
    )
    report = result.report(fmt="text")
    assert "HONEYPOT ANALYSIS REPORT" in report
    assert "TEST" in report
    assert "medium" in report
    assert "Buy tax:" in report
    assert "Sell tax:" in report
    assert "Trading cooldown" in report


def test_analysis_result_report_json() -> None:
    """Test JSON report format."""
    token = TokenInfo(address="0x1111", chain="base", symbol="TEST")
    result = HoneypotAnalysisResult(
        token=token,
        risk_score=50,
        risk_label="high",
        findings=[
            SecurityFinding(
                finding_type=HoneypotType.BLACKLIST,
                severity=FindingSeverity.HIGH,
                description="Blacklist function",
            ),
        ],
    )
    report = result.report(fmt="json")
    parsed = json.loads(report)
    assert parsed["risk_score"] == 50
    assert parsed["risk_label"] == "high"
    assert parsed["token"]["address"] == "0x1111"
    assert len(parsed["findings"]) == 1
    assert parsed["findings"][0]["type"] == "blacklist"


def test_analysis_result_to_dict() -> None:
    """Test result dict serialization includes all fields."""
    token = TokenInfo(address="0xaaaa", chain="arbitrum")
    result = HoneypotAnalysisResult(
        token=token,
        risk_score=100,
        risk_label="critical",
        warnings=["Test warning"],
        buy_tax_pct=10.0,
        sell_tax_pct=30.0,
        liquidity_locked_pct=0.0,
    )
    d = result.to_dict()
    assert d["risk_score"] == 100
    assert d["risk_label"] == "critical"
    assert "Test warning" in d["warnings"]
    assert d["buy_tax_pct"] == 10.0
    assert d["sell_tax_pct"] == 30.0
    assert d["liquidity_locked_pct"] == 0.0
    assert "scan_timestamp" in d


def test_invalid_address() -> None:
    """Test detector handles invalid addresses gracefully."""
    import asyncio

    detector = HoneypotDetector()

    async def _test() -> None:
        result = await detector.analyze_token("not-an-address", chain="ethereum")
        assert result.risk_label == "invalid"
        assert "Invalid EVM address" in result.warnings[0]

    asyncio.run(_test())


def test_risk_score_minimum() -> None:
    """Test risk score has minimum floor."""
    detector = HoneypotDetector()
    # If score < 15 was set by the compute function, it gets bumped
    assert detector._risk_score_to_label(3) == "safe"  # Below floor of 5
    assert detector._risk_score_to_label(15) == "low"
    assert detector._risk_score_to_label(25) == "medium"
    assert detector._risk_score_to_label(45) == "high"
    assert detector._risk_score_to_label(75) == "critical"


def test_risk_score_thresholds() -> None:
    """Test risk score to label mapping."""
    detector = HoneypotDetector()
    assert detector._risk_score_to_label(0) == "safe"
    assert detector._risk_score_to_label(4) == "safe"
    assert detector._risk_score_to_label(5) == "low"
    assert detector._risk_score_to_label(19) == "low"
    assert detector._risk_score_to_label(20) == "medium"
    assert detector._risk_score_to_label(39) == "medium"
    assert detector._risk_score_to_label(40) == "high"
    assert detector._risk_score_to_label(69) == "high"
    assert detector._risk_score_to_label(70) == "critical"
    assert detector._risk_score_to_label(100) == "critical"


def test_known_malicious_selectors() -> None:
    """Test that known malicious selectors are properly defined."""
    from app.smart_contract_honeypot_detector import KNOWN_MALICIOUS_SELECTORS

    assert "0x40c10f19" in KNOWN_MALICIOUS_SELECTORS  # mint(address,uint256)
    assert "0x24b6d0c4" in KNOWN_MALICIOUS_SELECTORS  # blacklist
    assert "0x41c0e1b5" in KNOWN_MALICIOUS_SELECTORS  # selfdestruct
    assert "0x3659cfe6" in KNOWN_MALICIOUS_SELECTORS  # upgradeTo
    assert "0x8456cb59" in KNOWN_MALICIOUS_SELECTORS  # pause
    assert "0x2e1a7d4d" in KNOWN_MALICIOUS_SELECTORS  # withdraw

    # Verify each has proper tuple structure
    for selector, (sig, htype, desc) in KNOWN_MALICIOUS_SELECTORS.items():
        assert selector.startswith("0x")
        assert len(selector) == 10  # 0x + 8 hex chars
        assert isinstance(sig, str)
        assert isinstance(htype, HoneypotType)
        assert isinstance(desc, str)


def test_benign_selectors() -> None:
    """Test benign selectors don't conflict with malicious ones."""
    from app.smart_contract_honeypot_detector import (
        BENIGN_SELECTORS,
        KNOWN_MALICIOUS_SELECTORS,
    )

    # Benign and malicious should not overlap
    overlap = set(BENIGN_SELECTORS) & set(KNOWN_MALICIOUS_SELECTORS.keys())
    assert len(overlap) == 0, f"Overlap found: {overlap}"


def test_honeypot_thresholds() -> None:
    """Test honeypot threshold values are reasonable."""
    assert HONEYPOT_THRESHOLDS["critical_sell_tax"] >= 20
    assert HONEYPOT_THRESHOLDS["high_sell_tax"] >= 10
    assert HONEYPOT_THRESHOLDS["critical_buy_tax"] >= 15
    assert HONEYPOT_THRESHOLDS["high_buy_tax"] >= 8
    assert HONEYPOT_THRESHOLDS["low_liquidity_threshold"] > 0
    assert HONEYPOT_THRESHOLDS["tiny_liquidity_threshold"] > 0


def test_evm_chains() -> None:
    """Test EVM chain configuration is complete."""
    from app.smart_contract_honeypot_detector import EVM_CHAINS

    required_chains = {"ethereum", "bsc", "polygon", "arbitrum", "base"}
    for chain in required_chains:
        assert chain in EVM_CHAINS, f"Missing chain: {chain}"
        assert "rpc" in EVM_CHAINS[chain]
        assert "chain_id" in EVM_CHAINS[chain]
        assert "explorer" in EVM_CHAINS[chain]
        assert EVM_CHAINS[chain]["chain_id"] > 0


def test_honeypot_detector_init() -> None:
    """Test HoneypotDetector initialization."""
    detector = HoneypotDetector()
    assert detector.enable_deep_scan is False
    assert detector.api_keys == {}
    assert "ethereum" in detector._explorer_keys

    detector2 = HoneypotDetector(enable_deep_scan=True)
    assert detector2.enable_deep_scan is True


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
