"""
Wallet Entity Resolution — chain lookups, address validation, ownership verification.

Extracted from manager.py during god-file split (2026-07-23).
"""

from __future__ import annotations
from app.core.logging import get_logger
from app.domains.wallet.crypto import ZKAddressVerifier  # re-exported for convenience
from app.domains.wallet.models import CHAIN_REGISTRY, ChainMeta

logger = get_logger("wallet_entities")

def get_chain_meta(chain: str) -> ChainMeta | None:
    """Get chain metadata by key."""
    return CHAIN_REGISTRY.get(chain)

def list_chains() -> list[str]:
    """List all supported chain keys."""
    return sorted(CHAIN_REGISTRY.keys())

def get_chains_by_group() -> dict[str, list[str]]:
    """Get chains grouped by ecosystem."""
    groups = {
        "evm": [
            k for k, v in CHAIN_REGISTRY.items()
            if v.curve == "secp256k1"
            and v.key
            in [
                "eth", "base", "polygon", "arbitrum", "optimism",
                "avalanche", "bsc", "fantom", "gnosis",
            ]
        ],
        "cosmos": ["atom", "osmo", "juno", "secret", "inj"],
        "bitcoin": [
            "btc", "btc-segwit", "btc-native-segwit",
            "ltc", "doge", "dash", "bch", "zec",
        ],
        "privacy": ["xmr"],
        "l1_alt": [
            "sol", "dot", "ksm", "ada", "algo", "xlm", "xrp",
            "near", "apt", "sui", "trx", "xtz", "nano", "ton", "band",
        ],
    }
    return groups

def resolve_chain_address(chain: str, address: str) -> bool:
    """
    Validate that an address matches the expected pattern for a chain.

    Returns True if valid or if no pattern is defined for the chain.
    """
    import re

    meta = CHAIN_REGISTRY.get(chain)
    if not meta or not meta.address_pattern:
        return True
    return bool(re.match(meta.address_pattern, address))

__all__ = [
    "ZKAddressVerifier",
    "get_chain_meta",
    "get_chains_by_group",
    "list_chains",
    "resolve_chain_address",
]
