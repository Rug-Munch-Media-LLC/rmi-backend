"""Chain-aware address normalization for dedup keying.

EVM addresses are EIP-55 checksummed so one wallet in mixed casing maps to a
single key; Solana base58 addresses are case-sensitive and preserved verbatim.
Anything unrecognized is returned trimmed (fail-safe, never raises).
"""

from __future__ import annotations

import re

_EVM_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
# Solana base58 addresses (no 0/I/O/l, case-sensitive by design).
_SOLANA_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

_EVM_CHAINS = frozenset(
    {
        "evm",
        "eth",
        "ethereum",
        "base",
        "bsc",
        "polygon",
        "arbitrum",
        "optimism",
        "avalanche",
        "fantom",
        "gnosis",
    }
)
_SOLANA_CHAINS = frozenset({"sol", "solana"})


def is_valid_evm_address(address: str) -> bool:
    """True when *address* looks like a 0x-prefixed 40-hex EVM address."""
    return bool(address and _EVM_ADDRESS_RE.match(address.strip()))


def to_checksum_address(address: str) -> str:
    """EIP-55 checksum an EVM address (idempotent).

    Falls back to lowercase when ``eth_utils`` is unavailable so dedup never
    hard-depends on the optional library. Non-EVM-shaped input is returned
    unchanged (fail-safe, never raises).
    """
    if not is_valid_evm_address(address):
        return address
    try:
        # eth-utils' py.typed top-level re-export is invisible to mypy (name
        # imported into __init__ without an `as` alias) — genuine stub gap.
        from eth_utils import to_checksum_address as _eth_checksum  # type: ignore[attr-defined]

        return _eth_checksum(address)
    except Exception:  # pragma: no cover - optional dependency fallback
        return address.lower()


def normalize_address(address: str, chain: str = "evm") -> str:
    """Normalize *address* for dedup keying by chain family.

    * EVM (default, plus eth/base/bsc/… aliases): EIP-55 checksum so the same
      wallet in mixed casing maps to one key.
    * Solana (``sol``/``solana``): base58 is case-sensitive — preserved
      verbatim (whitespace trimmed only).
    * Unknown chain: trimmed as-is (fail-safe, never raises).
    """
    if not address:
        return ""
    addr = address.strip()
    c = (chain or "evm").strip().lower()
    if c in _SOLANA_CHAINS:
        return addr
    if c in _EVM_CHAINS:
        return to_checksum_address(addr)
    return addr
