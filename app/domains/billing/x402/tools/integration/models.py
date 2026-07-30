"""Data models and constants for x402 integration tools."""

from __future__ import annotations

from typing import ClassVar

NETWORKS: ClassVar[list[str]] = [
    "base",
    "solana",
    "ethereum",
    "bsc",
    "arbitrum",
    "polygon",
    "avalanche",
    "fantom",
    "gnosis",
    "optimism",
    "tron",
    "bitcoin",
    "sepa",
]

CHAIN_SUFFIXES: ClassVar[frozenset[str]] = frozenset(
    {
        "solana",
        "base",
        "ethereum",
        "bsc",
        "polygon",
        "arbitrum",
        "optimism",
        "avalanche",
        "fantom",
        "gnosis",
        "tron",
        "bitcoin",
    }
)
