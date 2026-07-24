"""Chain connectors for portfolio scanning."""
from __future__ import annotations

import asyncio
from typing import Any

from app.core.logging import get_logger

from .config import CHAIN_CONFIGS, COMMON_TOKENS, DEFAULT_TIMEOUT, ERC20_ABI, MAX_TOKENS_PER_WALLET

logger = get_logger(__name__)

try:
    from web3 import HTTPProvider, Web3 as _Web3
    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False; _Web3 = None; HTTPProvider = None

try:
    from solana.rpc.api import Client as SolanaClient
    SOLANA_AVAILABLE = True
except ImportError:
    SOLANA_AVAILABLE = False; SolanaClient = None

class ChainConnector:
    async def get_native_balance(self, address: str) -> float:
        raise NotImplementedError
    async def get_token_balances(self, address: str) -> list[dict[str, Any]]:
        raise NotImplementedError

class EVMConnector(ChainConnector):
    def __init__(self, chain_name: str, config: dict[str, Any]) -> None:
        self.chain_name = chain_name
        self.config = config
        self._w3: Any = None

    @property
    def w3(self) -> Any:
        if self._w3 is None:
            if not WEB3_AVAILABLE:
                raise RuntimeError("web3.py not installed")
            provider = HTTPProvider(self.config["rpc"], request_kwargs={"timeout": DEFAULT_TIMEOUT})
            self._w3 = _Web3(provider)
            if self.config.get("poa"):
                try:
                    from web3.middleware import geth_poa_middleware
                    self._w3.middleware_onion.inject(geth_poa_middleware, layer=0)
                except ImportError:
                    try:
                        from web3.middleware import ExtraDataToPOAMiddleware
                        self._w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
                    except ImportError:
                        pass
        return self._w3

    async def get_native_balance(self, address: str) -> float:
        try:
            bal_wei = await asyncio.to_thread(self.w3.eth.get_balance, address)
            return float(bal_wei) / (10**self.config["decimals"])
        except Exception as e:
            logger.warning(f"{self.chain_name} balance error: {e}")
            return 0.0

    async def get_token_balances(self, address: str) -> list[dict[str, Any]]:
        results = []
        checked = set()
        common = COMMON_TOKENS.get(self.chain_name, {})
        for _symbol, token_addr in common.items():
            try:
                contract = self.w3.eth.contract(address=_Web3.to_checksum_address(token_addr), abi=ERC20_ABI)
                bal = await asyncio.to_thread(contract.functions.balanceOf(address).call)
                if bal > 0:
                    dec = await asyncio.to_thread(contract.functions.decimals.call)
                    sym = await asyncio.to_thread(contract.functions.symbol.call)
                    results.append({"address": token_addr, "symbol": sym, "balance_raw": bal, "balance_formatted": float(bal) / (10**dec), "decimals": dec})
                    checked.add(token_addr.lower())
            except Exception:
                logger.debug("common_token_balance_failed %s", token_addr[:12])
        try:
            latest = await asyncio.to_thread(self.w3.eth.block_number)
            from_block = max(latest - 5000, 0)
            transfer_topic = self.w3.keccak(text="Transfer(address,address,uint256)").hex()
            logs = await asyncio.to_thread(
                self.w3.eth.get_logs,
                {
                    "address": None,
                    "fromBlock": from_block,
                    "toBlock": latest,
                    "topics": [
                        transfer_topic,
                        None,
                        _Web3.to_checksum_address(address).hex() if address.startswith("0x") else "0x" + address.zfill(64),
                    ],
                },
            )
            for log in logs[:200]:
                token_addr = log["address"].lower()
                if token_addr in checked:
                    continue
                checked.add(token_addr)
                try:
                    contract = self.w3.eth.contract(address=token_addr, abi=ERC20_ABI)
                    bal = await asyncio.to_thread(contract.functions.balanceOf(address).call)
                    if bal > 0:
                        dec = await asyncio.to_thread(contract.functions.decimals.call)
                        sym = await asyncio.to_thread(contract.functions.symbol.call)
                        results.append({"address": log["address"], "symbol": sym, "balance_raw": bal, "balance_formatted": float(bal) / (10**dec), "decimals": dec})
                except Exception:
                    logger.debug("event_log_balance_failed %s", token_addr[:12])
                if len(results) >= MAX_TOKENS_PER_WALLET:
                    break
        except Exception as e:
            logger.debug(f"{self.chain_name} event scan error: {e}")
        return results

class SolanaConnector(ChainConnector):
    def __init__(self) -> None:
        self._client: Any = None

    @property
    def client(self) -> Any:
        if self._client is None:
            if not SOLANA_AVAILABLE:
                raise RuntimeError("solana-py not installed")
            self._client = SolanaClient(CHAIN_CONFIGS["solana"]["rpc"])
        return self._client

    async def get_native_balance(self, address: str) -> float:
        try:
            from solana.rpc.api import Pubkey as SolPubkey
            pubkey = SolPubkey.from_string(address)
            resp = await asyncio.to_thread(self.client.get_balance, pubkey)
            if resp and resp.get("result"):
                return resp["result"]["value"] / 1e9
            return 0.0
        except Exception as e:
            logger.warning(f"solana balance error: {e}")
            return 0.0

    async def get_token_balances(self, address: str) -> list[dict[str, Any]]:
        results = []
        try:
            from solana.rpc.api import Pubkey as SolPubkey
            pubkey = SolPubkey.from_string(address)
            resp = await asyncio.to_thread(self.client.get_token_accounts_by_owner_json_parsed, pubkey)
            if not resp or not resp.get("result"):
                return results
            for item in resp["result"]["value"]:
                try:
                    parsed = item["account"]["data"]["parsed"]["info"]
                    mint = parsed.get("mint", "")
                    token_amount = parsed.get("tokenAmount", {})
                    ui_amount = float(token_amount.get("uiAmount", 0) or 0)
                    if ui_amount <= 0:
                        continue
                    results.append({"address": mint, "symbol": token_amount.get("symbol", "SPL"), "balance_raw": int(token_amount.get("amount", "0")), "balance_formatted": ui_amount, "decimals": token_amount.get("decimals", 0)})
                except Exception:
                    logger.debug("solana_parse_token_failed %s", item.get("account", {}).get("data", {}).get("parsed", {}).get("info", {}).get("mint", "")[:12])
        except Exception as e:
            logger.warning(f"solana token balances error: {e}")
        return results
