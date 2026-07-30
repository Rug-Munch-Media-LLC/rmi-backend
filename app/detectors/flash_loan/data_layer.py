"""
Blockchain Data Layer for Flash Loan Attack Detector
=====================================================
RPC communication, transaction fetching, block scanning, and response
parsing. Provides the data acquisition layer for the detector.
"""

from __future__ import annotations

import json as json_module
import os
import urllib.request
from typing import Any

from app.core.logging import get_logger
from app.detectors.flash_loan.models import TransactionTrace

logger = get_logger(__name__)


class BlockchainDataLayer:
    """Handles all blockchain RPC communication for the detector.

    Abstracts away RPC endpoint configuration, HTTP transport, and
    response parsing. Supports any EVM-compatible chain.
    """

    def __init__(
        self,
        rpc_endpoints: dict[str, str] | None = None,
    ) -> None:
        """Initialize the data layer.

        Args:
            rpc_endpoints: Custom RPC endpoints per chain (optional)
        """
        self.rpc_endpoints = rpc_endpoints or {}

    async def fetch_recent_blocks(
        self,
        chain: str,
        blocks_back: int,
    ) -> list[int]:
        """Fetch recent block numbers for a given chain.

        Args:
            chain: Chain name
            blocks_back: Number of recent blocks to fetch

        Returns:
            List of block numbers (most recent first)
        """
        try:
            latest = await self._rpc_call(chain, "eth_blockNumber", [])
            if latest:
                latest_num = int(latest, 16)
                start = max(latest_num - blocks_back, 0)
                return list(range(latest_num, start - 1, -1))[:blocks_back]
        except Exception as e:
            logger.warning("Failed to fetch latest block for %s: %s", chain, e)

        return []

    async def fetch_block_transactions(
        self,
        chain: str,
        block_number: int,
    ) -> list[TransactionTrace]:
        """Fetch all transactions in a given block.

        Args:
            chain: Chain name
            block_number: Block number to fetch

        Returns:
            List of TransactionTrace objects
        """
        try:
            block_data = await self._rpc_call(chain, "eth_getBlockByNumber", [hex(block_number), True])
            if block_data and "transactions" in block_data:
                txs = []
                for tx_data in block_data["transactions"]:
                    tx = self._parse_tx_data(tx_data, chain)
                    if tx:
                        txs.append(tx)
                return txs
        except Exception as e:
            logger.warning(
                "Failed to fetch block %d on %s: %s",
                block_number,
                chain,
                e,
            )
        return []

    async def fetch_transaction(
        self,
        chain: str,
        tx_hash: str,
    ) -> TransactionTrace | None:
        """Fetch a specific transaction by hash.

        Args:
            chain: Chain name
            tx_hash: Transaction hash to fetch

        Returns:
            TransactionTrace if found, None otherwise
        """
        try:
            tx_data = await self._rpc_call(chain, "eth_getTransactionByHash", [tx_hash])
            if tx_data:
                return self._parse_tx_data(tx_data, chain)

            # Try receipt endpoint as fallback
            receipt = await self._rpc_call(chain, "eth_getTransactionReceipt", [tx_hash])
            if receipt:
                tx_data = {"hash": tx_hash, **receipt}
                return self._parse_tx_data(tx_data, chain)
        except Exception as e:
            logger.warning("Failed to fetch tx %s on %s: %s", tx_hash, chain, e)
        return None

    def _parse_tx_data(self, data: dict, chain: str) -> TransactionTrace | None:
        """Parse raw RPC response into a TransactionTrace.

        Args:
            data: Raw transaction data from RPC call
            chain: Chain identifier

        Returns:
            Parsed TransactionTrace or None
        """
        tx_hash = data.get("hash", "")
        if not tx_hash:
            return None

        return TransactionTrace(
            tx_hash=tx_hash if tx_hash.startswith("0x") else f"0x{tx_hash}",
            chain=chain,
            block_number=int(data.get("blockNumber", "0x0"), 16),
            tx_index=int(data.get("transactionIndex", "0x0"), 16),
            from_address=data.get("from", ""),
            to_address=data.get("to", ""),
            value_eth=int(data.get("value", "0x0"), 16) / 1e18,
            gas_price_gwei=int(data.get("gasPrice", "0x0"), 16) / 1e9,
            gas_used=int(data.get("gas", "0x0"), 16),
            input_data=data.get("input", ""),
            internal_calls=data.get("internal_calls", []),
            logs=data.get("logs", []),
        )

    async def _rpc_call(
        self,
        chain: str,
        method: str,
        params: list[Any],
    ) -> Any:
        """Make a JSON-RPC call to the configured endpoint for a chain.

        Args:
            chain: Chain identifier
            method: JSON-RPC method name
            params: RPC parameters

        Returns:
            RPC response result, or None on failure
        """
        endpoint = self.rpc_endpoints.get(chain)
        if not endpoint:
            env_var = f"RPC_{chain.upper()}"
            endpoint_ = os.environ.get(env_var, "")
            if not endpoint_:
                logger.debug("No RPC endpoint configured for %s", chain)
                return None
            endpoint = endpoint_

        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": 1,
        }

        try:
            result = await self._http_post(endpoint, payload)
            if result and "result" in result:
                return result["result"]
            elif result and "error" in result:
                logger.warning("RPC error on %s: %s", chain, result["error"].get("message", "unknown"))
        except Exception as e:
            logger.warning("RPC call failed for %s: %s", chain, e)

        return None

    async def _http_post(self, url: str, payload: dict) -> dict[str, Any] | None:
        """Make an async HTTP POST request.

        Attempts httpx first, falls back to urllib.

        Args:
            url: Target URL
            payload: JSON body

        Returns:
            Parsed response dict or None
        """
        try:
            import httpx  # type: ignore[import-untyped]

            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(url, json=payload)
                return response.json()
        except ImportError:
            logger.debug("httpx not available, trying urllib fallback via thread")
            return await asyncio.to_thread(self._http_post_sync, url, payload)
        except Exception as e:
            logger.debug("HTTP request failed: %s", e)
            return None

    def _http_post_sync(self, url: str, payload: dict) -> dict[str, Any] | None:
        """Synchronous HTTP POST fallback via urllib.

        Args:
            url: Target URL
            payload: JSON body

        Returns:
            Parsed response dict or None
        """
        try:
            data = json_module.dumps(payload).encode()
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json_module.loads(resp.read())
        except Exception as e:
            logger.debug("Sync HTTP fallback failed: %s", e)
            return None
