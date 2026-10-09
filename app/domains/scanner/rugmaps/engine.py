"""
RugMaps engine — BubbleMapsPro implementation.

Generates interactive wallet connection graphs, fund-flow diagrams, and cluster maps.
Split into:
  - engine.py       → thin re-export + BubbleMapsPro class
  - graph.py        → graph computation helpers (connections, positions, risk)
  - html_export.py  → interactive HTML export
  - models.py       → data models
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, ClassVar

import httpx

from app.config import settings
from app.domains.scanner.engines._logging import get_logger
from app.domains.scanner.rugmaps.graph import (
    add_center_node,
    add_or_update_link,
    add_or_update_node,
    calculate_connection_strength,
    calculate_positions,
    calculate_risk,
    calculate_stats,
    classify_wallet,
    get_entity_info,
)
from app.domains.scanner.rugmaps.html_export import _generate_interactive_html, export_html
from app.domains.scanner.rugmaps.models import BubbleMap

logger = get_logger(__name__)


class BubbleMapsPro:
    """
    Professional-grade bubble map generation.
    Fixes all competitor flaws.
    """

    TYPE_COLORS: ClassVar[dict[str, Any]] = {
        "center": "#ff6b6b",
        "scammer": "#ff0000",
        "suspected_scammer": "#ff6b6b",
        "exchange": "#4dabf7",
        "whale": "#ffd43b",
        "bot": "#9775fa",
        "kol": "#69db7c",
        "dev": "#ff8787",
        "unknown": "#868e96",
    }

    RISK_COLORS: ClassVar[dict[str, Any]] = {
        "safe": "#00ff00",
        "low": "#90ee90",
        "medium": "#ffd700",
        "high": "#ff6b6b",
        "critical": "#ff0000",
    }

    def __init__(self) -> None:
        self.transaction_cache: dict[str, list[dict[str, Any]]] = {}
        self.entity_cache: dict[str, dict[str, Any]] = {}

    async def generate_map(
        self,
        center_wallet: str,
        depth: int = 2,
        min_strength: float = 0.1,
        time_range: tuple[datetime, datetime] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> BubbleMap:
        """Generate a professional bubble map."""
        map_id = f"bubble_{center_wallet[:12]}_{int(datetime.now().timestamp())}"
        bubble_map = BubbleMap(
            map_id=map_id,
            center_wallet=center_wallet,
            depth=depth,
            min_strength=min_strength,
            created_at=datetime.now(),
        )

        # Fetch transactions at each depth level
        wallets_to_process = [(center_wallet, 0)]
        processed = set()
        all_transactions: dict[str, list[dict[str, Any]]] = {}

        while wallets_to_process:
            current_wallet, current_depth = wallets_to_process.pop(0)
            if current_wallet in processed or current_depth > depth:
                continue
            processed.add(current_wallet)

            txs = await self._get_transactions(current_wallet, center_wallet, current_depth)
            all_transactions[current_wallet] = txs

            if current_wallet == center_wallet:
                await add_center_node(bubble_map, current_wallet, txs)
            else:
                node_type = await classify_wallet(current_wallet, txs)
                await add_or_update_node(bubble_map, current_wallet, current_wallet[:12], node_type, current_depth, txs)

            # Extract connected wallets for next depth
            if current_depth < depth:
                connected = self._extract_connected_wallets(txs, current_wallet, center_wallet)
                for addr in connected:
                    if addr not in processed:
                        wallets_to_process.append((addr, current_depth + 1))

        # Build connections between nodes
        for wallet_addr, txs in all_transactions.items():
            for tx in txs:
                counterparty = tx.get("to", "")
                if counterparty and counterparty in processed:
                    strength = calculate_connection_strength(1, abs(float(tx.get("value", 0))), 1, 1)
                    if strength >= min_strength:
                        add_or_update_link(bubble_map, wallet_addr, counterparty, strength, "fund_flow")

        calculate_positions(bubble_map)
        bubble_map.stats = calculate_stats(bubble_map)
        return bubble_map

    async def _get_entity_info(self, address: str) -> dict[str, Any] | None:
        """Get entity info for a wallet (label graph → on-chain clustering).

        Restored thin wrapper over ``graph.get_entity_info`` so callers
        (``rugmaps_router`` /entity, ``rugmaps_ai.get_wallet_labels``) have
        a stable method on the engine instead of a dead reference.
        """
        return await get_entity_info(address)

    def _calculate_risk(self, transactions: list[dict[str, Any]], node_count: int = 1) -> tuple[float, str]:
        """Calculate risk score and level for a wallet's transactions.

        Restored thin wrapper over ``graph.calculate_risk``.
        """
        return calculate_risk(transactions, node_count)

    async def _get_transactions(
        self, address: str, center_address: str | None = None, depth: int = 1
    ) -> list[dict[str, Any]]:
        """Fetch transactions for a wallet address."""
        if address in self.transaction_cache:
            return self.transaction_cache[address]

        api_keys: dict[str, str] = {}
        for chain in ["ethereum", "solana", "bsc", "polygon"]:
            key = getattr(settings, f"{chain.upper()}_API_KEY", "") or os.getenv(f"{chain.upper()}_API_KEY", "")
            if key:
                api_keys[chain] = key

        txs: list[dict[str, Any]] = []
        if address.startswith("0x"):
            txs = await self._fetch_evm_transactions(address, api_keys)
        elif len(address) > 30:
            txs = await self._fetch_solana_transactions(address, api_keys)

        self.transaction_cache[address] = txs
        return txs

    async def _get_transaction_detail(self, signature: str, api_key: str) -> dict[str, Any] | None:
        """Fetch detailed transaction information."""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    "https://mainnet.helius-rpc.com/v0/transactions",
                    params={"apiKey": api_key},
                    json={"transactions": [signature]},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list) and len(data) > 0:
                        return data[0]
        except Exception as e:
            logger.debug("Failed to fetch tx detail %s: %s", signature[:12], e)
        return None

    def _extract_connected_wallets(
        self, txs: list[dict[str, Any]], current_wallet: str, center_wallet: str
    ) -> set[str]:
        """Extract unique connected wallet addresses from transactions."""
        connected = set()
        for tx in txs:
            for addr in [tx.get("from", ""), tx.get("to", "")]:
                if addr and addr.lower() != current_wallet.lower() and addr.lower() != center_wallet.lower():
                    connected.add(addr)
        return connected

    async def _fetch_evm_transactions(self, address: str, api_keys: dict[str, Any]) -> list[dict[str, Any]]:
        """Fetch EVM transactions via Etherscan API."""
        api_key = api_keys.get("ethereum", "")
        if not api_key:
            return []
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    "https://api.etherscan.io/api",
                    params={
                        "module": "account",
                        "action": "txlist",
                        "address": address,
                        "startblock": 0,
                        "endblock": 99999999,
                        "sort": "desc",
                        "apikey": api_key,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("status") == "1" and isinstance(data.get("result"), list):
                        return data["result"][:50]
        except Exception as e:
            logger.debug("EVM tx fetch error for %s: %s", address[:12], e)
        return []

    async def _fetch_solana_transactions(self, address: str, api_keys: dict[str, Any]) -> list[dict[str, Any]]:
        """Fetch Solana transactions via Helius API."""
        api_key = api_keys.get("solana", "")
        if not api_key:
            return []
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"https://mainnet.helius-rpc.com/v0/addresses/{address}/transactions",
                    params={"apiKey": api_key, "limit": 50},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list):
                        return data
        except Exception as e:
            logger.debug("Solana tx fetch error for %s: %s", address[:12], e)
        return []

    export_html = export_html

    def _generate_interactive_html(self, bubble_map: BubbleMap) -> str:
        return _generate_interactive_html(bubble_map)


# Global instance
_bubble_pro = None


def get_bubble_maps_pro() -> BubbleMapsPro:
    """Get global BubbleMapsPro instance."""
    global _bubble_pro
    if _bubble_pro is None:
        _bubble_pro = BubbleMapsPro()
    return _bubble_pro
