"""Multi-chain snapshot engine."""

from __future__ import annotations

import os

from app.core.logging import get_logger
from app.domains.airdrop.multichain.models import CrossChainHolder, TokenSource

logger = get_logger("darkroom_multichain_airdrop")

class MultiChainSnapshotEngine:
    """
    Aggregate token holders across multiple chains and tokens.

    Example:
      sources = [
          TokenSource("base", "0xCRM...", "CRM", weight=2.0, min_holdings="1000"),
          TokenSource("solana", "CRMsa...", "CRM", weight=1.5, min_holdings="1000"),
          TokenSource("solana", "CRMspl...", "CryptoRugMunch", weight=1.0, min_holdings="1"),
      ]
    """

    @staticmethod
    async def create_multichain_snapshot(
        sources: list[TokenSource],
        exclude_addresses: list[str] | None = None,
    ) -> dict[str, CrossChainHolder]:
        """
        Create a merged snapshot of all holders across all sources.
        Returns dict keyed by primary_address.
        """
        all_holders: dict[str, CrossChainHolder] = {}
        excluded = {a.lower() for a in (exclude_addresses or [])}

        for source in sources:
            logger.info(
                f"Snapshotting {source.token_symbol} on {source.chain} at {source.token_address}"
            )

            # Get holders for this source
            holders = await MultiChainSnapshotEngine._get_holders_for_source(source)

            for addr, amount in holders.items():
                if addr.lower() in excluded:
                    continue

                # Create or update cross-chain holder
                if addr not in all_holders:
                    all_holders[addr] = CrossChainHolder(
                        primary_address=addr,
                        chain_addresses={source.chain: addr},
                        holdings_per_source={},
                    )

                # Record holdings for this source
                source_key = f"{source.chain}:{source.token_symbol}"
                all_holders[addr].holdings_per_source[source_key] = amount

                # Update chain address mapping
                all_holders[addr].chain_addresses[source.chain] = addr

        logger.info(
            f"Multi-chain snapshot: {len(all_holders)} unique holders across {len(sources)} sources"
        )
        return all_holders

    @staticmethod
    async def _get_holders_for_source(source: TokenSource) -> dict[str, str]:
        """Get holders for a single token source."""
        if source.chain in ["ethereum", "base", "bsc"]:
            return await MultiChainSnapshotEngine._get_evm_holders(source)
        elif source.chain == "solana":
            return await MultiChainSnapshotEngine._get_solana_holders(source)
        elif source.chain == "tron":
            return await MultiChainSnapshotEngine._get_tron_holders(source)
        else:
            raise ValueError(f"Unsupported chain: {source.chain}")

    @staticmethod
    async def _get_evm_holders(source: TokenSource) -> dict[str, str]:
        """Get ERC-20 holders via RPC event logs."""
        from web3 import Web3

        rpc_url = os.getenv(f"{source.chain.upper()}_RPC_URL", "")
        if not rpc_url:
            raise ValueError(f"No RPC for {source.chain}")

        w3 = Web3(Web3.HTTPProvider(rpc_url))

        # Transfer event topic
        transfer_topic = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

        # Get recent blocks (last ~30 days worth, ~200k blocks on Base)
        current_block = w3.eth.block_number
        from_block = max(0, current_block - 200000)

        try:
            logs = w3.eth.get_logs(
                {
                    "fromBlock": from_block,
                    "toBlock": current_block,
                    "address": source.token_address,
                    "topics": [transfer_topic],
                }
            )
        except (ConnectionError, TimeoutError, ValueError) as e :
            logger.debug("_get_evm_holders_error", exc_info=True)
            logger.error(f"EVM log query failed: {e}")
            return {}

        # Build balances from transfer logs
        balances: dict[str, int] = {}
        for log in logs:
            try:
                from_addr = "0x" + log.topics[1].hex()[-40:]
                to_addr = "0x" + log.topics[2].hex()[-40:]
                amount = int(log.data.hex(), 16) if log.data else 0

                balances[from_addr] = balances.get(from_addr, 0) - amount
                balances[to_addr] = balances.get(to_addr, 0) + amount
            except (ConnectionError, OSError, TimeoutError):
                continue

        # Filter by minimum holdings
        min_amount = int(source.min_holdings)
        result = {}
        for addr, balance in balances.items():
            if balance > 0 and balance >= min_amount:
                # Apply max cap if set
                if source.max_holdings:
                    balance = min(balance, int(source.max_holdings))
                result[addr] = str(balance)

        return result

    @staticmethod
    async def _get_solana_holders(source: TokenSource) -> dict[str, str]:
        """Get SPL token holders via RPC."""
        from solana.rpc.api import Client
        from solders.pubkey import Pubkey

        rpc_url = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
        client = Client(rpc_url)

        try:
            mint = Pubkey.from_string(source.token_address)
            response = client.get_token_accounts_by_mint_json_parsed(mint, commitment="confirmed")

            holders = {}
            min_amount = int(source.min_holdings)

            for account in response["result"]["value"]:
                try:
                    parsed = account["account"]["data"]["parsed"]["info"]
                    owner = parsed["owner"]
                    amount = int(parsed["tokenAmount"]["amount"])

                    if amount >= min_amount:
                        if source.max_holdings:
                            amount = min(amount, int(source.max_holdings))
                        holders[owner] = str(amount)
                except (ConnectionError, OSError, TimeoutError):
                    continue

            return holders

        except (ValueError, KeyError, TypeError) as e :
            logger.debug("_get_solana_holders_error", exc_info=True)
            logger.error(f"Solana holder fetch failed: {e}")
            return {}

    @staticmethod
    async def _get_tron_holders(source: TokenSource) -> dict[str, str]:
        """Get TRC-20 holders via TronGrid."""
        # TRON holder enumeration requires an indexer or TronGrid API
        # Placeholder for now
        logger.warning("TRON holder snapshot not yet implemented - use manual list or TronGrid API")
        return {}
