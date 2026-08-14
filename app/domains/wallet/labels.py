"""
Wallet Labels — label/tag management, known-entity registry, legacy importing.

Extracted from manager.py during god-file split (2026-07-23).

Known-entity registry (``label_known_entities``)
-----------------------------------------------
A bundled, static seed of well-known public exchange/CEX wallet addresses
(EVM deposit/hot wallets + a Solana starter) with ``source`` + ``confidence``
metadata. The seed is intentionally small and curated; the FULL list is meant
to be loaded from disk (see ``RMI_KNOWN_ENTITIES_PATH``) with entries exported
from Etherscan public labels / explorer label APIs. No live scraping happens
inside the label function — resolution is a pure in-memory lookup.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from app.core.logging import get_logger
from app.domains.wallet.models import (
    CHAIN_REGISTRY,
    WalletPurpose,
    WalletRecord,
    WalletTier,
)

logger = get_logger("wallet_labels")


def import_legacy_wallets(manager: object) -> int:
    """Import wallets from legacy v1 JSON files into v2 vault."""
    imported = 0

    legacy_files = [
        "/root/.rmi/wallets/x402_wallets.json",
        "/root/.rmi/wallets/referral_wallets.json",
        "/app/data/wallets/x402_wallets.json",
        "/app/data/wallets/referral_wallets.json",
    ]

    for path in legacy_files:
        if not os.path.exists(path):
            continue

        try:
            with open(path) as f:
                data = json.load(f)

            wallets_data = data.get("wallets", {})
            for chain_key, wdata in wallets_data.items():
                chain_map = {
                    "tron": "trx",
                    "bitcoin": "btc",
                    "eth": "eth",
                    "sol": "sol",
                }
                chain = chain_map.get(chain_key, chain_key)

                # Check if already imported via get_wallet_by_address
                existing = None
                if hasattr(manager, "get_wallet_by_address"):
                    existing = manager.get_wallet_by_address(chain, wdata["address"])
                if existing:
                    logger.info(f"Legacy {chain_key} wallet already in vault")
                    continue

                wallet_id = f"wal_{chain}_legacy_{int(time.time())}_{secrets.token_hex(4)}"
                wallet = WalletRecord(
                    wallet_id=wallet_id,
                    chain=chain,
                    address=wdata["address"],
                    public_key=wdata.get("public_key_hex", ""),
                    name=f"Legacy {chain_key.upper()} Wallet",
                    purpose=WalletPurpose.PAYMENTS.value
                    if path.endswith("x402_wallets.json")
                    else WalletPurpose.OPERATIONS.value,
                    tier=WalletTier.WARM.value,
                    tags=["legacy", "imported"],
                    created_at=data.get("created", datetime.now(UTC).isoformat()),
                    created_by="import_script",
                )

                if hasattr(manager, "_wallets"):
                    manager._wallets[wallet_id] = wallet
                imported += 1
                logger.info(f"Imported legacy wallet: {chain_key} -> {wallet_id}")
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.error(f"Legacy import error ({path}): {e}")

    if imported > 0 and hasattr(manager, "_save_vault"):
        manager._save_vault()
        logger.info(f"Imported {imported} legacy wallets")

    return imported


# ── Known-entity registry (CEX / exchange wallets) ──────────────────────

# Env var pointing at a JSON file with a fuller entity list (see
# ``_load_entities_from_disk`` for the schema). Default: bundled seed only.
KNOWN_ENTITIES_PATH_ENV = "RMI_KNOWN_ENTITIES_PATH"

# Chain name aliases → canonical keys from ``CHAIN_REGISTRY``.
_CHAIN_ALIASES = {
    "ethereum": "eth",
    "binance smart chain": "bsc",
    "bnb": "bsc",
    "polygon pos": "polygon",
    "avax": "avalanche",
    "fantom opera": "fantom",
    "gnosis chain": "gnosis",
    "arbitrum one": "arbitrum",
    "solana": "sol",
    "tron": "trx",
    "bitcoin": "btc",
}

# EVM chain keys: secp256k1 / bip44 / 0x-prefixed addresses from CHAIN_REGISTRY.
_EVM_CHAIN_KEYS = frozenset(
    key
    for key, meta in CHAIN_REGISTRY.items()
    if meta.curve == "secp256k1" and meta.derivation == "bip44" and meta.address_prefix == "0x"
)


def _normalize_chain(chain: str) -> str:
    """Lowercase + alias a chain identifier to its canonical registry key."""
    c = (chain or "").strip().lower()
    return _CHAIN_ALIASES.get(c, c)


def _is_evm_chain_key(chain: str) -> bool:
    return chain in _EVM_CHAIN_KEYS


@dataclass(frozen=True)
class KnownEntity:
    """A well-known wallet address with provenance metadata.

    Attributes:
        address: Canonical address (EIP-55 checksummed for EVM, exact base58
            for Solana — Solana is case-sensitive).
        name: Entity name, e.g. ``"Binance"`` (lowercased by the labeler).
        entity_type: ``"cex"`` / ``"exchange"`` / ``"dex"`` / ``"mixer"`` —
            aligns with the label_type values the scanner's entity graph
            consumes (``entity_graph.py`` renders ``cex`` nodes amber).
        chains: Chain keys this address is known on (e.g. ``("eth",)``).
            ``("evm",)`` is a wildcard matching any EVM chain.
        source: Provenance, e.g. ``"etherscan-public-labels"``.
        confidence: 0..1 confidence the address belongs to the entity.
        tags: Optional extra tags (e.g. ``("deposit",)`` / ``("hot",)``).
    """

    address: str
    name: str
    entity_type: str
    chains: tuple[str, ...]
    source: str
    confidence: float
    tags: tuple[str, ...] = ()


# Static curated seed. Sources are public Etherscan/explorer label lists and
# community whale-tracking datasets. EVM addresses are canonical (EIP-55);
# the Solana entry is the widely-cited Binance SOL hot wallet — see [REVIEW]
# flags in the task write-up: verify against Helius/explorer before relying on
# it, and load the fuller list from disk via ``RMI_KNOWN_ENTITIES_PATH``.
KNOWN_ENTITIES_SEED: list[KnownEntity] = [
    # ── Binance (Ethereum hot/cold wallets, Etherscan labels) ──
    KnownEntity(
        "0x28C6c06298d514Db089934071355E5743bf21d60",
        "binance",
        "cex",
        ("eth",),
        "etherscan-public-labels",
        0.95,
        ("cold",),
    ),
    KnownEntity(
        "0xF977814e90dA44bFA03b6295A0616a897441aceC",
        "binance",
        "cex",
        ("eth",),
        "etherscan-public-labels",
        0.95,
        ("hot",),
    ),
    KnownEntity(
        "0xDFd5293D8e347dFe59E90eFd55b2956a1343963d",
        "binance",
        "cex",
        ("eth",),
        "etherscan-public-labels",
        0.95,
        ("hot",),
    ),
    KnownEntity(
        "0x21a31Ee1afC51d94C2eFcCAa2092aD1028285549",
        "binance",
        "cex",
        ("eth",),
        "etherscan-public-labels",
        0.95,
        ("hot",),
    ),
    KnownEntity(
        "0x3f5CE5FBFe3E9af3971dD833D26bA9b5C936f0bE",
        "binance",
        "cex",
        ("eth",),
        "etherscan-public-labels",
        0.95,
        ("hot",),
    ),
    KnownEntity(
        "0xBE0eB53F46cd790Cd13851d5EFf43D12404d33E8",
        "binance",
        "cex",
        ("eth",),
        "etherscan-public-labels",
        0.95,
        ("hot",),
    ),
    # ── Coinbase (Ethereum hot wallets) ──
    KnownEntity(
        "0x71660c4005BA85c37ccec55d0C4493E66Fe775d3",
        "coinbase",
        "cex",
        ("eth",),
        "etherscan-public-labels",
        0.95,
        ("hot",),
    ),
    KnownEntity(
        "0x503828976D22510aad0201ac7EC88293211D23Da",
        "coinbase",
        "cex",
        ("eth",),
        "etherscan-public-labels",
        0.95,
        ("hot",),
    ),
    KnownEntity(
        "0xF6874c88757721a02f47592140905c4336DfBc61",
        "coinbase",
        "cex",
        ("eth",),
        "etherscan-public-labels",
        0.95,
        ("hot",),
    ),
    KnownEntity(
        "0x3cD751E6b0078Be393132286c442345e5DC49699",
        "coinbase",
        "cex",
        ("eth",),
        "etherscan-public-labels",
        0.95,
        ("hot",),
    ),
    KnownEntity(
        "0xEB2629a2734e272Bcc07BDA959863f316F4bD4Cf",
        "coinbase",
        "cex",
        ("eth",),
        "etherscan-public-labels",
        0.95,
        ("hot",),
    ),
    KnownEntity(
        "0xDdfAbCdc4D8FfC6d5beaf154f18B778f892A0740",
        "coinbase",
        "cex",
        ("eth",),
        "etherscan-public-labels",
        0.95,
        ("hot",),
    ),
    # ── Kraken (Ethereum deposit + hot wallets) ──
    KnownEntity(
        "0xe853c56864a2ebe4576a807d26fdc4a0ada51919",
        "kraken",
        "cex",
        ("eth",),
        "etherscan-public-labels",
        0.95,
        ("deposit",),
    ),
    KnownEntity(
        "0x2910543Af39abA0Cd09dBb2D50200b3E800A63D2",
        "kraken",
        "cex",
        ("eth",),
        "etherscan-public-labels",
        0.95,
        ("deposit",),
    ),
    KnownEntity(
        "0x267be1C1d684F78cb4F6a176C4911b741E4Ffdc0",
        "kraken",
        "cex",
        ("eth",),
        "etherscan-public-labels",
        0.95,
        ("hot",),
    ),
    # ── Solana (Binance SOL hot wallet — community whale-tracker datasets) ──
    # [REVIEW] verify this base58 address against Helius/explorer before
    # production reliance; confidence kept low on purpose.
    KnownEntity(
        "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9",
        "binance",
        "cex",
        ("sol",),
        "community-whale-trackers",
        0.6,
        ("hot",),
    ),
]


def _load_entities_from_disk() -> list[KnownEntity]:
    """Load additional known entities from ``RMI_KNOWN_ENTITIES_PATH``.

    Expected JSON: a list of objects, e.g.::

        [
          {
            "address": "0x…",
            "name": "OKX",
            "entity_type": "cex",
            "chains": ["eth", "bsc"],
            "source": "etherscan-public-labels",
            "confidence": 0.95,
            "tags": ["deposit"]
          }
        ]

    ``address`` and ``name`` are required; the rest fall back to defaults.
    Malformed entries are skipped with a warning. Returns [] when the env
    var is unset or the file is unreadable (bundled seed still applies).
    """
    path = os.getenv(KNOWN_ENTITIES_PATH_ENV)
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("known_entities_disk_load_failed path=%s err=%s", path, e)
        return []
    if not isinstance(raw, list):
        logger.warning("known_entities_disk_invalid path=%s expected JSON list", path)
        return []

    entities: list[KnownEntity] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            entities.append(
                KnownEntity(
                    address=str(item["address"]),
                    name=str(item["name"]),
                    entity_type=str(item.get("entity_type", "cex")),
                    chains=tuple(_normalize_chain(c) for c in item.get("chains", [])),
                    source=str(item.get("source", "disk")),
                    confidence=float(item.get("confidence", 0.8)),
                    tags=tuple(str(t) for t in item.get("tags", [])),
                )
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("known_entities_skip_malformed address=%s", item.get("address"))
    return entities


_known_entities_cache: list[KnownEntity] | None = None


def get_known_entities() -> list[KnownEntity]:
    """Return the merged registry: disk entries (override) + bundled seed.

    The result is cached; call ``reset_known_entities_cache()`` after
    changing the env var/file in tests.
    """
    global _known_entities_cache
    if _known_entities_cache is None:
        merged: dict[tuple[str, tuple[str, ...]], KnownEntity] = {}
        for entity in [*_load_entities_from_disk(), *KNOWN_ENTITIES_SEED]:
            is_evm = any(_is_evm_chain_key(c) or c == "evm" for c in entity.chains)
            key = (entity.address.lower() if is_evm else entity.address, tuple(sorted(entity.chains)))
            merged.setdefault(key, entity)
        _known_entities_cache = list(merged.values())
    return _known_entities_cache


def reset_known_entities_cache() -> None:
    """Drop the cached registry (test/operator hook after config changes)."""
    global _known_entities_cache
    _known_entities_cache = None


def label_known_entities(address: str, chain: str) -> list[str]:
    """Return known-entity tags for *address* on *chain*, or ``[]``.

    Resolution is a pure in-memory lookup against the bundled seed plus any
    disk-loaded list — never a network call. Returns a stable list of the
    form ``[<entity name>, <entity_type>]`` (e.g. ``["binance", "cex"]``) so
    downstream consumers can use the entity name and the ``cex``/``exchange``
    label_type directly.

    Matching rules:
      * EVM chains (eth, base, bsc, …) compare case-insensitively so a
        non-checksummed input still resolves to the EIP-55 registry entry.
      * Solana base58 is case-sensitive (exact match) — that is intentional.
      * Chain aliases are normalized (``"ethereum"`` → ``"eth"``).
      * ``("evm",)`` in an entity's ``chains`` matches any EVM chain.

    Unknown address / unknown chain / empty input → ``[]`` (never raises).
    """
    if not address or not address.strip():
        return []
    norm_chain = _normalize_chain(chain)
    addr = address.strip()
    input_is_evm = _is_evm_chain_key(norm_chain) or norm_chain == "evm"
    match_key = addr.lower() if input_is_evm else addr

    entity_names: set[str] = set()
    entity_types: set[str] = set()
    for entity in get_known_entities():
        chains = {_normalize_chain(c) for c in entity.chains}
        chain_hit = norm_chain in chains or ("evm" in chains and input_is_evm)
        if not chain_hit:
            continue
        entity_is_evm = any(_is_evm_chain_key(c) or c == "evm" for c in chains)
        entity_key = entity.address.lower() if entity_is_evm else entity.address
        if entity_key == match_key:
            entity_names.add(entity.name.lower())
            entity_types.add(entity.entity_type.lower())
    return sorted(entity_names) + sorted(entity_types)


__all__ = [
    "KNOWN_ENTITIES_PATH_ENV",
    "KNOWN_ENTITIES_SEED",
    "KnownEntity",
    "get_known_entities",
    "import_legacy_wallets",
    "label_known_entities",
    "reset_known_entities_cache",
]
