"""Shared utilities: address validation, chain normalization, and common helpers.

Consolidates duplicate patterns found across the codebase:
- 12+ files defining identical EVM/Solana regex patterns
- 6 files with duplicate ``is_valid_address`` implementations
- 3 files with duplicate ``normalize_chain`` implementations
- 3 overlapping chain-alias dictionaries

Usage::

    from app.core.shared_utils import is_valid_address, normalize_chain

    if is_valid_address(wallet):
        chain = normalize_chain(user_input)
"""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse

# ── Address regex patterns (single source of truth) ────────────────
# These replace the 12+ identical copies scattered across the codebase.

EVM_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
"""EVM address pattern: 0x-prefixed 40-char hex string."""

SOLANA_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
"""Solana address pattern: base58, 32-44 chars, no ambiguous chars (0OIl)."""

# ── Consolidated chain alias map ───────────────────────────────────

CHAIN_ALIASES: dict[str, list[str]] = {
    "ethereum": ["eth", "ethereum", "mainnet", "1"],
    "bsc": ["bsc", "bnb", "binance", "bnb_chain", "56"],
    "polygon": ["polygon", "matic", "137"],
    "arbitrum": ["arbitrum", "arb", "42161"],
    "optimism": ["optimism", "op", "10"],
    "avalanche": ["avalanche", "avax", "43114"],
    "fantom": ["fantom", "ftm", "250"],
    "base": ["base", "8453"],
    "solana": ["solana", "sol", "mainnet-beta"],
    "tron": ["tron", "trx"],
    "bitcoin": ["bitcoin", "btc"],
}

_CHAIN_FROM_ALIAS: dict[str, str] = {}
for _canonical, _aliases in CHAIN_ALIASES.items():
    for _alias in _aliases:
        _CHAIN_FROM_ALIAS[_alias.lower()] = _canonical


def normalize_chain(chain: str) -> str:
    """Normalize a chain name/alias to its canonical form.

    Args:
        chain: A chain name or alias (e.g. ``"ETH"``, ``"bsc"``, ``"42161"``).

    Returns:
        Canonical chain name (e.g. ``"ethereum"``, ``"arbitrum"``).
        Falls back to lowercased input if unknown.
    """
    return _CHAIN_FROM_ALIAS.get(chain.lower().strip(), chain.lower().strip())


# ── Address validation ─────────────────────────────────────────────


def is_valid_address(address: str) -> bool:
    """Check whether *address* is a valid EVM or Solana address.

    Accepts:
        - ``0x``-prefixed 40-char hex strings (EVM)
        - base58 strings 32–44 chars long (Solana)

    Args:
        address: The wallet/contract address to validate.

    Returns:
        ``True`` if the address format is valid.
    """
    if not address or not isinstance(address, str):
        return False
    addr = address.strip()
    if EVM_ADDRESS_RE.match(addr):
        return True
    return bool(SOLANA_ADDRESS_RE.match(addr))


def is_evm_address(address: str) -> bool:
    """Check whether *address* is a valid EVM address.

    Args:
        address: The address to validate.

    Returns:
        ``True`` if it matches the ``0x``-prefixed 40-char hex pattern.
    """
    return bool(address and EVM_ADDRESS_RE.match(address.strip()))


def is_solana_address(address: str) -> bool:
    """Check whether *address* is a valid Solana address.

    Args:
        address: The address to validate.

    Returns:
        ``True`` if it matches the base58 32–44 char pattern.
    """
    return bool(address and SOLANA_ADDRESS_RE.match(address.strip()))


def detect_address_type(address: str) -> str:
    """Detect whether *address* is EVM or Solana.

    Returns:
        ``"evm"``, ``"solana"``, or ``""`` if unrecognised.
    """
    addr = address.strip()
    if EVM_ADDRESS_RE.match(addr):
        return "evm"
    if SOLANA_ADDRESS_RE.match(addr):
        return "solana"
    return ""


# ── EIP-55 checksum validation ─────────────────────────────────────


def validate_eip55_checksum(address: str) -> bool:
    """Validate an EIP-55 mixed-case checksummed Ethereum address.

    Uses ``keccak256`` via ``eth_hash`` if available; falls back to
    accepting any well-formed hex address otherwise.

    Args:
        address: The ``0x``-prefixed 42-char address.

    Returns:
        ``True`` if the checksum is valid (or if the keccak library
        is unavailable but the basic hex format is correct).
    """
    if not address.startswith("0x") or len(address) != 42:
        return False
    if not EVM_ADDRESS_RE.match(address):
        return False

    # All-lower or all-upper passes basic validation without keccak
    if address == address.lower() or address == address.upper():
        return True

    try:
        from eth_hash.auto import keccak

        hashed = keccak(address[2:].lower().encode("ascii")).hex()
    except ImportError:
        # Fallback: accept mixed-case without strict EIP-55
        return True

    for i, ch in enumerate(address[2:]):
        if ch.isalpha():
            should_be_upper = int(hashed[i], 16) >= 8
            if should_be_upper and ch.islower():
                return False
            if not should_be_upper and ch.isupper():
                return False
    return True


# ── Wallet address validation with chain detection ─────────────────


def validate_wallet_address(address: str) -> tuple[bool, str]:
    """Validate a wallet address and return its chain type.

    Returns:
        A ``(valid, chain_type)`` tuple where *chain_type* is
        ``"evm"``, ``"solana"``, or ``""``.
    """
    addr = address.strip()
    if EVM_ADDRESS_RE.match(addr):
        return True, "evm"
    if SOLANA_ADDRESS_RE.match(addr):
        return True, "solana"
    # Lenient fallback for Solana-like addresses without strict regex
    if not addr.startswith("0x") and len(addr) >= 32:
        return True, "solana"
    if addr.startswith("0x") and len(addr) == 42:
        return True, "evm"
    return False, ""


def validate_public_url(url: str) -> str:
    """SSRF guard: only public http/https URLs are allowed.

    Blocks private / loopback / link-local / reserved ranges:
      127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16
    and only permits http/https schemes. Resolves the hostname and requires
    every resolved address to be a global (public) unicast address.

    Returns the validated URL on success, raises ValueError on violation.
    Callers must also disable redirect-following so a crafted 2xx->internal
    redirect cannot bypass this check.
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise ValueError("Only http/https URLs are allowed")
    host = parsed.hostname
    if not host:
        raise ValueError("URL must include a host")
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise ValueError(f"Unable to resolve host {host}: {exc}") from exc
    if not infos:
        raise ValueError(f"Host {host} does not resolve")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise ValueError("Only public hosts are allowed")
    return url


__all__ = [
    "CHAIN_ALIASES",
    "EVM_ADDRESS_RE",
    "SOLANA_ADDRESS_RE",
    "detect_address_type",
    "is_evm_address",
    "is_solana_address",
    "is_valid_address",
    "normalize_chain",
    "validate_eip55_checksum",
    "validate_public_url",
    "validate_wallet_address",
]
