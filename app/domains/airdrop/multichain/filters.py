"""Anti-gaming filters and cross-chain identity resolution."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.domains.airdrop.multichain.models import CrossChainHolder

logger = get_logger("darkroom_multichain_airdrop")


class AntiGamingFilter:
    """
    Filter out sybils, bots, and gaming attempts.
    """

    KNOWN_BOT_PATTERNS = [
        "0x0000000000000000000000000000000000000000",
        "0x000000000000000000000000000000000000dead",
    ]

    @staticmethod
    async def filter_holders(
        holders: dict[str, CrossChainHolder],
        exclude_contracts: bool = True,
        exclude_known_bots: bool = True,
        min_wallet_age_days: int | None = None,
        min_tx_count: int | None = None,
    ) -> dict[str, CrossChainHolder]:
        """Apply all anti-gaming filters."""
        filtered = {}

        for addr, holder in holders.items():
            # Skip known bots
            if exclude_known_bots and addr.lower() in AntiGamingFilter.KNOWN_BOT_PATTERNS:
                continue

            # Skip contracts (would need bytecode check - simplified)
            if exclude_contracts:
                # In production, check if address has code
                pass

            # Skip young wallets
            if (
                min_wallet_age_days
                and holder.wallet_age_days is not None
                and holder.wallet_age_days < min_wallet_age_days
            ):
                continue

            # Skip low-activity wallets
            if min_tx_count and holder.tx_count_30d < min_tx_count:
                continue

            filtered[addr] = holder

        logger.info(f"Anti-gaming filter: {len(holders)} -> {len(filtered)} holders")
        return filtered

    @staticmethod
    async def detect_sybils(holders: dict[str, CrossChainHolder]) -> list[str]:
        """
        Detect potential sybil wallets (same owner, multiple addresses).
        Simple heuristics: similar balances, same first funding, etc.
        """
        # In production, use clustering analysis
        # For now, return empty list
        return []


class CrossChainIdentityResolver:
    """
    Link the same user's addresses across chains.

    Methods:
      1. Self-reported linking (user signs message proving ownership)
      2. Common funding source (same wallet funded both addresses)
      3. ENS/SNS resolution
      4. Platform binding (Twitter, Discord, etc.)
    """

    @staticmethod
    async def resolve_linked_addresses(
        primary_address: str,
        chain: str,
    ) -> dict[str, str]:
        """
        Find linked addresses for a wallet.
        Returns {chain: address} mapping.
        """
        # Check Redis for stored linkages
        try:
            import redis.asyncio as redis_lib

            r = redis_lib.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
            )

            key = f"wallet_links:{primary_address}:{chain}"
            data = await r.get(key)
            if data:
                return json.loads(data)
        except (ConnectionError, OSError, json.JSONDecodeError):
            pass

        return {}

    @staticmethod
    async def store_linkage(
        primary_address: str,
        primary_chain: str,
        linked_chain: str,
        linked_address: str,
        proof_signature: str,
    ) -> bool:
        """Store verified cross-chain address linkage."""
        try:
            import redis.asyncio as redis_lib

            r = redis_lib.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
            )

            key = f"wallet_links:{primary_address}:{primary_chain}"
            existing = {}
            data = await r.get(key)
            if data:
                existing = json.loads(data)

            existing[linked_chain] = linked_address
            await r.set(key, json.dumps(existing))

            # Also store reverse mapping
            reverse_key = f"wallet_links:{linked_address}:{linked_chain}"
            reverse_data = {primary_chain: primary_address}
            await r.set(reverse_key, json.dumps(reverse_data))

            return True
        except (json.JSONDecodeError, ConnectionError, OSError, ValueError, KeyError, TypeError) as e:
            logger.debug("store_linkage_error", exc_info=True)
            logger.error(f"Store linkage failed: {e}")
            return False
