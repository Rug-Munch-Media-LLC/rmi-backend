"""EVM token deployer (Ethereum, Base, BSC)."""

from __future__ import annotations

import os
from typing import Any, cast

from eth_typing import ChecksumAddress, HexStr
from web3.types import TxParams

from app.core.logging import get_logger
from app.domains.tokens.deployer.base import ChainDeployer
from app.domains.tokens.deployer.models import DeployParams, TokenDeployment

logger = get_logger("darkroom_token_deployer.evm")


class EVMDeployer(ChainDeployer):
    """Deploy ERC-20 tokens on EVM chains (Ethereum, Base, BSC)."""

    ERC20_BYTECODE = "0x" + (
        "608060405234801561001057600080fd5b50604051610d82380380610d82833981016040819052"
        "61002f9161007d565b600380546001600160a01b03191633179055604051819061005090610070"
        "565b6001600160a01b039091168152602001604051809103906000f08015801561007a573d6000"
        "803e3d6000fd5b50506100a9565b6000806040838503121561009057600080fd5b505080516020"
        "909101519092909150565b610cc5806100b86000396000f3fe"
    )

    def __init__(self, chain: str, private_key: str, rpc_url: str) -> None:
        from eth_account import Account
        from web3 import Web3

        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.account = Account.from_key(private_key)
        super().__init__(chain, private_key, rpc_url)

    def _derive_address(self) -> str:
        return self.account.address

    def _build_contract_code(self, params: DeployParams) -> tuple[str, str]:
        from web3 import Web3

        params.name.encode("utf-8").hex()
        params.symbol.encode("utf-8").hex()
        decimals = params.decimals
        supply = int(params.initial_supply)
        owner = params.owner_address or self.deployer_address
        args_encoded = (
            Web3.to_bytes(text=params.name).ljust(32, b"\x00").hex()
            + Web3.to_bytes(text=params.symbol).ljust(32, b"\x00").hex()
            + hex(decimals)[2:].zfill(64)
            + hex(supply)[2:].zfill(64)
            + owner[2:].zfill(64)
            + ("1" if params.mintable else "0").zfill(64)
            + ("1" if params.burnable else "0").zfill(64)
        )
        bytecode = self.ERC20_BYTECODE + args_encoded
        return bytecode, owner

    async def deploy_token(self, params: DeployParams) -> TokenDeployment:
        try:
            factory_address = os.getenv(f"{self.chain.upper()}_TOKEN_FACTORY", "")
            if factory_address:
                return await self._deploy_via_factory(factory_address, params)
            else:
                return await self._deploy_direct(params)
        except (ValueError, TypeError, KeyError, OSError) as e:
            logger.debug("deploy_token_error", exc_info=True)
            logger.error(f"EVM deploy failed on {self.chain}: {e}")
            raise

    async def _deploy_direct(self, params: DeployParams) -> TokenDeployment:
        bytecode, owner = self._build_contract_code(params)
        nonce = self.w3.eth.get_transaction_count(cast("ChecksumAddress", self.deployer_address))
        gas_price = self.w3.eth.gas_price
        tx: TxParams = {
            "from": cast("ChecksumAddress", self.deployer_address),
            "nonce": nonce,
            "gasPrice": gas_price,
            "data": cast("HexStr", bytecode),
            "chainId": self.w3.eth.chain_id,
        }
        try:
            tx["gas"] = self.w3.eth.estimate_gas(tx)
        except (ConnectionError, OSError, TimeoutError):
            tx["gas"] = 3000000

        signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        contract_address = receipt.contractAddress  # type: ignore[attr-defined]  # PTB None-able fields
        deployment = TokenDeployment(
            deployment_id=self._generate_deployment_id(self.chain, params.symbol, tx_hash.hex()),
            chain=self.chain,
            name=params.name,
            symbol=params.symbol,
            decimals=params.decimals,
            total_supply=params.initial_supply,
            contract_address=contract_address,
            deployer_address=self.deployer_address,
            tx_hash=tx_hash.hex(),
            block_number=receipt.blockNumber,  # type: ignore[attr-defined]  # PTB None-able fields
            owner_address=owner,
            mintable=params.mintable,
            burnable=params.burnable,
            pausable=params.pausable,
            max_supply=params.max_supply,
            metadata_uri=params.metadata_uri,
        )
        logger.info(f"Deployed {params.symbol} on {self.chain} at {contract_address}")
        return deployment

    async def _deploy_via_factory(self, factory_address: str, params: DeployParams) -> TokenDeployment:
        factory_abi = [
            {
                "inputs": [
                    {"name": "name", "type": "string"},
                    {"name": "symbol", "type": "string"},
                    {"name": "decimals", "type": "uint8"},
                    {"name": "initialSupply", "type": "uint256"},
                    {"name": "owner", "type": "address"},
                    {"name": "mintable", "type": "bool"},
                    {"name": "burnable", "type": "bool"},
                ],
                "name": "createToken",
                "outputs": [{"name": "tokenAddress", "type": "address"}],
                "type": "function",
            }
        ]
        factory = self.w3.eth.contract(address=cast("ChecksumAddress", factory_address), abi=factory_abi)
        owner = params.owner_address or self.deployer_address
        tx = factory.functions.createToken(
            params.name,
            params.symbol,
            params.decimals,
            int(params.initial_supply),
            owner,
            params.mintable,
            params.burnable,
        ).build_transaction(
            {
                "from": self.deployer_address,
                "nonce": self.w3.eth.get_transaction_count(cast("ChecksumAddress", self.deployer_address)),
                "gasPrice": self.w3.eth.gas_price,
                "chainId": self.w3.eth.chain_id,
            }
        )
        signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        contract_address = None
        for log in receipt.logs:  # type: ignore[attr-defined]  # PTB None-able fields
            if log.address.lower() == factory_address.lower():
                contract_address = "0x" + log.data[-40:]
                break
        if not contract_address:
            raise RuntimeError("Factory deployment failed - no TokenCreated event found")
        deployment = TokenDeployment(
            deployment_id=self._generate_deployment_id(self.chain, params.symbol, tx_hash.hex()),
            chain=self.chain,
            name=params.name,
            symbol=params.symbol,
            decimals=params.decimals,
            total_supply=params.initial_supply,
            contract_address=contract_address,
            deployer_address=self.deployer_address,
            tx_hash=tx_hash.hex(),
            block_number=receipt.blockNumber,  # type: ignore[attr-defined]  # PTB None-able fields
            owner_address=owner,
            mintable=params.mintable,
            burnable=params.burnable,
            pausable=params.pausable,
            max_supply=params.max_supply,
            metadata_uri=params.metadata_uri,
        )
        logger.info(f"Factory-deployed {params.symbol} on {self.chain} at {contract_address}")
        return deployment

    async def _erc20_call(self, contract_address: str, abi: list[Any], fn: str, *args):
        """Helper for ERC-20 contract interactions."""
        contract = self.w3.eth.contract(address=cast("ChecksumAddress", contract_address), abi=abi)
        tx = getattr(contract.functions, fn)(*args).build_transaction(
            {
                "from": self.deployer_address,
                "nonce": self.w3.eth.get_transaction_count(cast("ChecksumAddress", self.deployer_address)),
                "gasPrice": self.w3.eth.gas_price,
                "chainId": self.w3.eth.chain_id,
            }
        )
        signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        return tx_hash.hex()

    async def mint_tokens(self, contract_address: str, to_address: str, amount: str) -> str:
        return await self._erc20_call(
            contract_address,
            [
                {
                    "inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}],
                    "name": "mint",
                    "outputs": [],
                    "type": "function",
                },
            ],
            "mint",
            to_address,
            int(amount),
        )

    async def burn_tokens(self, contract_address: str, amount: str) -> str:
        return await self._erc20_call(
            contract_address,
            [
                {"inputs": [{"name": "amount", "type": "uint256"}], "name": "burn", "outputs": [], "type": "function"},
            ],
            "burn",
            int(amount),
        )

    async def transfer_ownership(self, contract_address: str, new_owner: str) -> str:
        return await self._erc20_call(
            contract_address,
            [
                {
                    "inputs": [{"name": "newOwner", "type": "address"}],
                    "name": "transferOwnership",
                    "outputs": [],
                    "type": "function",
                },
            ],
            "transferOwnership",
            new_owner,
        )

    async def renounce_ownership(self, contract_address: str) -> str:
        return await self._erc20_call(
            contract_address,
            [
                {"inputs": [], "name": "renounceOwnership", "outputs": [], "type": "function"},
            ],
            "renounceOwnership",
        )

    async def get_token_info(self, contract_address: str) -> dict[str, Any]:
        abi = [
            {"inputs": [], "name": "name", "outputs": [{"type": "string"}], "type": "function"},
            {"inputs": [], "name": "symbol", "outputs": [{"type": "string"}], "type": "function"},
            {"inputs": [], "name": "decimals", "outputs": [{"type": "uint8"}], "type": "function"},
            {"inputs": [], "name": "totalSupply", "outputs": [{"type": "uint256"}], "type": "function"},
            {"inputs": [], "name": "owner", "outputs": [{"type": "address"}], "type": "function"},
        ]
        contract = self.w3.eth.contract(address=cast("ChecksumAddress", contract_address), abi=abi)
        return {
            "name": contract.functions.name().call(),
            "symbol": contract.functions.symbol().call(),
            "decimals": contract.functions.decimals().call(),
            "total_supply": str(contract.functions.totalSupply().call()),
            "owner": contract.functions.owner().call(),
        }

    async def get_balance(self, contract_address: str, wallet_address: str) -> str:
        abi = [
            {
                "inputs": [{"name": "account", "type": "address"}],
                "name": "balanceOf",
                "outputs": [{"type": "uint256"}],
                "type": "function",
            }
        ]
        contract = self.w3.eth.contract(address=cast("ChecksumAddress", contract_address), abi=abi)
        return str(contract.functions.balanceOf(wallet_address).call())

    async def blacklist_add(self, contract_address: str, address: str) -> str:
        return await self._erc20_call(
            contract_address,
            [
                {
                    "inputs": [{"name": "account", "type": "address"}],
                    "name": "blacklist",
                    "outputs": [],
                    "type": "function",
                },
            ],
            "blacklist",
            address,
        )

    async def blacklist_remove(self, contract_address: str, address: str) -> str:
        return await self._erc20_call(
            contract_address,
            [
                {
                    "inputs": [{"name": "account", "type": "address"}],
                    "name": "unblacklist",
                    "outputs": [],
                    "type": "function",
                },
            ],
            "unblacklist",
            address,
        )

    async def is_blacklisted(self, contract_address: str, address: str) -> bool:
        abi = [
            {
                "inputs": [{"name": "account", "type": "address"}],
                "name": "isBlacklisted",
                "outputs": [{"type": "bool"}],
                "type": "function",
            }
        ]
        contract = self.w3.eth.contract(address=cast("ChecksumAddress", contract_address), abi=abi)
        return contract.functions.isBlacklisted(address).call()

    async def set_trading_enabled(self, contract_address: str, enabled: bool) -> str:
        return await self._erc20_call(
            contract_address,
            [
                {
                    "inputs": [{"name": "enabled", "type": "bool"}],
                    "name": "setTradingEnabled",
                    "outputs": [],
                    "type": "function",
                },
            ],
            "setTradingEnabled",
            enabled,
        )

    async def set_max_wallet(self, contract_address: str, max_amount: str) -> str:
        return await self._erc20_call(
            contract_address,
            [
                {
                    "inputs": [{"name": "maxAmount", "type": "uint256"}],
                    "name": "setMaxWalletAmount",
                    "outputs": [],
                    "type": "function",
                },
            ],
            "setMaxWalletAmount",
            int(max_amount),
        )

    async def set_max_tx(self, contract_address: str, max_amount: str) -> str:
        return await self._erc20_call(
            contract_address,
            [
                {
                    "inputs": [{"name": "maxAmount", "type": "uint256"}],
                    "name": "setMaxTxAmount",
                    "outputs": [],
                    "type": "function",
                },
            ],
            "setMaxTxAmount",
            int(max_amount),
        )
