"""
Wallet Labels — label/tag management and legacy wallet importing.

Extracted from manager.py during god-file split (2026-07-23).
"""

from __future__ import annotations

import json
import os
import secrets
import time
from datetime import UTC, datetime

import httpx

from app.core.logging import get_logger
from app.domains.wallet.models import (
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


__all__ = ["import_legacy_wallets"]
