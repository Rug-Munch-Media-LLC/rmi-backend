"""
Web3 Chat Models
================
Constants, enums, and data models for Web3-native chat features.
"""


from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

# ── Constants ─────────────────────────────────────────────────────

SUPPORTED_CHAINS = {
    "ethereum": {"chain_id": 1, "native": "ETH", "explorer": "https://etherscan.io/tx/"},
    "solana": {"chain_id": 900, "native": "SOL", "explorer": "https://solscan.io/tx/"},
    "base": {"chain_id": 8453, "native": "ETH", "explorer": "https://basescan.org/tx/"},
    "polygon": {"chain_id": 137, "native": "MATIC", "explorer": "https://polygonscan.com/tx/"},
    "optimism": {"chain_id": 10, "native": "ETH", "explorer": "https://optimistic.etherscan.io/tx/"},
    "arbitrum": {"chain_id": 42161, "native": "ETH", "explorer": "https://arbiscan.io/tx/"},
    "lightning": {"chain_id": 0, "native": "BTC", "explorer": "https://mempool.space/tx/"},
}

SUPPORTED_TOKENS = {
    "ETH": {"decimals": 18, "chains": ["ethereum", "base", "arbitrum", "optimism"]},
    "SOL": {"decimals": 9, "chains": ["solana"]},
    "USDC": {"decimals": 6, "chains": ["ethereum", "solana", "base", "polygon", "arbitrum", "optimism"]},
    "USDT": {"decimals": 6, "chains": ["ethereum", "polygon", "arbitrum", "optimism"]},
    "DAI": {"decimals": 18, "chains": ["ethereum", "polygon", "arbitrum", "optimism"]},
    "BTC": {"decimals": 8, "chains": ["lightning"]},
}

# ERC-20 token addresses (common)
TOKEN_ADDRESSES: dict[str, dict[str, str]] = {
    "ethereum": {
        "USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "USDT": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        "DAI": "0x6B175474E89094C44Da98b954EedeAC495271d0F",
    },
    "base": {
        "USDC": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "DAI": "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb",
    },
    "polygon": {
        "USDC": "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
        "USDT": "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
        "DAI": "0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063",
    },
    "arbitrum": {
        "USDC": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        "USDT": "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
    },
    "optimism": {
        "USDC": "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
        "USDT": "0x94b008aA00579c1307B0EF2c499aD98a8ce58e58",
        "DAI": "0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1",
    },
    "solana": {
        "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    },
}

# ── Enums ─────────────────────────────────────────────────────────


class PaymentStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    EXPIRED = "expired"


class GateType(StrEnum):
    TOKEN = "token"  # noqa: S105 — config default
    NFT = "nft"
    MIN_VALUE = "min_value"
    ANY_NFT = "any_nft"


class ChainType(StrEnum):
    ETHEREUM = "ethereum"
    SOLANA = "solana"
    BASE = "base"
    POLYGON = "polygon"
    OPTIMISM = "optimism"
    ARBITRUM = "arbitrum"
    LIGHTNING = "lightning"


# ── Data Models ───────────────────────────────────────────────────


@dataclass
class PaymentRequest:
    sender_id: str
    recipient_username: str
    amount: str
    token: str
    chain: str
    room_id: str | None = None
    gas_estimation: dict[str, Any] | None = None
    tx_hash: str | None = None
    status: PaymentStatus = PaymentStatus.PENDING
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    id: str = field(default_factory=lambda: f"pay-{uuid.uuid4().hex[:12]}")


@dataclass
class TokenGate:
    room_id: str
    gate_type: GateType
    token_address: str | None = None
    token_symbol: str | None = None
    chain: str | None = None
    min_amount: str | None = None
    nft_contract: str | None = None
    nft_collection_name: str | None = None
    nft_creator: str | None = None
    enabled: bool = True


@dataclass
class TipRecord:
    id: str
    from_user: str
    to_user: str
    amount: str
    token: str
    chain: str
    room_id: str | None
    message: str | None
    tx_hash: str | None
    status: PaymentStatus
    created_at: str


@dataclass
class SplitPayment:
    id: str
    initiator: str
    recipients: list[str]
    total_amount: str
    token: str
    chain: str
    room_id: str | None
    tx_hash: str | None
    status: PaymentStatus
    created_at: str
