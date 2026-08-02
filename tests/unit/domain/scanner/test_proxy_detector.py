"""Unit tests for app/domains/scanners/proxy_detector.py.

Tests EIP-1967 / beacon / old-style / function-call (UUPS-style) proxy
detection, non-proxy contract handling, rug-pattern bytecode analysis,
risk calculation, and the full analyze() pipeline with mocked RPC calls.
No network access — all external calls are mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.domains.scanners.proxy_detector import (
    EIP1967_ADMIN_SLOT,
    EIP1967_BEACON_SLOT,
    EIP1967_IMPLEMENTATION_SLOT,
    KNOWN_RUG_PATTERNS,
    ProxyDetector,
    ProxyReport,
)

# 32-byte storage slot values (64 hex chars after 0x)
IMPL_SLOT = "0x" + "00" * 12 + "1111111111111111111111111111111111111111"
ADMIN_SLOT = "0x" + "00" * 12 + "2222222222222222222222222222222222222222"
BEACON_SLOT = "0x" + "00" * 12 + "3333333333333333333333333333333333333333"
IMPL_FROM_BEACON = "0x" + "00" * 12 + "4444444444444444444444444444444444444444"

IMPL_ADDR = "0x" + "11" * 20
ADMIN_ADDR = "0x" + "22" * 20
BEACON_ADDR = "0x" + "33" * 20
IMPL_ADDR_B = "0x" + "44" * 20


class TestCheckEvmProxy:
    """Proxy detection strategies for EVM contracts."""

    @pytest.mark.asyncio
    async def test_eip1967_proxy_detection(self) -> None:
        """A non-zero EIP-1967 implementation slot should resolve the proxy."""
        detector = ProxyDetector()

        async def fake_eth_call(to: str, data: str, chain: str) -> str | None:
            if data == EIP1967_IMPLEMENTATION_SLOT:
                return IMPL_SLOT
            if data == EIP1967_ADMIN_SLOT:
                return ADMIN_SLOT
            return None

        with patch.object(detector, "_eth_call", AsyncMock(side_effect=fake_eth_call)):
            is_proxy, proxy_type, impl, admin = await detector._check_evm_proxy("0xProxy", "ethereum")
        assert is_proxy is True
        assert proxy_type == "eip1967"
        assert impl == IMPL_ADDR
        assert admin == ADMIN_ADDR

    @pytest.mark.asyncio
    async def test_beacon_proxy_detection(self) -> None:
        """Beacon proxies should resolve the implementation from the beacon."""
        detector = ProxyDetector()

        async def fake_eth_call(to: str, data: str, chain: str) -> str | None:
            if data == EIP1967_BEACON_SLOT:
                return BEACON_SLOT
            if data == "0x5c60da1b":  # implementation()
                return IMPL_FROM_BEACON
            return None

        with patch.object(detector, "_eth_call", AsyncMock(side_effect=fake_eth_call)):
            is_proxy, proxy_type, impl, admin = await detector._check_evm_proxy("0xBeaconProxy", "ethereum")
        assert is_proxy is True
        assert proxy_type == "beacon"
        assert impl == IMPL_ADDR_B
        assert admin == BEACON_ADDR

    @pytest.mark.asyncio
    async def test_old_style_proxy_detection(self) -> None:
        """Old-style (non-EIP-1967) proxies should be detected via slot 0x5f3b5cf7."""
        detector = ProxyDetector()

        async def fake_eth_call(to: str, data: str, chain: str) -> str | None:
            if data.startswith("0x5f3b5cf7"):
                return IMPL_SLOT
            return None

        with patch.object(detector, "_eth_call", AsyncMock(side_effect=fake_eth_call)):
            is_proxy, proxy_type, impl, admin = await detector._check_evm_proxy("0xOldProxy", "ethereum")
        assert is_proxy is True
        assert proxy_type == "old_style"
        assert impl == IMPL_ADDR
        assert admin == ""

    @pytest.mark.asyncio
    async def test_uups_function_call_detection(self) -> None:
        """UUPS-style proxies exposing implementation() should be detected."""
        detector = ProxyDetector()

        async def fake_eth_call(to: str, data: str, chain: str) -> str | None:
            if data == "0x5c60da1b":  # implementation()
                return IMPL_SLOT
            return None

        with patch.object(detector, "_eth_call", AsyncMock(side_effect=fake_eth_call)):
            is_proxy, proxy_type, impl, _admin = await detector._check_evm_proxy("0xUups", "ethereum")
        assert is_proxy is True
        assert proxy_type == "function_call"
        assert impl == IMPL_ADDR

    @pytest.mark.asyncio
    async def test_non_proxy_contract(self) -> None:
        """A plain contract with no proxy slots should not be flagged."""
        detector = ProxyDetector()
        with patch.object(detector, "_eth_call", AsyncMock(return_value=None)):
            is_proxy, proxy_type, impl, admin = await detector._check_evm_proxy("0xPlain", "ethereum")
        assert is_proxy is False
        assert proxy_type == ""
        assert impl == ""
        assert admin == ""

    @pytest.mark.asyncio
    async def test_zero_impl_slot_is_not_proxy(self) -> None:
        """A zeroed implementation slot (address 0x0) should not count as proxy."""
        detector = ProxyDetector()
        zero_slot = "0x" + "00" * 32
        with patch.object(detector, "_eth_call", AsyncMock(return_value=zero_slot)):
            is_proxy, _, _, _ = await detector._check_evm_proxy("0xZero", "ethereum")
        assert is_proxy is False


class TestRugPatternAnalysis:
    """Implementation bytecode analysis for rug-pattern selectors."""

    def test_detects_known_rug_patterns(self) -> None:
        """Bytecode containing 3+ rug selectors should be flagged as a rug."""
        detector = ProxyDetector()
        rug_selectors = [p[2:] for p in KNOWN_RUG_PATTERNS[:3]]
        bytecode = "0x6080" + "".join(rug_selectors)
        is_rug, patterns, code_hash = detector._check_impl_for_rug_patterns("0xImpl", bytecode)
        assert is_rug is True
        assert len(patterns) >= 3
        assert len(code_hash) == 16

    def test_safe_bytecode_no_rug_patterns(self) -> None:
        """Bytecode without rug selectors should not be flagged."""
        detector = ProxyDetector()
        is_rug, patterns, code_hash = detector._check_impl_for_rug_patterns(
            "0xImpl", "0x6080604052604051806101e001610100038239"
        )
        assert is_rug is False
        assert patterns == []
        assert len(code_hash) == 16

    def test_empty_bytecode_not_rug(self) -> None:
        """Empty bytecode should return no rug verdict and no hash."""
        detector = ProxyDetector()
        assert detector._check_impl_for_rug_patterns("0xImpl", "0x") == (False, [], "")
        assert detector._check_impl_for_rug_patterns("0xImpl", "") == (False, [], "")

    def test_fewer_than_three_patterns_not_rug(self) -> None:
        """1-2 rug selectors should be flagged as patterns but not a rug."""
        detector = ProxyDetector()
        one_selector = "0x6080" + KNOWN_RUG_PATTERNS[0][2:]
        is_rug, patterns, _ = detector._check_impl_for_rug_patterns("0xImpl", one_selector)
        assert is_rug is False
        assert len(patterns) == 1


class TestCalculateRisk:
    """Risk scoring for proxy reports."""

    def test_upgradeable_no_timelock_is_high_risk(self) -> None:
        """Upgradeable proxy with no timelock should score HIGH or CRITICAL."""
        detector = ProxyDetector()
        report = ProxyReport(
            token_address="0xProxy",
            chain="ethereum",
            is_proxy=True,
            proxy_type="eip1967",
            implementation_address=IMPL_ADDR,
            admin_address=ADMIN_ADDR,
            can_upgrade=True,
            upgrade_timelock=0,
        )
        score, level, warnings = detector._calculate_risk(report)
        assert score >= 40
        assert level in ("HIGH", "CRITICAL")
        assert any("NO timelock" in w for w in warnings)

    def test_timelocked_upgrade_is_moderate(self) -> None:
        """Upgradeable proxy with a 2-day timelock should be MEDIUM."""
        detector = ProxyDetector()
        report = ProxyReport(
            token_address="0xProxy",
            chain="ethereum",
            is_proxy=True,
            can_upgrade=True,
            upgrade_timelock=2 * 86400,
        )
        score, level, warnings = detector._calculate_risk(report)
        assert score < 40
        assert level in ("LOW", "MEDIUM")
        assert any("timelock" in w.lower() for w in warnings)

    def test_non_proxy_is_low_risk(self) -> None:
        """Non-proxy contracts should score 0 / LOW with no warnings."""
        detector = ProxyDetector()
        report = ProxyReport(token_address="0xPlain", chain="ethereum", is_proxy=False)
        score, level, warnings = detector._calculate_risk(report)
        assert score == 0
        assert level == "LOW"
        assert warnings == []

    def test_known_rug_impl_adds_risk(self) -> None:
        """Known rug implementations should push the score toward CRITICAL."""
        detector = ProxyDetector()
        report = ProxyReport(
            token_address="0xProxy",
            chain="ethereum",
            is_proxy=True,
            is_known_rug_impl=True,
            impl_risk_patterns=KNOWN_RUG_PATTERNS[:3],
            can_upgrade=True,
            upgrade_timelock=0,
        )
        score, level, _ = detector._calculate_risk(report)
        assert score >= 70
        assert level == "CRITICAL"


class TestAnalyze:
    """Full analyze() pipeline with mocked data sources."""

    @pytest.mark.asyncio
    async def test_analyze_evm_proxy_full_flow(self) -> None:
        """EVM proxy analysis should populate the full report."""
        detector = ProxyDetector()
        with (
            patch.object(
                detector,
                "_check_evm_proxy",
                AsyncMock(return_value=(True, "eip1967", IMPL_ADDR, ADMIN_ADDR)),
            ),
            patch.object(
                detector,
                "_check_can_upgrade",
                AsyncMock(return_value=(True, "0x" + "99" * 20, 0)),
            ),
            patch.object(detector, "_eth_getCode", AsyncMock(return_value="0x6080604052")),
            patch.object(detector, "_fetch_contract_source", AsyncMock(return_value=None)),
            patch("app.domains.scanners.proxy_detector.query_rag_citations", AsyncMock(return_value=[])),
        ):
            report = await detector.analyze("0xProxy", "ethereum")
        assert report.is_proxy is True
        assert report.proxy_type == "eip1967"
        assert report.implementation_address == IMPL_ADDR
        assert report.admin_address == ADMIN_ADDR
        assert report.can_upgrade is True
        assert report.has_timelock is False
        assert report.risk_score >= 40
        assert report.risk_level in ("HIGH", "CRITICAL")

    @pytest.mark.asyncio
    async def test_analyze_evm_non_proxy(self) -> None:
        """Non-proxy EVM contracts should yield a LOW risk report."""
        detector = ProxyDetector()
        with (
            patch.object(
                detector,
                "_check_evm_proxy",
                AsyncMock(return_value=(False, "", "", "")),
            ),
            patch("app.domains.scanners.proxy_detector.query_rag_citations", AsyncMock(return_value=[])),
        ):
            report = await detector.analyze("0xPlain", "ethereum")
        assert report.is_proxy is False
        assert report.risk_score == 0
        assert report.risk_level == "LOW"

    @pytest.mark.asyncio
    async def test_analyze_solana_upgradeable(self) -> None:
        """Solana programs with an upgrade authority should be flagged upgradeable."""
        detector = ProxyDetector()
        program_data = {
            "owner": "BPFLoaderUpgradeab1e11111111111111111111111",
            "data": [{"parsed": {"type": "account"}}],
        }
        with (
            patch.object(detector, "_fetch_solana_program_data", AsyncMock(return_value=program_data)),
            patch.object(
                detector,
                "_fetch_solana_upgrade_authority",
                AsyncMock(return_value="SomeUpgradeAuth111111111111111111111111"),
            ),
            patch("app.domains.scanners.proxy_detector.query_rag_citations", AsyncMock(return_value=[])),
        ):
            report = await detector.analyze("SolMint1111111111111111111111111111111", "solana")
        assert report.is_proxy is True
        assert report.proxy_type == "solana_upgradeable"
        assert report.can_upgrade is True
        assert report.implementation_address == "BPFLoaderUpgradeab1e11111111111111111111111"

    @pytest.mark.asyncio
    async def test_analyze_solana_immutable_program(self) -> None:
        """Solana programs without an upgrade authority are immutable, not proxies."""
        detector = ProxyDetector()
        program_data = {
            "owner": "BPFLoaderUpgradeab1e11111111111111111111111",
            "data": [{"parsed": {"type": "account"}}],
        }
        with (
            patch.object(detector, "_fetch_solana_program_data", AsyncMock(return_value=program_data)),
            patch.object(detector, "_fetch_solana_upgrade_authority", AsyncMock(return_value=None)),
            patch("app.domains.scanners.proxy_detector.query_rag_citations", AsyncMock(return_value=[])),
        ):
            report = await detector.analyze("SolMint1111111111111111111111111111111", "solana")
        assert report.is_proxy is False
        assert report.can_upgrade is False


if __name__ == "__main__":
    pytest.main([__file__])
