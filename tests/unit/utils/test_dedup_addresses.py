"""Chain-aware address normalization for dedup keying."""

from __future__ import annotations

import pytest

from app.utils.dedup import is_valid_evm_address, normalize_address, to_checksum_address

LOWER = "0xabcdef0123456789abcdef0123456789abcdef01"
CHECKSUM = "0xAbCdEf0123456789aBcDeF0123456789AbCdEf01"
SOLANA = "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"


@pytest.mark.parametrize("address", [LOWER, CHECKSUM, f"  {LOWER}  "])
def test_valid_evm_address(address: str) -> None:
    assert is_valid_evm_address(address) is True


@pytest.mark.parametrize(
    "address",
    ["", "0x123", "not-an-address", SOLANA, "0x" + "g" * 40, None],
)
def test_invalid_evm_address(address: str | None) -> None:
    assert is_valid_evm_address(address or "") is False


def test_evm_normalization_collapses_case() -> None:
    assert normalize_address(LOWER) == normalize_address(CHECKSUM)
    assert normalize_address(LOWER) == to_checksum_address(LOWER)
    assert normalize_address(LOWER, "eth") == normalize_address(LOWER, "base")
    assert normalize_address(LOWER).startswith("0x")


def test_checksum_is_idempotent() -> None:
    once = to_checksum_address(LOWER)
    assert to_checksum_address(once) == once


def test_non_evm_input_returned_unchanged() -> None:
    assert to_checksum_address(SOLANA) == SOLANA
    assert to_checksum_address("nope") == "nope"


def test_solana_preserves_case() -> None:
    assert normalize_address(SOLANA, "sol") == SOLANA
    assert normalize_address(f"  {SOLANA}  ", "solana") == SOLANA


def test_unknown_chain_only_trims() -> None:
    mixed = "  AbC-Def  "
    assert normalize_address(mixed, "cosmos") == "AbC-Def"
    assert normalize_address(mixed, "") == normalize_address(mixed, "evm")


def test_empty_address() -> None:
    assert normalize_address("") == ""
    assert normalize_address("", "sol") == ""
