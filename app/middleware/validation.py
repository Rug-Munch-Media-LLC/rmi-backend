"""
Input Validation Middleware
===========================

Comprehensive Pydantic-based validation for security endpoints.
Includes:
- Request sanitization
- Type coercion
- Length/range validation
- Regex pattern validation
- Custom validators
"""

import logging
import re

from pydantic import BaseModel, Field, validator

logger = logging.getLogger(__name__)


# ─── COMMON VALIDATION CONSTANTS ─────────────────────────────────

ADDRESS_PATTERN = re.compile(r"^(0x)?[0-9a-fA-F]{38,64}$")
HASH_PATTERN = re.compile(r"^(0x)?[0-9a-fA-F]{64}$")
WALLET_PATTERN = re.compile(r"^(0x)?[0-9a-fA-F]{38,64}$")
CHAIN_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]+$")

MIN_ADDRESS_LENGTH = 38  # 0x + 38 hex chars = 20 bytes (EVM)
MAX_ADDRESS_LENGTH = 66  # 0x + 66 hex chars = 32 bytes (some chains)
MIN_CHAIN_LENGTH = 2
MAX_CHAIN_LENGTH = 20

# Chain names whitelist
VALID_CHAINS = {
    "ethereum",
    "base",
    "arbitrum",
    "optimism",
    "polygon",
    "bsc",
    "avalanche",
    "fantom",
    "celo",
    "moonbeam",
    "polygon_zkevm",
    "linea",
    "scroll",
    "zksync",
    "blast",
    "Mode",
    "sepolia",
    "holesky",
}


# ─── BASE REQUEST MODELS ────────────────────────────────────────


class WalletRequest(BaseModel):
    """Base model for wallet address requests."""

    address: str = Field(
        ...,
        description="Wallet address (EVM format: 0x...)",
        min_length=MIN_ADDRESS_LENGTH,
        max_length=MAX_ADDRESS_LENGTH,
    )
    chain: str = Field(default="ethereum", description="Blockchain network name")

    @validator("address")
    def validate_address(self, v: str) -> str:
        """Validate wallet address format."""
        if not v.startswith("0x"):
            v = "0x" + v
        if not ADDRESS_PATTERN.match(v):
            raise ValueError(f"Invalid address format: {v}")
        return v.lower()

    @validator("chain")
    def validate_chain(self, v: str) -> str:
        """Validate chain name."""
        if v not in VALID_CHAINS:
            logger.warning(f"Unknown chain '{v}', allowing anyway for flexibility")
        return v.lower()


class BatchWalletRequest(BaseModel):
    """Request for batch wallet operations."""

    addresses: list[str] = Field(..., min_length=1, max_length=50, description="List of wallet addresses")
    chain: str = Field(default="ethereum", description="Blockchain network")

    @validator("addresses", each_item=True)
    def validate_address(self, v: str) -> str:
        """Validate each address in batch."""
        if not v.startswith("0x"):
            v = "0x" + v
        if not ADDRESS_PATTERN.match(v):
            raise ValueError(f"Invalid address format: {v}")
        return v.lower()

    @validator("chain")
    def validate_chain(self, v: str) -> str:
        """Validate chain name."""
        if v not in VALID_CHAINS:
            logger.warning(f"Unknown chain '{v}', allowing anyway")
        return v.lower()


class ContractRequest(WalletRequest):
    """Request for contract operations."""

    deep: bool = Field(default=False, description="Run deep scan with Slither/Mythril")


class TextSearchRequest(BaseModel):
    """Request for text-based searches."""

    query: str = Field(..., min_length=1, max_length=200, description="Search query")
    limit: int = Field(default=10, ge=1, le=100, description="Maximum results to return")


# ─── SECURITY-FOCUSED MODELS ────────────────────────────────────


class ThreatCheckRequest(WalletRequest):
    """Request for threat intelligence checks."""

    check_sanctions: bool = Field(default=True, description="Check OFAC sanctions list")
    check_risk: bool = Field(default=True, description="Check risk score")
    check_exchange: bool = Field(default=True, description="Check if address is exchange/deposit")


class MempoolMonitorRequest(WalletRequest):
    """Request for mempool monitoring."""

    threshold_gas_gwei: float = Field(default=50.0, ge=1.0, le=1000.0, description="Gas price threshold in Gwei")
    alert_on_bot: bool = Field(default=True, description="Alert on bot detection")


class PortfolioRequest(WalletRequest):
    """Request for portfolio analysis."""

    include_tokens: bool = Field(default=True, description="Include ERC-20 tokens")
    include_nfts: bool = Field(default=False, description="Include NFTs")
    history_days: int = Field(default=30, ge=1, le=365, description="Days of transaction history")


# ─── UTILITY VALIDATORS ─────────────────────────────────────────


def tier_validator(tier: str, rate_type: str = "requests_per_hour") -> dict[str, int]:
    """
    Get rate limit for authentication tier.

    Args:
        tier: Authentication tier (FREE, BASIC, PREMIUM, ENTERPRISE)
        rate_type: Rate type to check

    Returns:
        Rate limit configuration
    """
    tiers = {
        "FREE": {"requests_per_hour": 10, "requests_per_day": 100},
        "BASIC": {"requests_per_hour": 50, "requests_per_day": 500},
        "PREMIUM": {"requests_per_hour": 200, "requests_per_day": 2000},
        "ENTERPRISE": {"requests_per_hour": 1000, "requests_per_day": 10000},
    }
    return tiers.get(tier.upper(), tiers["FREE"])


def validate_x402_payment(required: int, tier: str) -> bool:
    """
    Validate x402 micropayment capability based on tier.

    Args:
        required: Required x402 tokens
        tier: User's authentication tier

    Returns:
        True if payment capability is sufficient
    """
    tier_caps = {
        "FREE": 10,
        "BASIC": 100,
        "PREMIUM": 1000,
        "ENTERPRISE": 10000,
    }
    available = tier_caps.get(tier.upper(), 0)
    return available >= required
