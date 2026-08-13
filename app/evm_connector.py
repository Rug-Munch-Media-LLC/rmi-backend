"""
Web3.py Integration - EVM Blockchain Connectors
===============================================

Web3 integration with Infura/Alchemy/Local node support.
Includes:
- Contract reading (view/pure functions)
- Event filtering
- Gas estimation
- Transaction signing (via wallet)
- Chain abstraction (Ethereum, Base, Arbitrum, etc.)
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

try:
    from web3 import HTTPProvider, Web3

    try:
        from web3.middleware import geth_poa_middleware  # type: ignore[attr-defined]  # web3 < v7
    except ImportError:
        from web3.middleware import ExtraDataToPOAMiddleware as geth_poa_middleware  # web3 v7+
    from web3.types import (  # type: ignore[attr-defined]  # BlockNumber removed in web3 v7
        BlockNumber,
        FilterParams,
        TxParams,
        Wei,
    )

    WEB3_AVAILABLE = True
except ImportError:
    logger.warning("web3.py not installed, using stub mode")
    WEB3_AVAILABLE = False
    Web3 = None  # type: ignore[assignment,misc]  # stub mode: shadow imported type
    HTTPProvider = None  # type: ignore[assignment,misc]
    geth_poa_middleware = None
    TxParams = dict  # type: ignore[assignment,misc]
    Wei = int  # type: ignore[assignment,misc]
    BlockNumber = int  # type: ignore[assignment,misc]
    FilterParams = dict  # type: ignore[assignment,misc]

# ─── CHAIN CONFIGURATIONS ─────────────────────────────────────────

CHAIN_CONFIGS: dict[str, dict[str, Any]] = {
    "ethereum": {
        "rpc": "https://ethereum-rpc.publicnode.com",
        "chain_id": 1,
        "native_symbol": "ETH",
        "decimals": 18,
        "poa": False,  # Proof of Authority chains need middleware
    },
    "base": {
        "rpc": "https://base-rpc.publicnode.com",
        "chain_id": 8453,
        "native_symbol": "ETH",
        "decimals": 18,
        "poa": False,
    },
    "arbitrum": {
        "rpc": "https://arbitrum-rpc.publicnode.com",
        "chain_id": 42161,
        "native_symbol": "ETH",
        "decimals": 18,
        "poa": False,
    },
    "optimism": {
        "rpc": "https://optimism-rpc.publicnode.com",
        "chain_id": 10,
        "native_symbol": "ETH",
        "decimals": 18,
        "poa": False,
    },
    "polygon": {
        "rpc": "https://polygon-rpc.publicnode.com",
        "chain_id": 137,
        "native_symbol": "MATIC",
        "decimals": 18,
        "poa": True,
    },
    "bsc": {
        "rpc": "https://bsc-rpc.publicnode.com",
        "chain_id": 56,
        "native_symbol": "BNB",
        "decimals": 18,
        "poa": True,
    },
    "avalanche": {
        "rpc": "https://avalanche-c-chain-rpc.publicnode.com",
        "chain_id": 43114,
        "native_symbol": "AVAX",
        "decimals": 18,
        "poa": False,
    },
    "fantom": {
        "rpc": "https://fantom-rpc.publicnode.com",
        "chain_id": 250,
        "native_symbol": "FTM",
        "decimals": 18,
        "poa": True,
    },
}

# ─── MORALIS API KEY ──────────────────────────────────────────────

MORALIS_API_KEY = ""
try:
    with open("/root/.secrets/moralis_api_key") as f:
        MORALIS_API_KEY = f.read().strip()
except (ConnectionError, TimeoutError, httpx.HTTPError):
    logger.warning("Moralis API key not found at /root/.secrets/moralis_api_key")

# ─── MORALIS RPC ENDPOINTS ────────────────────────────────────────

MORALIS_RPC = {
    "ethereum": "https://deep-index.moralis.io/api/v2.2/eth/mainnet",
    "base": "https://deep-index.moralis.io/api/v2.2/base/mainnet",
    "arbitrum": "https://deep-index.moralis.io/api/v2.2/arbitrum/mainnet",
    "optimism": "https://deep-index.moralis.io/api/v2.2/optimism/mainnet",
    "polygon": "https://deep-index.moralis.io/api/v2.2/polygon/mainnet",
    "bsc": "https://deep-index.moralis.io/api/v2.2/bsc/mainnet",
    "avalanche": "https://deep-index.moralis.io/api/v2.2/avalanche/mainnet",
    "fantom": "https://deep-index.moralis.io/api/v2.2/fantom/mainnet",
}

# ─── WEB3 CLIENT ──────────────────────────────────────────────────


class EVMClient:
    """
    EVM blockchain client with chain abstraction.
    Can read contracts, fetch events, estimate gas.
    """

    def __init__(self, chain: str = "base", rpc_url: str | None = None, use_moralis: bool = False) -> None:
        self.chain = chain.lower()
        self.config = CHAIN_CONFIGS.get(self.chain, CHAIN_CONFIGS["base"])
        self._w3: Web3 | None = None
        self._use_moralis = use_moralis

        # Use custom RPC if provided, otherwise use chain default or moralis
        if rpc_url:
            self.rpc_url = rpc_url
        elif use_moralis and MORALIS_API_KEY:
            self.rpc_url = MORALIS_RPC.get(self.chain, self.config["rpc"])
        else:
            self.rpc_url = self.config["rpc"]

    @property
    def w3(self) -> Web3:
        """Get or create Web3 instance."""
        if self._w3 is None:
            if not WEB3_AVAILABLE:
                raise RuntimeError("web3.py not installed")

            # Build provider with optional Moralis auth header
            if self._use_moralis and MORALIS_API_KEY:
                provider = HTTPProvider(self.rpc_url, request_kwargs={"headers": {"X-API-Key": MORALIS_API_KEY}})
            else:
                provider = HTTPProvider(self.rpc_url)

            self._w3 = Web3(provider)

            # Add POA middleware for PoA chains
            if self.config.get("poa"):
                self._w3.middleware_onion.inject(geth_poa_middleware, layer=0)

            logger.info(f"Connected to {self.chain}: {self._w3.eth.chain_id}")

        return self._w3

    def is_connected(self) -> bool:
        """Check if blockchain is reachable."""
        try:
            return self.w3.is_connected()
        except (ConnectionError, OSError, TimeoutError):
            return False

    def get_block_number(self) -> int:
        """Get latest block number."""
        return self.w3.eth.block_number

    def get_gas_price(self) -> int:
        """Get current gas price in wei."""
        return self.w3.eth.gas_price

    def get_balance(self, address: str, block: int | None = None) -> int:
        """Get ETH balance of address in wei."""
        address = self.w3.to_checksum_address(address)
        if block is None:
            block = "latest"  # type: ignore[assignment]  # web3 accepts block tag str at runtime
        return self.w3.eth.get_balance(address, block)

    def get_balance_ether(self, address: str) -> float:
        """Get ETH balance in ether (wei * 10^18)."""
        wei = self.get_balance(address)
        return float(self.w3.from_wei(wei, "ether"))

    def get_nonce(self, address: str, block: int | None = None) -> int:
        """Get account nonce."""
        address = self.w3.to_checksum_address(address)
        if block is None:
            block = "latest"  # type: ignore[assignment]  # web3 accepts block tag str at runtime
        return self.w3.eth.get_transaction_count(address, block)

    def get_block(self, block_number: int) -> dict[str, Any] | None:
        """Get block details."""
        try:
            return self.w3.eth.get_block(block_number)  # type: ignore[return-value]  # web3 v7 BlockData is dict-like
        except Exception as e:
            logger.error(f"Error fetching block {block_number}: {e}")
            return None

    def get_transaction(self, tx_hash: str) -> dict[str, Any] | None:
        """Get transaction details."""
        try:
            return self.w3.eth.get_transaction(tx_hash)  # type: ignore[arg-type,return-value]  # web3 v7 TxData is dict-like
        except Exception as e:
            logger.error(f"Error fetching tx {tx_hash}: {e}")
            return None

    def estimate_gas(self, tx_params: TxParams) -> int:
        """Estimate gas for transaction."""
        return self.w3.eth.estimate_gas(tx_params)

    def get_contract(self, address: str, abi: list[dict[str, Any]]) -> Any:
        """
        Get Web3 contract instance.

        Args:
            address: Contract address
            abi: Contract ABI as list of dicts

        Returns:
            Web3 contract instance
        """
        address = self.w3.to_checksum_address(address)
        return self.w3.eth.contract(address=address, abi=abi)

    def get_events(
        self, contract: Any, event_name: str, from_block: int = 0, to_block: int | None = None
    ) -> list[dict[str, Any]]:
        """
        Get historical events from a contract.

        Args:
            contract: Web3 contract instance
            event_name: Name of event to filter
            from_block: Starting block
            to_block: Ending block (default: latest)

        Returns:
            List of event logs
        """
        if to_block is None:
            to_block = self.get_block_number()

        event = getattr(contract.events, event_name)
        filter_params = event.create_filter(from_block=from_block, to_block=to_block)
        events = filter_params.get_all_entries()

        return [self._decode_event(e) for e in events]

    def _decode_event(self, event: Any) -> dict[str, Any]:
        """Decode event log to dict."""
        return {
            "event": event.event,
            "args": dict(event.args),
            "block_number": event.blockNumber,
            "transaction_hash": event.transactionHash.hex(),
            "log_index": event.logIndex,
        }

    def call_contract_function(self, contract: Any, function_name: str, *args, **kwargs) -> Any:
        """
        Call a view/pure contract function.

        Args:
            contract: Web3 contract instance
            function_name: Name of function to call
            *args: Function arguments
            **kwargs: Additional params (block, from_block)

        Returns:
            Function return value
        """
        func = getattr(contract.functions, function_name)
        return func(*args, **kwargs).call()

    def parse_address(self, address: str) -> str:
        """Parse and normalize address."""
        return self.w3.to_checksum_address(address)

    def to_wei(self, amount: int | float | str, unit: str = "ether") -> int:
        """Convert to wei."""
        return self.w3.to_wei(amount, unit)

    def from_wei(self, amount: int, unit: str = "ether") -> float:
        """Convert from wei."""
        return float(self.w3.from_wei(amount, unit))


# ─── CLIENT MANAGER ───────────────────────────────────────────────


class ClientManager:
    """Manages multiple EVM clients (one per chain)."""

    def __init__(self) -> None:
        self.clients: dict[str, EVMClient] = {}

    def get_client(self, chain: str = "base", rpc_url: str | None = None, use_moralis: bool = False) -> EVMClient:
        """Get or create client for chain."""
        key = f"{chain}:{rpc_url or 'default'}:{use_moralis}"
        if key not in self.clients:
            self.clients[key] = EVMClient(chain=chain, rpc_url=rpc_url, use_moralis=use_moralis)
        return self.clients[key]

    def clear(self) -> None:
        """Clear all cached clients."""
        self.clients.clear()


# Global singleton
_client_manager = ClientManager()


def get_evm_client(chain: str = "base", rpc_url: str | None = None, use_moralis: bool = False) -> EVMClient:
    """Get global EVM client."""
    return _client_manager.get_client(chain, rpc_url, use_moralis)


# ─── CIRCUIT BREAKER ──────────────────────────────────────────────
# Prevents cascading failures when RPC endpoints are degraded.
# Pattern: 5 failures → open, 30s recovery timeout, half-open probe.
# See app.core.circuit_breaker for the implementation.

from app.core.circuit_breaker import AsyncCircuitBreaker  # noqa: E402 — module docstring precedes import block

_evm_rpc_cb = AsyncCircuitBreaker(
    name="evm_rpc",
    failure_threshold=5,
    failure_window=60.0,
    recovery_timeout=30.0,
    half_open_max=1,
)
