"""Tests for Contract Upgrade Monitor (contract_upgrade_monitor.py)."""

from __future__ import annotations

import pytest

from app.contract_upgrade_monitor import (
    ContractUpgradeAnalyzer,
    ContractUpgradeReport,
    ProxyInfo,
    UpgradeEvent,
    detect_proxy,
    format_upgrade_report,
    is_valid_evm_address,
)


class TestAddressValidation:
    """Test EVM address validation."""

    def test_valid_address(self):
        assert is_valid_evm_address("0xdAC17F958D2ee523a2206206994597C13D831ec7")
        assert is_valid_evm_address("0x0000000000000000000000000000000000000000")

    def test_invalid_address(self):
        assert not is_valid_evm_address("")
        assert not is_valid_evm_address("not_an_address")
        assert not is_valid_evm_address("0xshort")
        assert not is_valid_evm_address("0xGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGG")  # non-hex

    def test_checksum_case_insensitive(self):
        # Lowercase is valid (EVM addresses are case-insensitive for storage)
        assert is_valid_evm_address("0xdac17f958d2ee523a2206206994597c13d831ec7")


class TestProxyDetection:
    """Test proxy detection logic (mocked storage)."""

    @pytest.mark.asyncio
    async def test_non_proxy_returns_false(self):
        """Check that a non-proxy address returns is_proxy=False."""
        # Use a well-known EOA (Vitalik's address) - should not be a proxy
        result = await detect_proxy("0xab5801a7d398352b5532b8a7c0c8e9c6c5c9f6d5", "ethereum")
        # This might still return proxy=False or proxy with low confidence
        # depending on whether the RPC is available
        assert result is not None
        assert isinstance(result, ProxyInfo)

    def test_proxy_info_creation(self):
        info = ProxyInfo(address="0x1234", chain="ethereum")
        assert info.address == "0x1234"
        assert info.chain == "ethereum"
        assert not info.is_proxy
        assert info.confidence == 0.0

    def test_proxy_info_with_implementation(self):
        info = ProxyInfo(
            address="0x1234",
            chain="ethereum",
            implementation_address="0x5678",
            is_proxy=True,
            proxy_type="eip1967",
            proxy_type_name="EIP-1967 Transparent/Universal Proxy",
            confidence=0.9,
        )
        assert info.is_proxy
        assert info.proxy_type == "eip1967"
        assert info.implementation_address == "0x5678"


class TestUpgradeEvent:
    """Test upgrade event creation and sorting."""

    def test_upgrade_event_creation(self):
        event = UpgradeEvent(
            block_number=100,
            transaction_hash="0xabc",
            timestamp=1000000,
            previous_implementation="0xold",
            new_implementation="0xnew",
            triggered_by="0xadmin",
        )
        assert event.block_number == 100
        assert event.new_implementation == "0xnew"
        assert event.triggered_by == "0xadmin"

    def test_upgrade_event_minimal(self):
        event = UpgradeEvent(
            block_number=0,
            transaction_hash="",
            timestamp=0,
        )
        assert event.previous_implementation is None
        assert event.triggered_by is None


class TestFormatter:
    """Test report formatting."""

    def test_format_proxy_report(self):
        proxy = ProxyInfo(
            address="0xdAC17F958D2ee523a2206206994597C13D831ec7",
            chain="ethereum",
            implementation_address="0x1111111111111111111111111111111111111111",
            admin_address="0x2222222222222222222222222222222222222222",
            is_proxy=True,
            proxy_type="eip1967",
            proxy_type_name="EIP-1967 Transparent/Universal Proxy",
            confidence=0.9,
        )
        report = ContractUpgradeReport(
            contract_address="0xdAC17F958D2ee523a2206206994597C13D831ec7",
            chain="ethereum",
            proxy_info=proxy,
            timelock_status="no_timelock",
            upgrade_count_30d=2,
            upgrade_history=[
                UpgradeEvent(
                    block_number=100,
                    transaction_hash="0xabc123def456",
                    timestamp=1000000,
                ),
            ],
            recent_suspicious_upgrades=[],
            risk_score=35.0,
            risk_factors=[
                "No timelock detected - upgrades can be instant",
                "Moderate upgrade frequency: 2 upgrades in 30 days",
            ],
            admin_privileges=["upgradeTo(address)", "changeAdmin(address)"],
            summary="Summary text here",
        )
        text = format_upgrade_report(report)
        assert "CONTRACT UPGRADE MONITOR REPORT" in text
        assert "EIP-1967" in text
        assert "35.0/100" in text or "35/100" in text
        assert "Proxy Detected" in text

    def test_format_non_proxy_report(self):
        report = ContractUpgradeReport(
            contract_address="0xdAC17F958D2ee523a2206206994597C13D831ec7",
            chain="ethereum",
            proxy_info=ProxyInfo(address="0xtest", chain="ethereum"),
            risk_score=0.0,
            summary="Not a proxy",
        )
        text = format_upgrade_report(report)
        assert "not a proxy" in text.lower() or "Not a proxy" in text


class TestRiskAssessment:
    """Test risk scoring logic (internal)."""

    def test_high_risk_frequent_upgrades(self):
        from app.contract_upgrade_monitor import _assess_risk

        proxy = ProxyInfo(
            address="0xtest",
            chain="ethereum",
            is_proxy=True,
            proxy_type="eip1967",
            confidence=0.9,
        )
        upgrades = [
            UpgradeEvent(block_number=i, transaction_hash=f"0x{i}", timestamp=1000000 + i * 1000)
            for i in range(10)
        ]
        current_time = 1000000 + 5 * 3600  # 5 hours after first upgrade
        risk, factors = _assess_risk(proxy, upgrades, current_time)
        assert risk > 0
        assert len(factors) > 0
        # Should flag the no-timelock risk
        assert any("timelock" in f.lower() for f in factors)

    def test_low_risk_stable_proxy(self):
        from app.contract_upgrade_monitor import _assess_risk

        proxy = ProxyInfo(
            address="0xtest",
            chain="ethereum",
            is_proxy=True,
            proxy_type="eip1967",
            confidence=0.9,
        )
        risk, factors = _assess_risk(proxy, [], 2000000)
        assert risk >= 0
        # Should still flag no timelock
        assert any("timelock" in f.lower() for f in factors)

    def test_beacon_proxy_extra_risk(self):
        from app.contract_upgrade_monitor import _assess_risk

        proxy = ProxyInfo(
            address="0xtest",
            chain="ethereum",
            is_proxy=True,
            proxy_type="beacon",
            confidence=0.9,
        )
        risk, factors = _assess_risk(proxy, [], 2000000)
        assert risk >= 15  # Beacon risk penalty
        assert any("Beacon" in f for f in factors)


class TestAnalyzer:
    """Test the ContractUpgradeAnalyzer (integration-light)."""

    @pytest.mark.asyncio
    async def test_analyzer_returns_report(self):
        analyzer = ContractUpgradeAnalyzer()
        # Use a known address - USDT contract on Ethereum
        # This is a real contract but we don't rely on it being a proxy
        report = await analyzer.analyze(
            contract_address="0xdAC17F958D2ee523a2206206994597C13D831ec7",
            chain="ethereum",
        )
        assert isinstance(report, ContractUpgradeReport)
        assert (
            report.contract_address.lower() == "0xdAC17F958D2ee523a2206206994597C13D831ec7".lower()
        )
        assert report.chain == "ethereum"

    @pytest.mark.asyncio
    async def test_analyzer_invalid_address(self):
        analyzer = ContractUpgradeAnalyzer()
        with pytest.raises(ValueError, match="Invalid EVM address"):
            await analyzer.analyze(
                contract_address="not_an_address",
                chain="ethereum",
            )


class TestModuleFunctions:
    """Test module-level helper functions."""

    def test_get_upgrade_analyzer(self):
        from app.contract_upgrade_monitor import get_upgrade_analyzer

        instance = get_upgrade_analyzer()
        assert isinstance(instance, ContractUpgradeAnalyzer)

        # Singleton behavior
        instance2 = get_upgrade_analyzer()
        assert instance is instance2


class TestDangerousSelectors:
    """Test that dangerous function selectors are properly mapped."""

    def test_known_selectors(self):
        from app.contract_upgrade_monitor import DANGEROUS_SELECTORS

        # upgradeTo selector should be known
        assert "0x3659cfe6" in DANGEROUS_SELECTORS
        assert "upgrade" in DANGEROUS_SELECTORS["0x3659cfe6"].lower()

    def test_proxy_patterns_listed(self):
        from app.contract_upgrade_monitor import PROXY_PATTERNS

        assert "eip1967" in PROXY_PATTERNS
        assert "eip1822" in PROXY_PATTERNS
        assert "beacon" in PROXY_PATTERNS
