#!/usr/bin/env python3
"""Sources 6 + 7 — walletLabels.xyz and bluepages.fyi per-address enrichment.

Both are thin orchestrators over ``per_address``; they exist separately because
they differ in the address list and the API URL.
"""

from __future__ import annotations

from typing import Any

from .builtin_contracts import (
    DEFI_ADMIN_ADDRESSES,
    INCH_ROUTERS,
    TORNADO_CASH_ADDRESSES,
    UNISWAP_ROUTERS,
)
from .logging_setup import logger
from .per_address import (
    addresses_for_chain,
    ch_addresses,
    fallback_addresses,
    fetch_per_address_labels,
)
from .store_labels import insert_wallet_labels

__all__ = ["ingest_bluepages", "ingest_walletlabels_xyz"]

WALLETLABELS_URL = "https://api.walletlabels.xyz/v1/address/{addr}"
BLUEPAGES_URL = "https://bluepages.fyi/api/v1/address/{addr}"
BLUEPAGES_DIRECTORIES = (TORNADO_CASH_ADDRESSES, UNISWAP_ROUTERS, INCH_ROUTERS, DEFI_ADMIN_ADDRESSES)


async def ingest_walletlabels_xyz(ch: Any, redis: Any | None, chain: str = "ethereum") -> dict[str, int]:
    """Fetch labels from walletLabels.xyz for known addresses."""
    logger.info("=" * 60)
    logger.info(f"SOURCE: walletLabels.xyz ({chain})")
    logger.info("=" * 60)

    totals: dict[str, int] = {"wallet_labels": 0, "scam_addresses": 0}
    addresses = ch_addresses(ch, chain) if ch else []
    if not addresses:
        addresses = fallback_addresses(chain)
    logger.info(f"  Checking {len(addresses)} addresses")

    rows = await fetch_per_address_labels(WALLETLABELS_URL, addresses, chain, "walletlabels_xyz")
    if rows:
        totals["wallet_labels"] = insert_wallet_labels(ch, redis, rows)
    logger.info(f"  → {len(rows)} wallet labels from walletLabels.xyz")
    return totals


async def ingest_bluepages(ch: Any, redis: Any | None, chain: str = "ethereum") -> dict[str, int]:
    """Fetch labels from bluepages.fyi for built-in known addresses."""
    logger.info("=" * 60)
    logger.info(f"SOURCE: bluepages.fyi ({chain})")
    logger.info("=" * 60)

    totals: dict[str, int] = {"wallet_labels": 0, "scam_addresses": 0}
    addresses = addresses_for_chain(chain, BLUEPAGES_DIRECTORIES)
    logger.info(f"  Checking {len(addresses)} addresses")

    rows = await fetch_per_address_labels(BLUEPAGES_URL, addresses, chain, "bluepages_fyi")
    if rows:
        totals["wallet_labels"] = insert_wallet_labels(ch, redis, rows)
    logger.info(f"  → {len(rows)} wallet labels from bluepages.fyi")
    return totals
