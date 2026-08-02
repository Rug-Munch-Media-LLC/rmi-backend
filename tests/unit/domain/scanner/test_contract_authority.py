"""Unit tests for app/domains/scanners/contract_authority.py.

Tests Solana mint/freeze/update authority detection (Helius RPC mocked),
raw SPL mint parsing, self-referential authority flags, and EVM proxy
upgradeability detection (Etherscan API mocked). All external calls are
mocked — no network access.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.domains.scanners.contract_authority import (
    AuthorityRiskLevel,
    ContractAuthorityReport,
    ContractAuthorityScanner,
    EVMProxyInfo,
)

SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
SOLANA_ZERO = "11111111111111111111111111111111"

# 32-byte EIP-1967 implementation slot value (64 hex chars after 0x)
IMPL_SLOT_VALUE = "0x" + "00" * 12 + "AaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAa"
ADMIN_SLOT_VALUE = "0x" + "00" * 12 + "BbBbBbBbBbBbBbBbBbBbBbBbBbBbBbBbBbBbBbBb"
IMPL_ADDRESS = "0x" + "aa" * 20
ADMIN_ADDRESS = "0x" + "bb" * 20


def _solana_rpc_response(info: dict) -> dict:
    """Build a getAccountInfo-style jsonParsed response for an SPL mint."""
    return {
        "result": {
            "value": {
                "owner": SPL_TOKEN_PROGRAM,
                "data": {"parsed": {"info": info}},
            }
        }
    }


class TestSolanaScan:
    """Solana authority detection via mocked Helius RPC."""

    @pytest.mark.asyncio
    async def test_scan_detects_active_mint_authority(self) -> None:
        """An unrenounced mint authority should be flagged with risk."""
        scanner = ContractAuthorityScanner()
        rpc = _solana_rpc_response(
            {
                "mintAuthority": "DevWallet11111111111111111111111111111111",
                "freezeAuthority": SOLANA_ZERO,
            }
        )
        with patch.object(scanner._chain, "rpc_call", AsyncMock(return_value=rpc)):
            report = await scanner.scan("TokenMint11111111111111111111111111111", "solana")
        assert report.solana_authorities is not None
        mint = report.solana_authorities.mint_authority
        assert mint is not None
        assert mint.is_renounced is False
        assert any("Mint authority not renounced" in w for w in report.warnings)
        assert report.risk_score >= 30

    @pytest.mark.asyncio
    async def test_scan_detects_active_freeze_authority(self) -> None:
        """An unrenounced freeze authority should be flagged as risk."""
        scanner = ContractAuthorityScanner()
        rpc = _solana_rpc_response(
            {
                "mintAuthority": SOLANA_ZERO,
                "freezeAuthority": "Freezer111111111111111111111111111111111",
            }
        )
        with patch.object(scanner._chain, "rpc_call", AsyncMock(return_value=rpc)):
            report = await scanner.scan("TokenMint11111111111111111111111111111", "solana")
        freeze = report.solana_authorities.freeze_authority
        assert freeze is not None
        assert freeze.is_renounced is False
        assert any("Freeze authority not renounced" in w for w in report.warnings)

    @pytest.mark.asyncio
    async def test_scan_renounced_authorities_are_low_risk(self) -> None:
        """Renounced mint + freeze authorities should produce no warnings."""
        scanner = ContractAuthorityScanner()
        rpc = _solana_rpc_response({"mintAuthority": SOLANA_ZERO, "freezeAuthority": SOLANA_ZERO})
        with patch.object(scanner._chain, "rpc_call", AsyncMock(return_value=rpc)):
            report = await scanner.scan("TokenMint11111111111111111111111111111", "solana")
        assert report.solana_authorities.mint_authority.is_renounced is True
        assert report.solana_authorities.freeze_authority.is_renounced is True
        assert report.warnings == []
        assert report.risk_score == 0
        assert report.risk_level == AuthorityRiskLevel.LOW.value

    @pytest.mark.asyncio
    async def test_scan_self_referential_mint_authority(self) -> None:
        """Mint authority set to the token itself is critical."""
        scanner = ContractAuthorityScanner()
        token = "SelfRefMint111111111111111111111111111"
        rpc = _solana_rpc_response({"mintAuthority": token, "freezeAuthority": SOLANA_ZERO})
        with patch.object(scanner._chain, "rpc_call", AsyncMock(return_value=rpc)):
            report = await scanner.scan(token, "solana")
        mint = report.solana_authorities.mint_authority
        assert mint.is_token_itself is True
        assert any("self-referential" in w.lower() for w in report.warnings)
        assert report.risk_score >= 45

    @pytest.mark.asyncio
    async def test_scan_token_account_not_found(self) -> None:
        """Missing token accounts should yield a warning, not an exception."""
        scanner = ContractAuthorityScanner()
        with patch.object(scanner._chain, "rpc_call", AsyncMock(return_value={"result": None})):
            report = await scanner.scan("TokenMint11111111111111111111111111111", "solana")
        assert any("not found" in w.lower() for w in report.warnings)
        assert report.risk_score >= 0

    @pytest.mark.asyncio
    async def test_scan_solana_rpc_error_graceful(self) -> None:
        """RPC exceptions should be captured into report warnings."""
        scanner = ContractAuthorityScanner()
        with patch.object(scanner._chain, "rpc_call", AsyncMock(side_effect=KeyError("boom"))):
            report = await scanner.scan("TokenMint11111111111111111111111111111", "solana")
        assert any("error" in w.lower() for w in report.warnings)


class TestParseSplMintRaw:
    """Raw SPL mint account byte parsing."""

    def test_parse_renounced_mint_raw(self) -> None:
        """All-zero 82-byte mint data should parse as fully renounced."""
        scanner = ContractAuthorityScanner()
        parsed = scanner._parse_spl_mint_raw(b"\x00" * 82, "mint")
        assert parsed == {"mint_authority": "", "freeze_authority": ""}

    def test_parse_active_mint_authority_raw(self) -> None:
        """Non-zero mint authority bytes should produce a public key."""
        scanner = ContractAuthorityScanner()
        # 32 bytes mint auth (first byte set) + supply + decimals + init + 32 bytes freeze + pad
        raw = b"\x01" + b"\x00" * 31 + b"\x00" * 8 + b"\x00" + b"\x01" + b"\x00" * 32 + b"\x00" * 8
        assert len(raw) == 82
        parsed = scanner._parse_spl_mint_raw(raw, "mint")
        assert parsed["mint_authority"] != ""
        assert parsed["freeze_authority"] == ""

    def test_parse_active_freeze_authority_raw(self) -> None:
        """Non-zero freeze authority bytes should produce a public key."""
        scanner = ContractAuthorityScanner()
        # Freeze authority occupies bytes 46..78 in the SPL Mint layout.
        raw = b"\x00" * 32 + b"\x00" * 8 + b"\x00" + b"\x01" + b"\x00" * 4 + b"\x02" + b"\x00" * 31 + b"\x00" * 4
        assert len(raw) == 82
        parsed = scanner._parse_spl_mint_raw(raw, "mint")
        assert parsed["mint_authority"] == ""
        assert parsed["freeze_authority"] != ""

    def test_parse_short_buffer_returns_none(self) -> None:
        """Buffers shorter than the SPL mint layout should return None."""
        scanner = ContractAuthorityScanner()
        assert scanner._parse_spl_mint_raw(b"\x00" * 10, "mint") is None


class TestAuthorityBuilders:
    """SolanaAuthority construction helpers."""

    def test_build_solana_authority_renounced(self) -> None:
        """Zero address maps to a renounced authority."""
        scanner = ContractAuthorityScanner()
        auth = scanner._build_solana_authority("mint_authority", SOLANA_ZERO, "token")
        assert auth.is_renounced is True
        assert auth.is_token_itself is False
        assert auth.address == SOLANA_ZERO

    def test_build_solana_authority_token_itself(self) -> None:
        """Authority equal to the token address is self-referential."""
        scanner = ContractAuthorityScanner()
        auth = scanner._build_solana_authority("mint_authority", "token", "token")
        assert auth.is_token_itself is True
        assert auth.is_renounced is False

    def test_build_solana_authority_active(self) -> None:
        """A normal non-zero authority is active and not self-referential."""
        scanner = ContractAuthorityScanner()
        auth = scanner._build_solana_authority("freeze_authority", "SomeKey111111111111111111111111", "token")
        assert auth.is_renounced is False
        assert auth.is_token_itself is False
        assert auth.authority_type == "freeze_authority"


class TestEVMScan:
    """EVM proxy/upgradeability detection via mocked Etherscan API."""

    @pytest.mark.asyncio
    async def test_scan_evm_detects_eip1967_proxy(self) -> None:
        """A non-zero EIP-1967 implementation slot should mark the contract as a proxy."""
        scanner = ContractAuthorityScanner()
        with (
            patch.object(scanner, "_etherscan_get_bytecode", AsyncMock(return_value="0x6080604052")),
            patch.object(
                scanner,
                "_etherscan_get_storage",
                AsyncMock(side_effect=[IMPL_SLOT_VALUE, ADMIN_SLOT_VALUE]),
            ),
            patch.object(scanner, "_etherscan_get_abi", AsyncMock(return_value=[])),
        ):
            report = await scanner.scan("0xProxy", "ethereum")
        assert report.evm_proxy is not None
        assert report.evm_proxy.is_proxy is True
        assert report.evm_proxy.proxy_type == "EIP-1967"
        assert report.evm_proxy.implementation_address == IMPL_ADDRESS
        assert report.evm_proxy.admin_address == ADMIN_ADDRESS
        assert any("Proxy contract detected" in w for w in report.warnings)

    @pytest.mark.asyncio
    async def test_scan_evm_upgradeable_admin_not_renounced(self) -> None:
        """An active proxy admin should be flagged as upgradeable risk."""
        scanner = ContractAuthorityScanner()
        with (
            patch.object(scanner, "_etherscan_get_bytecode", AsyncMock(return_value="0x6080604052")),
            patch.object(
                scanner,
                "_etherscan_get_storage",
                AsyncMock(side_effect=[IMPL_SLOT_VALUE, ADMIN_SLOT_VALUE]),
            ),
            patch.object(scanner, "_etherscan_get_abi", AsyncMock(return_value=[])),
        ):
            report = await scanner.scan("0xProxy", "ethereum")
        assert report.evm_proxy.is_ownership_renounced is False
        assert any("Proxy admin not renounced" in w for w in report.warnings)
        assert report.risk_score >= 40

    @pytest.mark.asyncio
    async def test_scan_evm_renounced_proxy_admin(self) -> None:
        """A dead-address proxy admin should count as ownership renounced."""
        scanner = ContractAuthorityScanner()
        burned_admin = "0x" + "00" * 12 + "000000000000000000000000000000000000dEaD"
        with (
            patch.object(scanner, "_etherscan_get_bytecode", AsyncMock(return_value="0x6080604052")),
            patch.object(
                scanner,
                "_etherscan_get_storage",
                AsyncMock(side_effect=[IMPL_SLOT_VALUE, burned_admin]),
            ),
            patch.object(scanner, "_etherscan_get_abi", AsyncMock(return_value=[])),
        ):
            report = await scanner.scan("0xProxy", "ethereum")
        assert report.evm_proxy.is_ownership_renounced is True
        assert any("ownership is renounced" in w for w in report.warnings)

    @pytest.mark.asyncio
    async def test_scan_evm_not_a_contract(self) -> None:
        """Addresses without bytecode should be reported as non-contracts."""
        scanner = ContractAuthorityScanner()
        with patch.object(scanner, "_etherscan_get_bytecode", AsyncMock(return_value=None)):
            report = await scanner.scan("0xEOA", "ethereum")
        assert any("not a contract" in w.lower() for w in report.warnings)
        assert report.evm_proxy is not None
        assert report.evm_proxy.is_proxy is False

    @pytest.mark.asyncio
    async def test_scan_evm_minimal_proxy_pattern(self) -> None:
        """EIP-1167 minimal proxy bytecode should be detected as a proxy."""
        scanner = ContractAuthorityScanner()
        minimal_bytecode = "0x363d3d373d3d3d363d73" + "00" * 20
        with (
            patch.object(scanner, "_etherscan_get_bytecode", AsyncMock(return_value=minimal_bytecode)),
            patch.object(scanner, "_etherscan_get_storage", AsyncMock(return_value=None)),
            patch.object(scanner, "_etherscan_get_abi", AsyncMock(return_value=[])),
        ):
            report = await scanner.scan("0xMinimalProxy", "ethereum")
        assert report.evm_proxy.is_proxy is True
        assert report.evm_proxy.proxy_type == "Minimal_EIP-1167"

    @pytest.mark.asyncio
    async def test_scan_evm_admin_functions_from_abi(self) -> None:
        """Admin function names in the ABI should be recorded."""
        scanner = ContractAuthorityScanner()
        abi = [
            {"type": "function", "name": "transferOwnership"},
            {"type": "function", "name": "approve"},
            {"type": "function", "name": "upgradeTo"},
        ]
        with (
            patch.object(scanner, "_etherscan_get_bytecode", AsyncMock(return_value="0x6080604052")),
            patch.object(scanner, "_etherscan_get_storage", AsyncMock(return_value=None)),
            patch.object(scanner, "_etherscan_get_abi", AsyncMock(return_value=abi)),
        ):
            report = await scanner.scan("0xToken", "ethereum")
        assert report.evm_proxy.has_admin_functions is True
        assert set(report.evm_proxy.admin_function_signatures) == {"transferOwnership", "upgradeTo"}
        assert any("Admin functions found" in w for w in report.warnings)

    @pytest.mark.asyncio
    async def test_scan_unsupported_chain(self) -> None:
        """Unsupported chains should produce a warning and LOW risk report."""
        scanner = ContractAuthorityScanner()
        report = await scanner.scan("0xtoken", "cosmos")
        assert any("Unsupported chain" in w for w in report.warnings)
        assert report.risk_level == AuthorityRiskLevel.LOW.value


class TestEVMAnalysisHelpers:
    """Pure helper functions for EVM analysis."""

    def test_strip_to_address(self) -> None:
        """32-byte slot values should be trimmed to 20-byte addresses."""
        scanner = ContractAuthorityScanner()
        assert scanner._strip_to_address(IMPL_SLOT_VALUE) == IMPL_ADDRESS
        assert scanner._strip_to_address("") == ""
        assert scanner._strip_to_address("0xabc") == "0xabc"

    def test_is_minimal_proxy(self) -> None:
        """Only EIP-1167 prefix bytecode should match."""
        scanner = ContractAuthorityScanner()
        assert scanner._is_minimal_proxy("0x363d3d373d3d3d363d73...") is True
        assert scanner._is_minimal_proxy("0x6080604052") is False
        assert scanner._is_minimal_proxy("") is False

    def test_analyze_evm_abi_ignores_non_function_entries(self) -> None:
        """Non-function ABI entries should not contribute admin signatures."""
        scanner = ContractAuthorityScanner()
        proxy_info = EVMProxyInfo()
        abi = [
            {"type": "event", "name": "transferOwnership"},
            {"type": "function", "name": "transferOwnership"},
            "not-a-dict",
        ]
        mini_report = ContractAuthorityReport(token_address="0x", chain="ethereum")
        scanner._analyze_evm_abi(abi, proxy_info, mini_report)
        assert proxy_info.has_admin_functions is True
        assert proxy_info.admin_function_signatures == ["transferOwnership"]


if __name__ == "__main__":
    pytest.main([__file__])
