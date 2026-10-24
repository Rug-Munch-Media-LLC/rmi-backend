"""Regression tests for the single canonical chain-name map."""

from __future__ import annotations

import pytest

from app.core.chain_names import CHAIN_MAP, normalize_chain


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("eth", "ethereum"),
        ("ETH", "ethereum"),
        ("mainnet", "ethereum"),
        ("bnb", "bsc"),
        ("binance", "bsc"),
        ("matic", "polygon"),
        ("arb", "arbitrum"),
        ("op", "optimism"),
        ("avax", "avalanche"),
        ("ftm", "fantom"),
        ("xdai", "gnosis"),
        ("solana", "solana"),
        ("base", "base"),
    ],
)
def test_normalize_chain_aliases(raw: str, expected: str) -> None:
    assert normalize_chain(raw) == expected


def test_normalize_chain_is_case_insensitive() -> None:
    assert normalize_chain("ArBiTrUm") == "arbitrum"


def test_unknown_chain_falls_back_to_lowercase_identity() -> None:
    assert normalize_chain("HyperEVM") == "hyperevm"


def test_map_values_are_canonical_ids() -> None:
    assert set(CHAIN_MAP.values()) <= {
        "ethereum",
        "bsc",
        "polygon",
        "arbitrum",
        "optimism",
        "base",
        "avalanche",
        "fantom",
        "gnosis",
        "solana",
    }
