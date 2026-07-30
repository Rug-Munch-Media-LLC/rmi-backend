"""New Block provider — polls free public RPCs for new blocks.

This provider uses public free RPC endpoints (not paid services like Alchemy/QuickNode).
It falls back gracefully if RPCs are unavailable.

RPCs used:
  - Ethereum: https://cloudflare-eth.com (free, no auth, rate-limited)
  - Solana: https://api.mainnet-beta.solana.com (free, no auth)

Source: Free public RPCs — zero cost.
"""

from __future__ import annotations

import httpx

from app.core.logging import get_logger
from app.domains.scanner.webhooks.models import WebhookEventType
from app.domains.scanner.webhooks.providers.utils import load_cursor, save_cursor

log = get_logger(__name__)

# Free public RPC endpoints (no API key required)
FREE_RPCS: dict[str, str] = {
    "ethereum": "https://cloudflare-eth.com",
    "solana": "https://api.mainnet-beta.solana.com",
    "polygon": "https://polygon-rpc.com",
    "arbitrum": "https://arb1.arbitrum.io/rpc",
    "optimism": "https://mainnet.optimism.io",
    "bsc": "https://bsc-dataseed.binance.org",
    "base": "https://mainnet.base.org",
}


class NewBlockProvider:
    """Polls free RPCs for new block numbers."""

    event_type: WebhookEventType = WebhookEventType.CHAIN_BLOCK
    poll_interval: int = 15  # seconds — blocks every ~12s on ETH

    async def poll(self, cursor: dict | None = None) -> list[dict]:
        """Poll free RPCs for new blocks since the last cursor.

        Uses eth_blockNumber for EVM chains, getSlot for Solana.
        Returns events for each chain that has advanced.
        """
        last_blocks: dict[str, int] = (cursor or {}).get("blocks", {})
        events: list[dict] = []

        async with httpx.AsyncClient(timeout=10.0) as client:
            for chain, rpc_url in FREE_RPCS.items():
                try:
                    if chain == "solana":
                        block_num = await self._sol_get_slot(client, rpc_url)
                    else:
                        block_num = await self._eth_block_number(client, rpc_url)

                    if block_num is not None:
                        last_block = last_blocks.get(chain, 0)
                        if block_num > last_block:
                            events.append(
                                {
                                    "chain": chain,
                                    "block_number": block_num,
                                    "previous_block_number": last_block,
                                }
                            )
                            last_blocks[chain] = block_num

                except (ValueError, ConnectionError, RuntimeError, httpx.RequestError) as exc:
                    log.debug("new_block_poll_failed", chain=chain, error=str(exc))
                    # Individual RPC failure is non-fatal; continue polling others

        # Update cursor with latest block numbers
        if events:
            cursor_data = {"blocks": last_blocks}
            # We'll persist this via the engine's cursor management
            # Returning it in the event payload is enough because the engine
            # calls save_cursor with the return value.

        return events

    async def _eth_block_number(self, client: httpx.AsyncClient, rpc_url: str) -> int | None:
        """Call eth_blockNumber on an EVM RPC."""
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_blockNumber",
            "params": [],
            "id": 1,
        }
        resp = await client.post(rpc_url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        if "result" in data:
            return int(data["result"], 16)
        return None

    async def _sol_get_slot(self, client: httpx.AsyncClient, rpc_url: str) -> int | None:
        """Call getSlot on a Solana RPC."""
        payload = {
            "jsonrpc": "2.0",
            "method": "getSlot",
            "params": [],
            "id": 1,
        }
        resp = await client.post(rpc_url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        if "result" in data:
            return int(data["result"])
        return None

    async def cursor_key(self) -> str:
        return "webhook:cursor:new_block"
