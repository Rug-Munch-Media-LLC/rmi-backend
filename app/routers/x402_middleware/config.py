"""
x402 Middleware — Configuration constants.
"""

from __future__ import annotations

from typing import Any

# ERC-20 token metadata for EIP-712 domain verification
TOKEN_METADATA = {
    (8453, "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"): ("USD Coin", "2"),
    (84532, "0x036cbd53842c5426634e7929541ec2318f3dcf7e"): ("USD Coin", "2"),
}

# Network configs with correct facilitator URLs
FACILITATOR_CONFIGS: dict[str, dict[str, Any]] = {
    "base": {
        "network": "eip155:8453",
        "chain_id": 8453,
        "url": "https://facilitator.payai.network",
    },
    "base-sepolia": {
        "network": "eip155:84532",
        "chain_id": 84532,
        "url": "https://x402.org/facilitator",
    },
    "solana": {
        "network": "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
        "url": "https://facilitator.payai.network",
    },
    "solana-devnet": {
        "network": "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1",
        "url": "https://facilitator.payai.network",
    },
}
