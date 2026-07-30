"""TRON TRC-20 token deployer."""

from __future__ import annotations

import time

from app.core.logging import get_logger
from app.domains.tokens.deployer.base import ChainDeployer
from app.domains.tokens.deployer.models import DeployParams, TokenDeployment

logger = get_logger("darkroom_token_deployer.tron")


class TronDeployer(ChainDeployer):
    """Deploy TRC-20 tokens on TRON."""

    def __init__(self, private_key: str, rpc_url: str = "https://api.trongrid.io") -> None:
        from tronpy import Tron
        from tronpy.keys import PrivateKey

        self.client = Tron(network="mainnet" if "mainnet" in rpc_url else "shasta")
        self.private_key_obj = PrivateKey(bytes.fromhex(private_key.replace("0x", "")))
        super().__init__("tron", private_key, rpc_url)

    def _derive_address(self) -> str:
        return self.private_key_obj.public_key.to_base58check_address()

    async def deploy_token(self, params: DeployParams) -> TokenDeployment:
        try:
            deployment_id = self._generate_deployment_id("tron", params.symbol, str(time.time()))
            logger.warning("TRON deployment requires manual contract compilation.")
            deployment = TokenDeployment(
                deployment_id=deployment_id,
                chain="tron",
                name=params.name,
                symbol=params.symbol,
                decimals=params.decimals,
                total_supply=params.initial_supply,
                contract_address="",
                deployer_address=self.deployer_address,
                tx_hash="",
                status="pending",
                owner_address=params.owner_address or self.deployer_address,
                mintable=params.mintable,
                burnable=params.burnable,
                pausable=params.pausable,
                max_supply=params.max_supply,
                metadata_uri=params.metadata_uri,
                extra={"note": "TRON deployment requires manual contract compilation and deployment"},
            )
            return deployment
        except ValueError as e:
            logger.debug("deploy_token_error", exc_info=True)
            logger.error(f"TRON deploy failed: {e}")
            raise

    async def _call_tron(self, contract_address: str, fn: str, *args) -> str:
        """Helper for TRC-20 contract interactions."""
        contract = self.client.get_contract(contract_address)
        tx = getattr(contract.functions, fn)(*args)
        txb = tx.build().sign(self.private_key_obj)
        result = txb.broadcast()
        return result["txid"]

    async def mint_tokens(self, contract_address: str, to_address: str, amount: str) -> str:
        return await self._call_tron(contract_address, "mint", to_address, int(amount))

    async def burn_tokens(self, contract_address: str, amount: str) -> str:
        return await self._call_tron(contract_address, "burn", int(amount))

    async def transfer_ownership(self, contract_address: str, new_owner: str) -> str:
        return await self._call_tron(contract_address, "transferOwnership", new_owner)

    async def renounce_ownership(self, contract_address: str) -> str:
        return await self._call_tron(contract_address, "renounceOwnership")

    async def get_token_info(self, contract_address: str) -> dict:
        contract = self.client.get_contract(contract_address)
        return {
            "name": contract.functions.name().call(),
            "symbol": contract.functions.symbol().call(),
            "decimals": contract.functions.decimals().call(),
            "total_supply": str(contract.functions.totalSupply().call()),
            "owner": contract.functions.owner().call() if hasattr(contract.functions, "owner") else None,
        }

    async def get_balance(self, contract_address: str, wallet_address: str) -> str:
        contract = self.client.get_contract(contract_address)
        return str(contract.functions.balanceOf(wallet_address).call())

    async def blacklist_add(self, contract_address: str, address: str) -> str:
        return await self._call_tron(contract_address, "blacklist", address)

    async def blacklist_remove(self, contract_address: str, address: str) -> str:
        return await self._call_tron(contract_address, "unblacklist", address)

    async def is_blacklisted(self, contract_address: str, address: str) -> bool:
        contract = self.client.get_contract(contract_address)
        return contract.functions.isBlacklisted(address).call()

    async def set_trading_enabled(self, contract_address: str, enabled: bool) -> str:
        return await self._call_tron(contract_address, "setTradingEnabled", enabled)

    async def set_max_wallet(self, contract_address: str, max_amount: str) -> str:
        return await self._call_tron(contract_address, "setMaxWalletAmount", int(max_amount))

    async def set_max_tx(self, contract_address: str, max_amount: str) -> str:
        return await self._call_tron(contract_address, "setMaxTxAmount", int(max_amount))
