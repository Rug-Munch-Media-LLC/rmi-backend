"""SENTINEL - Contract Authority Detection.

THIN ADAPTER. Canonical source: ``rugmunch_scanner.detectors.contract_authority``.
The implementation was superseded by the private rugmunch-scanner repo; this
module only preserves the backward-compatible ``app.domains.scanners.
contract_authority`` import path for callers (sentinel_pipeline, x402 report
tools, rug_imminence_predictor). All public symbols are re-exported explicitly
from the scanner package.
"""

from __future__ import annotations

from rugmunch_scanner.detectors.contract_authority import (
    BSCSCAN_API_KEY,
    EIP1822_SLOT,
    EIP1967_ADMIN_SLOT,
    EIP1967_BEACON_SLOT,
    EIP1967_IMPL_SLOT,
    ETHERSCAN_API_KEY,
    ETHERSCAN_NETWORKS,
    OZ_TRANSPARENT_ADMIN_SIG,
    OZ_UUPS_UPGRADE_SIG,
    POLYGONSCAN_API_KEY,
    SNOWTRACE_API_KEY,
    SOLANA_ZERO_ADDRESS,
    SPL_TOKEN_PROGRAM_ID,
    SYSTEM_PROGRAM_ID,
    TOKEN_2022_PROGRAM_ID,
    AuthorityRiskLevel,
    ContractAuthorityReport,
    ContractAuthorityScanner,
    EVMProxyInfo,
    SolanaAuthorities,
    SolanaAuthority,
    logger,
    run_contract_authority_scan,
)

__all__ = [
    "BSCSCAN_API_KEY",
    "EIP1822_SLOT",
    "EIP1967_ADMIN_SLOT",
    "EIP1967_BEACON_SLOT",
    "EIP1967_IMPL_SLOT",
    "ETHERSCAN_API_KEY",
    "ETHERSCAN_NETWORKS",
    "OZ_TRANSPARENT_ADMIN_SIG",
    "OZ_UUPS_UPGRADE_SIG",
    "POLYGONSCAN_API_KEY",
    "SNOWTRACE_API_KEY",
    "SOLANA_ZERO_ADDRESS",
    "SPL_TOKEN_PROGRAM_ID",
    "SYSTEM_PROGRAM_ID",
    "TOKEN_2022_PROGRAM_ID",
    "AuthorityRiskLevel",
    "ContractAuthorityReport",
    "ContractAuthorityScanner",
    "EVMProxyInfo",
    "SolanaAuthorities",
    "SolanaAuthority",
    "logger",
    "run_contract_authority_scan",
]
