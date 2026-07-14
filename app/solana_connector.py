"""
Solana Integration - Using solana-py Client directly
=====================================================

Uses solana-py (the "solana" python package) for reliable Solana interactions.
This package provides a high-level synchronous API.
"""

from app.core.logging import get_logger
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = get_logger(__name__)

try:
    from solana.rpc.api import Client, Pubkey, Signature, Transaction  # noqa: F401

    SOLANA_AVAILABLE = True
    logger.info("solana-py SDK imported successfully")
except ImportError as e:
    logger.warning(f"solana-py SDK not available: {e}")
    SOLANA_AVAILABLE = False


# ─── RPC CONFIGURATION ────────────────────────────────────────────

SOLANA_RPC_URLS = {
    "mainnet": "https://solana-rpc.publicnode.com",
    "devnet": "https://api.devnet.solana.com",
    "testnet": "https://api.testnet.solana.com",
    "localnet": "http://localhost:8899",
}


# ─── DATA MODELS ──────────────────────────────────────────────────


@dataclass
class SolanaAccount:
    """Solana account data."""

    address: str
    lamports: int
    owner: str
    executable: bool
    rent_epoch: int
    data: str | None = None


@dataclass
class SolanaTransaction:
    """Solana transaction data."""

    signature: str
    slot: int
    block_time: int | None
    status: dict[str, Any] | None
    fee: int
    pre_balances: list[int]
    post_balances: list[int]


# ─── SOLANA CLIENT ────────────────────────────────────────────────


class SolanaClient:
    """High-level Solana client wrapper."""

    def __init__(self, network: str = "mainnet", rpc_url: str | None = None):
        self.network = network.lower()
        self.rpc_url = rpc_url or SOLANA_RPC_URLS.get(self.network, SOLANA_RPC_URLS["mainnet"])
        self._client: Any = None

        if not SOLANA_AVAILABLE:
            logger.warning("solana-py SDK not installed, using stub mode")

    @property
    def client(self) -> Any:
        """Get or create Client instance."""
        if not SOLANA_AVAILABLE:
            raise RuntimeError("solana-py SDK not installed")

        if self._client is None:
            self._client = Client(self.rpc_url)
        return self._client

    def get_account_info(self, address: str) -> SolanaAccount | None:
        """
        Get account information for an address.

        Args:
            address: Solana address (base58)

        Returns:
            SolanaAccount data or None if account doesn't exist
        """
        if not SOLANA_AVAILABLE:
            return self._stub_account(address)

        try:
            pubkey = Pubkey(address)
            response = self.client.get_account_info(pubkey)

            if response.value is None:
                return None

            info = response.value
            return SolanaAccount(
                address=address,
                lamports=info.lamports,
                owner=str(info.owner),
                executable=info.executable,
                rent_epoch=info.rent_epoch,
                data=info.data,
            )
        except Exception as e:
            logger.error(f"Error fetching account {address}: {e}")
            return self._stub_account(address)

    def get_balance(self, address: str) -> int:
        """Get account balance in lamports."""
        if not SOLANA_AVAILABLE:
            return 0

        try:
            pubkey = Pubkey(address)
            response = self.client.get_balance(pubkey)
            return response.value
        except Exception as e:
            logger.error(f"Error fetching balance for {address}: {e}")
            return 0

    def send_transaction(self, transaction, payer) -> str:
        """
        Send a Solana transaction.

        Args:
            transaction: Built transaction
            payer: Payer public key

        Returns:
            Transaction signature (base58)
        """
        if not SOLANA_AVAILABLE:
            return "stub_sig_" + datetime.utcnow().isoformat()

        try:
            response = self.client.send_transaction(transaction, payer)
            return str(response.value)
        except Exception as e:
            logger.error(f"Error sending transaction: {e}")
            raise

    def get_transaction(self, signature: str) -> SolanaTransaction | None:
        """
        Get transaction details by signature.

        Args:
            signature: Transaction signature (base58)

        Returns:
            SolanaTransaction data or None
        """
        if not SOLANA_AVAILABLE:
            return self._stub_transaction(signature)

        try:
            sig = Signature.from_string(signature)
            response = self.client.get_transaction(sig, encoding="jsonParsed")

            if response.value is None:
                return None

            tx = response.value
            meta = tx.meta
            return SolanaTransaction(
                signature=signature,
                slot=tx.slot,
                block_time=tx.block_time,
                status=meta,
                fee=meta.fee if meta else 0,
                pre_balances=meta.pre_balances if meta else [],
                post_balances=meta.post_balances if meta else [],
            )
        except Exception as e:
            logger.error(f"Error fetching transaction {signature}: {e}")
            return None

    def get_block_height(self) -> int:
        """Get current block height."""
        if not SOLANA_AVAILABLE:
            return 0
        try:
            return self.client.get_block_height()
        except Exception as e:
            logger.error(f"Error fetching block height: {e}")
            return 0

    def get_slot(self) -> int:
        """Get current slot."""
        if not SOLANA_AVAILABLE:
            return 0
        try:
            return self.client.get_slot()
        except Exception as e:
            logger.error(f"Error fetching slot: {e}")
            return 0

    def get_health(self) -> bool:
        """Check RPC health."""
        if not SOLANA_AVAILABLE:
            return False
        try:
            return self.client.get_health()
        except Exception:
            return False

    def _stub_account(self, address: str) -> SolanaAccount:
        """Stub account info when SDK unavailable."""
        return SolanaAccount(
            address=address,
            lamports=0,
            owner="11111111111111111111111111111111",
            executable=False,
            rent_epoch=0,
            data="",
        )

    def _stub_transaction(self, signature: str) -> SolanaTransaction:
        """Stub transaction data when SDK unavailable."""
        return SolanaTransaction(
            signature=signature,
            slot=0,
            block_time=None,
            status=None,
            fee=0,
            pre_balances=[],
            post_balances=[],
        )


# ─── GLOBAL SINGLETON ─────────────────────────────────────────────

_default_client: SolanaClient | None = None


def get_solana_client(network: str = "mainnet") -> SolanaClient:
    """Get or create Solana client instance."""
    global _default_client
    if _default_client is None:
        _default_client = SolanaClient(network=network)
    return _default_client


def check_solana_connection() -> bool:
    """Quick check if Solana RPC is accessible."""
    if not SOLANA_AVAILABLE:
        return False

    try:
        client = get_solana_client()
        return client.get_health()
    except Exception:
        return False
