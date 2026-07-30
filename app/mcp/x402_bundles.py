"""x402 tool bundles and multi-chain payment facilitator config."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, ClassVar


@dataclass
class X402Bundle:
    """Complete x402 tool bundle definition."""

    bundle_id: str
    name: str
    description: str
    category: str
    endpoints: list[dict[str, Any]] = field(default_factory=list)
    price_usd: float = 0.0
    tier: str = "standard"  # free, trial, standard, premium, enterprise
    is_premium: bool = False
    requires_payment: bool = True

    @property
    def base_eth(self) -> str:
        return f"{self.price_usd / 3000:.6f} ETH"

    @property
    def solana_usdc(self) -> str:
        return f"{self.price_usd:.2f} USDC"

    def dict(self) -> dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "endpoints": self.endpoints,
            "endpoint_count": len(self.endpoints),
            "price_usd": self.price_usd,
            "tier": self.tier,
            "is_premium": self.is_premium,
            "requires_payment": self.requires_payment,
            "pricing": {"base": self.base_eth, "solana": self.solana_usdc},
        }


class MultiChainFacilitator:
    """Multi-chain payment facilitator."""

    base_config: ClassVar[dict] = {
        "name": "RugMunch Intelligence x402 Gateway",
        "network": "BASE",
        "wallet_address": os.environ.get("X402_BASE_WALLET", "0x1E3AC01d0fdb976179790BDD02823196A92705C9"),
        "token_symbol": "ETH",
        "token_decimals": 18,
        "rpc_url": "https://mainnet.base.org",
        "explorer": "https://basescan.org",
    }

    solana_config: ClassVar[dict] = {
        "name": "RugMunch Intelligence x402 Gateway",
        "network": "SOLANA",
        "wallet_address": os.environ.get("X402_SOLANA_WALLET", "Gix4P9AmwcZRGzr2hCEME5m2QAvY86dBfm8c7e7MpFzv"),
        "token_symbol": "USDC",
        "token_decimals": 6,
        "rpc_url": "https://api.mainnet-beta.solana.com",
        "explorer": "https://solscan.io",
    }

    third_party: ClassVar[dict] = {
        "base": "https://mcp.rugmunch.io",
        "solana": "https://mcp.rugmunch.io",
    }
