"""Solana SPL Token deployer."""

from __future__ import annotations

from typing import Any

import httpx

from app.core.logging import get_logger
from app.domains.tokens.deployer.base import ChainDeployer
from app.domains.tokens.deployer.models import DeployParams, TokenDeployment

logger = get_logger("darkroom_token_deployer.solana")


class SolanaDeployer(ChainDeployer):
    """Deploy SPL tokens on Solana."""

    def __init__(self, private_key: str, rpc_url: str) -> None:
        from solana.rpc.api import Client
        from solders.keypair import Keypair

        self.client = Client(rpc_url)
        self.keypair = Keypair.from_base58_string(private_key)
        super().__init__("solana", private_key, rpc_url)

    def _derive_address(self) -> str:
        return str(self.keypair.pubkey())

    async def deploy_token(self, params: DeployParams) -> TokenDeployment:
        from solana.transaction import Transaction
        from solders.keypair import Keypair
        from solders.pubkey import Pubkey
        from solders.system_program import CreateAccountParams, create_account
        from spl.token.constants import TOKEN_PROGRAM_ID
        from spl.token.instructions import create_mint, get_associated_token_address, mint_to

        try:
            mint = Keypair()
            rent = self.client.get_minimum_balance_for_rent_exemption(82)["result"]
            tx = Transaction()
            create_account_ix = create_account(
                CreateAccountParams(
                    from_pubkey=self.keypair.pubkey(),
                    to_pubkey=mint.pubkey(),
                    lamports=rent,
                    space=82,
                    program_id=TOKEN_PROGRAM_ID,
                )
            )
            tx.add(create_account_ix)
            init_mint_ix = create_mint(
                mint=mint.pubkey(),
                decimals=params.decimals,
                mint_authority=self.keypair.pubkey(),
                freeze_authority=Pubkey.from_string(params.freeze_authority) if params.freeze_authority else None,
                program_id=TOKEN_PROGRAM_ID,
            )
            tx.add(init_mint_ix)
            ata = get_associated_token_address(self.keypair.pubkey(), mint.pubkey())
            mint_to_ix = mint_to(
                mint=mint.pubkey(),
                dest=ata,
                mint_authority=self.keypair.pubkey(),
                amount=int(params.initial_supply) * (10**params.decimals),
                program_id=TOKEN_PROGRAM_ID,
            )
            tx.add(mint_to_ix)
            tx.sign(self.keypair, mint)
            result = self.client.send_transaction(tx, self.keypair, mint)
            tx_hash = result["result"]

            deployment = TokenDeployment(
                deployment_id=self._generate_deployment_id("solana", params.symbol, tx_hash),
                chain="solana",
                name=params.name,
                symbol=params.symbol,
                decimals=params.decimals,
                total_supply=params.initial_supply,
                contract_address=str(mint.pubkey()),
                deployer_address=self.deployer_address,
                tx_hash=tx_hash,
                owner_address=self.deployer_address,
                mintable=params.mintable,
                burnable=params.burnable,
                pausable=params.pausable,
                max_supply=params.max_supply,
                metadata_uri=params.metadata_uri,
            )
            logger.info(f"Deployed SPL token {params.symbol} on Solana at {mint.pubkey()}")
            return deployment
        except (httpx.HTTPError, ConnectionError, TimeoutError, ValueError, KeyError, TypeError, OSError) as e:
            logger.debug("unknown_error", exc_info=True)
            logger.error(f"Solana deploy failed: {e}")
            raise

    async def mint_tokens(self, contract_address: str, to_address: str, amount: str) -> str:
        from solana.transaction import Transaction
        from solders.pubkey import Pubkey
        from spl.token.constants import TOKEN_PROGRAM_ID
        from spl.token.instructions import get_associated_token_address, mint_to

        mint = Pubkey.from_string(contract_address)
        dest = get_associated_token_address(Pubkey.from_string(to_address), mint)
        tx = Transaction()
        mint_to_ix = mint_to(
            mint=mint,
            dest=dest,
            mint_authority=self.keypair.pubkey(),
            amount=int(amount),
            program_id=TOKEN_PROGRAM_ID,
        )
        tx.add(mint_to_ix)
        tx.sign(self.keypair)
        result = self.client.send_transaction(tx, self.keypair)
        return result["result"]

    async def burn_tokens(self, contract_address: str, amount: str) -> str:
        from solana.transaction import Transaction
        from solders.pubkey import Pubkey
        from spl.token.constants import TOKEN_PROGRAM_ID
        from spl.token.instructions import burn, get_associated_token_address

        mint = Pubkey.from_string(contract_address)
        ata = get_associated_token_address(self.keypair.pubkey(), mint)
        tx = Transaction()
        burn_ix = burn(
            account=ata, mint=mint, owner=self.keypair.pubkey(), amount=int(amount), program_id=TOKEN_PROGRAM_ID
        )
        tx.add(burn_ix)
        tx.sign(self.keypair)
        result = self.client.send_transaction(tx, self.keypair)
        return result["result"]

    async def transfer_ownership(self, contract_address: str, new_owner: str) -> str:
        from solana.transaction import Transaction
        from solders.pubkey import Pubkey
        from spl.token.constants import TOKEN_PROGRAM_ID
        from spl.token.instructions import AuthorityType, set_authority

        mint = Pubkey.from_string(contract_address)
        tx = Transaction()
        set_auth_ix = set_authority(
            account=mint,
            current_authority=self.keypair.pubkey(),
            authority_type=AuthorityType.MINT_TOKENS,
            new_authority=Pubkey.from_string(new_owner),
            program_id=TOKEN_PROGRAM_ID,
        )
        tx.add(set_auth_ix)
        tx.sign(self.keypair)
        result = self.client.send_transaction(tx, self.keypair)
        return result["result"]

    async def renounce_ownership(self, contract_address: str) -> str:
        from solana.transaction import Transaction
        from solders.pubkey import Pubkey
        from spl.token.constants import TOKEN_PROGRAM_ID
        from spl.token.instructions import AuthorityType, set_authority

        mint = Pubkey.from_string(contract_address)
        tx = Transaction()
        set_auth_ix = set_authority(
            account=mint,
            current_authority=self.keypair.pubkey(),
            authority_type=AuthorityType.MINT_TOKENS,
            new_authority=None,
            program_id=TOKEN_PROGRAM_ID,
        )
        tx.add(set_auth_ix)
        tx.sign(self.keypair)
        result = self.client.send_transaction(tx, self.keypair)
        return result["result"]

    async def get_token_info(self, contract_address: str) -> dict[str, Any]:
        from solders.pubkey import Pubkey

        mint = Pubkey.from_string(contract_address)
        account_info = self.client.get_account_info(mint)
        data = account_info["result"]["value"]["data"][0]
        import base64

        raw = base64.b64decode(data)
        decimals = raw[44]
        supply = int.from_bytes(raw[36:44], "little")
        metadata = {}
        try:
            from metaplex.metadata import get_metadata

            metadata = get_metadata(self.client, contract_address)
        except (ConnectionError, OSError, TimeoutError):
            pass
        return {"address": contract_address, "decimals": decimals, "supply": str(supply), "metadata": metadata}

    async def get_balance(self, contract_address: str, wallet_address: str) -> str:
        from solders.pubkey import Pubkey
        from spl.token.instructions import get_associated_token_address

        mint = Pubkey.from_string(contract_address)
        wallet = Pubkey.from_string(wallet_address)
        ata = get_associated_token_address(wallet, mint)
        balance = self.client.get_token_account_balance(str(ata))
        return balance["result"]["value"]["amount"]

    async def blacklist_add(self, contract_address: str, address: str) -> str:
        from solana.transaction import Transaction
        from solders.pubkey import Pubkey
        from spl.token.constants import TOKEN_PROGRAM_ID
        from spl.token.instructions import freeze_account, get_associated_token_address

        mint = Pubkey.from_string(contract_address)
        account = get_associated_token_address(Pubkey.from_string(address), mint)
        tx = Transaction()
        freeze_ix = freeze_account(
            account=account,
            mint=mint,
            owner=self.keypair.pubkey(),
            freeze_authority=self.keypair.pubkey(),
            program_id=TOKEN_PROGRAM_ID,
        )
        tx.add(freeze_ix)
        tx.sign(self.keypair)
        result = self.client.send_transaction(tx, self.keypair)
        return result["result"]

    async def blacklist_remove(self, contract_address: str, address: str) -> str:
        from solana.transaction import Transaction
        from solders.pubkey import Pubkey
        from spl.token.constants import TOKEN_PROGRAM_ID
        from spl.token.instructions import get_associated_token_address, thaw_account

        mint = Pubkey.from_string(contract_address)
        account = get_associated_token_address(Pubkey.from_string(address), mint)
        tx = Transaction()
        thaw_ix = thaw_account(
            account=account,
            mint=mint,
            owner=self.keypair.pubkey(),
            freeze_authority=self.keypair.pubkey(),
            program_id=TOKEN_PROGRAM_ID,
        )
        tx.add(thaw_ix)
        tx.sign(self.keypair)
        result = self.client.send_transaction(tx, self.keypair)
        return result["result"]

    async def is_blacklisted(self, contract_address: str, address: str) -> bool:
        from solders.pubkey import Pubkey
        from spl.token.instructions import get_associated_token_address

        mint = Pubkey.from_string(contract_address)
        account = get_associated_token_address(Pubkey.from_string(address), mint)
        info = self.client.get_account_info(str(account))
        if info["result"]["value"]:
            data = info["result"]["value"]["data"][0]
            import base64

            raw = base64.b64decode(data)
            return raw[64] == 2 if len(raw) > 64 else False
        return False

    async def set_trading_enabled(self, contract_address: str, enabled: bool) -> str:
        if enabled:
            return "Trading already enabled (mint authority active)"
        return await self.renounce_ownership(contract_address)

    async def set_max_wallet(self, contract_address: str, max_amount: str) -> str:
        logger.warning("Max wallet not natively supported on Solana SPL")
        return "not_supported"

    async def set_max_tx(self, contract_address: str, max_amount: str) -> str:
        logger.warning("Max tx not natively supported on Solana SPL")
        return "not_supported"
