"""Abstract base class for all chain deployers."""

from __future__ import annotations

import hashlib
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.domains.tokens.deployer.models import DeployParams, TokenDeployment


class ChainDeployer(ABC):
    """Abstract base for all chain deployers."""

    def __init__(self, chain: str, private_key: str, rpc_url: str) -> None:
        self.chain = chain
        self.private_key = private_key
        self.rpc_url = rpc_url
        self.deployer_address = self._derive_address()

    @abstractmethod
    def _derive_address(self) -> str:
        """Derive deployer wallet address from private key."""

    @abstractmethod
    async def deploy_token(self, params: DeployParams) -> TokenDeployment:
        """Deploy a new token contract."""

    @abstractmethod
    async def mint_tokens(self, contract_address: str, to_address: str, amount: str) -> str:
        """Mint additional tokens. Returns tx hash."""

    @abstractmethod
    async def burn_tokens(self, contract_address: str, amount: str) -> str:
        """Burn tokens from deployer balance. Returns tx hash."""

    @abstractmethod
    async def transfer_ownership(self, contract_address: str, new_owner: str) -> str:
        """Transfer contract ownership. Returns tx hash."""

    @abstractmethod
    async def renounce_ownership(self, contract_address: str) -> str:
        """Renounce contract ownership. Returns tx hash."""

    @abstractmethod
    async def get_token_info(self, contract_address: str) -> dict[str, Any]:
        """Get token metadata from contract."""

    @abstractmethod
    async def get_balance(self, contract_address: str, wallet_address: str) -> str:
        """Get token balance for a wallet."""

    @abstractmethod
    async def blacklist_add(self, contract_address: str, address: str) -> str:
        """Add an address to the token blacklist. Returns tx hash."""

    @abstractmethod
    async def blacklist_remove(self, contract_address: str, address: str) -> str:
        """Remove an address from the token blacklist. Returns tx hash."""

    @abstractmethod
    async def is_blacklisted(self, contract_address: str, address: str) -> bool:
        """Check if an address is blacklisted."""

    @abstractmethod
    async def set_trading_enabled(self, contract_address: str, enabled: bool) -> str:
        """Enable or disable trading. Returns tx hash."""

    @abstractmethod
    async def set_max_wallet(self, contract_address: str, max_amount: str) -> str:
        """Set max wallet holding limit. Returns tx hash."""

    @abstractmethod
    async def set_max_tx(self, contract_address: str, max_amount: str) -> str:
        """Set max transaction limit. Returns tx hash."""

    def _generate_deployment_id(self, chain: str, symbol: str, tx_hash: str) -> str:
        """Generate unique deployment ID."""
        raw = f"{chain}:{symbol}:{tx_hash}:{time.time()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
