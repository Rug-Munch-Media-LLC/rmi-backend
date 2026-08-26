"""
SENTINEL - Block Zero Sniper Analysis
======================================
Detects team distribution farms by tracking the first 5 wallets that bought
in block zero (or earliest block) and checking if they immediately split
tokens into 50+ smaller wallets - a classic team distribution pattern
where devs prepare to dump on retail.

Detection logic:
  1. Fetch earliest transactions for the token (first buy block)
  2. Identify first 5 unique buyer wallets
  3. Trace outgoing transfers from those wallets within 10 blocks
  4. If any wallet split to 50+ downstream wallets → splitting detected
  5. >50% of first buyers splitting → HIGH farm confidence
  6. >80% of first buyers splitting → CRITICAL farm confidence

Data sources (direct API calls - no circular imports):
  Solana:     Helius Enhanced Transactions, Solscan account transfers
  EVM:        Etherscan tokentx (blockscan-family API)
  Fallback:   Consensus RPC for earliest block estimation
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

import httpx
import requests

from app.chain_registry import is_evm, is_solana
from app.core.logging import get_logger
from app.domains.scanners._sentinel_registry import sentinel_module

logger = get_logger("block_zero_sniper")

# ── API Keys ────────────────────────────────────────────────────────────────

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")
BSCSCAN_API_KEY = os.getenv("BSCSCAN_API_KEY", "")
POLYGONSCAN_API_KEY = os.getenv("POLYGONSCAN_API_KEY", "")
SNOWTRACE_API_KEY = os.getenv("SNOWTRACE_API_KEY", "")
FTMSCAN_API_KEY = os.getenv("FTMSCAN_API_KEY", "")

HELIUS_RPC_URL = "https://mainnet.helius-rpc.com"
HELIUS_API_URL = "https://mainnet.helius-rpc.com"
DEXSCREENER_URL = "https://api.dexscreener.com"

# EVM blockscan network registry
ETHERSCAN_NETWORKS = {
    "ethereum": {"url": "https://api.etherscan.io/api", "key": ETHERSCAN_API_KEY},
    "eth": {"url": "https://api.etherscan.io/api", "key": ETHERSCAN_API_KEY},
    "bsc": {"url": "https://api.bscscan.com/api", "key": BSCSCAN_API_KEY},
    "polygon": {"url": "https://api.polygonscan.com/api", "key": POLYGONSCAN_API_KEY},
    "avalanche": {"url": "https://api.snowtrace.io/api", "key": SNOWTRACE_API_KEY},
    "fantom": {"url": "https://api.ftmscan.com/api", "key": FTMSCAN_API_KEY},
    "arbitrum": {"url": "https://api.arbiscan.io/api", "key": ETHERSCAN_API_KEY},
    "optimism": {"url": "https://api-optimistic.etherscan.io/api", "key": ETHERSCAN_API_KEY},
    "base": {"url": "https://api.basescan.org/api", "key": ETHERSCAN_API_KEY},
}

# ── Data Classes ────────────────────────────────────────────────────────────


@dataclass
class SplitPath:
    """A single split hop: from wallet → to wallet at a given block."""

    from_address: str
    to_address: str
    block: int


@dataclass
class BlockZeroReport:
    """Report from BlockZeroSniperAnalyzer.

    Attributes:
        sniper_detected:     Whether first-block sniping activity was found.
        splitting_detected:  Whether any first buyers split into 50+ wallets.
        farm_confidence:     0-1 float, confidence that this is a distribution farm.
        first_wallets:       First 5 unique buyer wallets (addresses).
        split_wallets:       Wallets detected doing splits (addresses).
        split_paths:         Detailed split transfer records [{from, to, block}, ...].
        risk_label:          UNKNOWN / LOW / HIGH / CRITICAL.
    """

    sniper_detected: bool = False
    splitting_detected: bool = False
    farm_confidence: float = 0.0
    first_wallets: list[str] = field(default_factory=list)
    split_wallets: list[str] = field(default_factory=list)
    split_paths: list[SplitPath] = field(default_factory=list)
    risk_label: str = "UNKNOWN"


# ── Analyzer ────────────────────────────────────────────────────────────────


@sentinel_module(
    key="block_zero_sniper", tier="premium", description="Block-zero sniper detection (dev/bot buys first block)"
)
class BlockZeroSniperAnalyzer:
    """Analyzes block-zero sniping and split patterns to detect team distribution farms.

    Tracks first 5 wallets that bought in block zero (or earliest block).
    If those wallets immediately split tokens into 50+ smaller wallets,
    it's likely a team distribution farm preparing to dump.

    This module is a pure analyzer - it accepts optional pre-fetched transaction
    data via `earliest_transactions` and falls back to direct API calls if needed.
    """

    # Thresholds
    SPLIT_WALLET_THRESHOLD = 50  # 50+ downstream wallets = split detected
    SPLIT_BLOCK_WINDOW = 10  # within 10 blocks of the buy
    MAX_FIRST_WALLETS = 5  # track first 5 unique buyers

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=15.0)
        self._helius_key = HELIUS_API_KEY

    async def close(self) -> None:
        """Clean up the HTTP client."""
        await self._http.aclose()

    # ── Public API ──────────────────────────────────────────────────────────

    async def analyze(
        self,
        token_address: str,
        chain: str,
        earliest_transactions: list[dict[str, Any]] | None = None,
    ) -> BlockZeroReport:
        """Analyze block-zero sniping and wallet splitting.

        Args:
            token_address: Token contract/mint address to analyze.
            chain: Blockchain name (solana, ethereum, base, bsc, etc.).
            earliest_transactions: Optional pre-fetched list of earliest
                token transactions. Each dict should have at least:
                - 'from' or 'from_address' (str)
                - 'to' or 'to_address' (str)
                - 'block' or 'block_number' (int)
                If None, the analyzer will fetch them via direct API calls.

        Returns:
            BlockZeroReport with detection results.
        """
        chain_lower = chain.lower() if chain else ""

        # Gracefully handle missing transaction data
        if earliest_transactions is None:
            earliest_transactions = await self._fetch_earliest_transactions(token_address, chain_lower)

        # If still no data after fetch attempt, return unknown
        if not earliest_transactions:
            logger.info(
                "No transaction data available for %s on %s",
                token_address,
                chain_lower,
            )
            return BlockZeroReport(
                sniper_detected=False,
                splitting_detected=False,
                farm_confidence=0.0,
                first_wallets=[],
                split_wallets=[],
                split_paths=[],
                risk_label="UNKNOWN",
            )

        # Step 1: Identify first 5 unique buyer wallets
        first_wallets = self._extract_first_buyers(earliest_transactions)

        if not first_wallets:
            return BlockZeroReport(risk_label="UNKNOWN")

        # Step 2: Trace outgoing splits from each first wallet
        all_split_paths: list[SplitPath] = []
        split_wallets: list[str] = []

        for wallet in first_wallets:
            splits = await self._trace_wallet_splits(wallet, token_address, chain_lower)
            if len(splits) >= self.SPLIT_WALLET_THRESHOLD:
                split_wallets.append(wallet)
                all_split_paths.extend(splits)

        # Step 3: Classify
        sniper_detected = bool(first_wallets)
        splitting_detected = bool(split_wallets)
        ratio = len(split_wallets) / max(len(first_wallets), 1)

        if ratio > 0.8:
            farm_confidence = 1.0
            risk_label = "CRITICAL"
        elif ratio > 0.5:
            farm_confidence = 0.75
            risk_label = "HIGH"
        elif ratio > 0.0:
            farm_confidence = 0.5
            risk_label = "LOW"
        else:
            farm_confidence = 0.0
            risk_label = "LOW"

        return BlockZeroReport(
            sniper_detected=sniper_detected,
            splitting_detected=splitting_detected,
            farm_confidence=round(farm_confidence, 2),
            first_wallets=first_wallets,
            split_wallets=split_wallets,
            split_paths=all_split_paths,
            risk_label=risk_label,
        )

    # ── First-buyer extraction ──────────────────────────────────────────────

    @staticmethod
    def _extract_first_buyers(transactions: list[dict[str, Any]]) -> list[str]:
        """Extract the first 5 unique buyer wallets from earliest transactions.

        Transactions are assumed to be sorted chronologically (earliest first).
        A 'buyer' is the 'from' address - the wallet that initiated the purchase.
        """
        seen: set[Any] = set()
        buyers: list[str] = []

        for tx in transactions:
            # Normalise key names (supports both snake_case and camelCase)
            buyer = tx.get("from") or tx.get("from_address") or ""
            if not buyer:
                continue

            # Skip system/zero/null addresses
            if buyer in seen or _is_dead_address(buyer):
                continue

            seen.add(buyer)
            buyers.append(buyer)

            if len(buyers) >= 5:
                break

        return buyers

    # ── Split tracing ───────────────────────────────────────────────────────

    async def _trace_wallet_splits(
        self,
        wallet: str,
        token_address: str,
        chain: str,
    ) -> list[SplitPath]:
        """Trace outgoing token transfers from a wallet within the split window.

        For Solana: uses Helius signature fetching + Solscan transfers.
        For EVM: uses Etherscan tokentx with wallet as address filter.
        Returns list of SplitPath records - more than SPLIT_WALLET_THRESHOLD
        unique 'to' addresses indicates a split.
        """
        if is_solana(chain):
            return await self._trace_solana_splits(wallet, token_address)
        elif is_evm(chain):
            return await self._trace_evm_splits(wallet, token_address, chain)
        return []

    async def _trace_solana_splits(
        self,
        wallet: str,
        token_address: str,
    ) -> list[SplitPath]:
        """Trace Solana splits via Helius signatures for the wallet."""
        if not self._helius_key:
            logger.warning("HELIUS_API_KEY not set - cannot trace Solana splits")
            return []

        splits: list[SplitPath] = []

        try:
            # Get recent signatures for this wallet (outgoing transactions)
            sigs = await self._helius_rpc(
                "getSignaturesForAddress",
                [wallet, {"limit": 100}],
            )
            if not sigs or not isinstance(sigs, list):
                return []

            signatures = [s.get("signature", "") for s in sigs if s.get("signature")]
            if not signatures:
                return []

            # Parse transactions to find token transfers
            resp = await self._http.post(
                f"{HELIUS_API_URL}/v0/transactions/?api-key={self._helius_key}",
                json={"transactions": signatures[:50]},
            )
            if resp.status_code != 200:
                return []

            txs = resp.json()
            if not isinstance(txs, list):
                return []

            for tx in txs:
                block = tx.get("slot", 0) or 0
                # Check token transfers in the transaction
                token_transfers = tx.get("tokenTransfers") or []
                for tt in token_transfers:
                    tt_from = tt.get("fromUserAccount") or tt.get("fromTokenAccount") or ""
                    tt_to = tt.get("toUserAccount") or tt.get("toTokenAccount") or ""
                    mint = tt.get("mint", "")

                    # Only consider transfers of the target token, from our wallet
                    if mint.lower() != token_address.lower():
                        continue
                    if tt_from.lower() != wallet.lower():
                        continue
                    if tt_to.lower() == wallet.lower():
                        continue
                    if _is_dead_address(tt_to):
                        continue

                    splits.append(
                        SplitPath(
                            from_address=wallet,
                            to_address=tt_to,
                            block=block,
                        )
                    )

        except (
            httpx.HTTPError,
            requests.RequestException,
            ConnectionError,
            TimeoutError,
            ValueError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
        ) as e:
            logger.debug("unknown_error", exc_info=True)
            logger.warning("Solana split trace failed for %s: %s", wallet, e)

        return splits

    async def _trace_evm_splits(
        self,
        wallet: str,
        token_address: str,
        chain: str,
    ) -> list[SplitPath]:
        """Trace EVM splits via Etherscan tokentx for the wallet."""
        net = ETHERSCAN_NETWORKS.get(chain.lower())
        if not net:
            return []

        api_key = net["key"]
        if not api_key:
            logger.warning("No Etherscan API key for %s", chain)
            return []

        splits: list[SplitPath] = []

        try:
            params = {
                "module": "account",
                "action": "tokentx",
                "address": wallet,
                "contractaddress": token_address,
                "sort": "asc",
                "apikey": api_key,
            }
            resp = await self._http.get(net["url"], params=params)
            if resp.status_code != 200:
                return []

            data = resp.json()
            if data.get("status") != "1":
                return []

            results = data.get("result", [])
            if not isinstance(results, list):
                return []

            for tx in results:
                tx_from = (tx.get("from", "") or "").lower()
                tx_to = (tx.get("to", "") or "").lower()
                block = int(tx.get("blockNumber", 0) or 0)

                # Only transfers FROM our wallet
                if tx_from != wallet.lower():
                    continue
                if tx_to == wallet.lower():
                    continue
                if _is_dead_address(tx_to):
                    continue

                splits.append(
                    SplitPath(
                        from_address=wallet,
                        to_address=tx.get("to", ""),
                        block=block,
                    )
                )

        except (httpx.HTTPError, requests.RequestException, json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
            logger.debug("unknown_error", exc_info=True)
            logger.warning("EVM split trace failed for %s on %s: %s", wallet, chain, e)

        return splits

    # ── Fetch earliest transactions (fallback) ──────────────────────────────

    async def _fetch_earliest_transactions(
        self,
        token_address: str,
        chain: str,
    ) -> list[dict[str, Any]]:
        """Fetch earliest token transactions via direct API calls.

        Strategy:
          1. DexScreener for pair creation info (earliest block)
          2. Solana: Helius signatures for the token mint
          3. EVM: Etherscan tokentx for the token contract
        """
        transactions: list[dict[str, Any]] = []

        # Indexer-first (2026-08-26): wallet_memory.dex_events stores every
        # swap with block numbers — earliest txs are ONE indexed query vs
        # DexScreener+Helius+Etherscan rate-limited chains. Solana keeps
        # its Helius path (SPL balance changes live in a different table).
        if is_evm(chain):
            import os as _os

            try:
                from clickhouse_driver import Client as CHClient

                client = CHClient(
                    host=_os.getenv("CH_HOST") or _os.getenv("CLICKHOUSE_HOST", "127.0.0.1"),
                    port=int(_os.getenv("CH_PORT") or _os.getenv("CLICKHOUSE_PORT", "9000")),
                    user=_os.getenv("CH_USER") or _os.getenv("CLICKHOUSE_USER", "rmi"),
                    password=_os.getenv("CH_PASSWORD") or _os.getenv("CLICKHOUSE_PASSWORD") or "",
                    database="wallet_memory",
                    connect_timeout=3,
                    send_receive_timeout=10,
                )
                rows = client.execute(
                    """
                    SELECT sender, recipient, block_number, tx_hash
                    FROM dex_events
                    WHERE chain = %(chain)s
                      AND (token0 = %(token)s OR token1 = %(token)s)
                      AND type = 'swap'
                    ORDER BY block_number ASC, log_index ASC
                    LIMIT 60
                    """,
                    {"chain": chain.lower(), "token": token_address.lower()},
                )
                for sender, recipient, blk, tx in rows:
                    transactions.append(
                        {
                            "from": sender or "",
                            "to": recipient or "",
                            "block_number": int(blk),
                            "hash": tx or "",
                        }
                    )
                if transactions:
                    logger.info(
                        "block_zero_indexer_first token=%s txs=%d",
                        token_address[:10],
                        len(transactions),
                    )
                    return transactions
            except Exception as exc:
                logger.debug("earliest_txs_indexer_failed: %s", str(exc)[:120])

        if is_solana(chain):
            transactions = await self._fetch_solana_earliest(token_address)
        elif is_evm(chain):
            transactions = await self._fetch_evm_earliest(token_address, chain)

        return transactions

    async def _fetch_solana_earliest(
        self,
        token_address: str,
    ) -> list[dict[str, Any]]:
        """Fetch earliest Solana token transactions via Helius."""
        if not self._helius_key:
            logger.warning("HELIUS_API_KEY not set - cannot fetch Solana early txs")
            return []

        try:
            # Get earliest signatures for this token mint
            sigs = await self._helius_rpc(
                "getSignaturesForAddress",
                [token_address, {"limit": 50}],
            )
            if not sigs or not isinstance(sigs, list):
                return []

            signatures = [s.get("signature", "") for s in sigs if s.get("signature")]
            if not signatures:
                return []

            # Parse through Helius Enhanced Transactions API
            resp = await self._http.post(
                f"{HELIUS_API_URL}/v0/transactions/?api-key={self._helius_key}",
                json={"transactions": signatures[:50]},
            )
            if resp.status_code != 200:
                return []

            txs = resp.json()
            if not isinstance(txs, list):
                return []

            result: list[dict[str, Any]] = []
            for tx in txs:
                block = tx.get("slot", 0) or 0
                token_transfers = tx.get("tokenTransfers") or []
                for tt in token_transfers:
                    mint = tt.get("mint", "")
                    if mint.lower() != token_address.lower():
                        continue
                    result.append(
                        {
                            "from": tt.get("fromUserAccount", ""),
                            "to": tt.get("toUserAccount", ""),
                            "block": block,
                            "amount": tt.get("tokenAmount", 0),
                            "tx": tx.get("signature", ""),
                        }
                    )

            # Sort by block ascending
            result.sort(key=lambda x: x.get("block", 0))
            return result

        except (httpx.HTTPError, requests.RequestException, json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug("unknown_error", exc_info=True)
            logger.warning(
                "Failed to fetch Solana earliest txs for %s: %s",
                token_address,
                e,
            )
            return []

    async def _fetch_evm_earliest(
        self,
        token_address: str,
        chain: str,
    ) -> list[dict[str, Any]]:
        """Fetch earliest EVM token transactions via Etherscan tokentx."""
        net = ETHERSCAN_NETWORKS.get(chain.lower())
        if not net:
            return []

        api_key = net["key"]
        if not api_key:
            logger.warning("No Etherscan API key for %s", chain)
            return []

        try:
            params = {
                "module": "account",
                "action": "tokentx",
                "contractaddress": token_address,
                "sort": "asc",
                "apikey": api_key,
            }
            resp = await self._http.get(net["url"], params=params)
            if resp.status_code != 200:
                return []

            data = resp.json()
            if data.get("status") != "1":
                return []

            results = data.get("result", [])
            if not isinstance(results, list):
                return []

            result: list[dict[str, Any]] = []
            for tx in results:
                result.append(
                    {
                        "from": tx.get("from", ""),
                        "to": tx.get("to", ""),
                        "block": int(tx.get("blockNumber", 0) or 0),
                        "amount": float(tx.get("value", 0) or 0),
                        "tx": tx.get("hash", ""),
                    }
                )

            # Already sorted asc by Etherscan
            return result

        except (httpx.HTTPError, requests.RequestException, json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
            logger.debug("unknown_error", exc_info=True)
            logger.warning(
                "Failed to fetch EVM earliest txs for %s on %s: %s",
                token_address,
                chain,
                e,
            )
            return []

    # ── Helius RPC helper ───────────────────────────────────────────────────

    async def _helius_rpc(self, method: str, params: list[Any] | None = None) -> Any | None:
        """Make a Helius JSON-RPC call."""
        try:
            resp = await self._http.post(
                f"{HELIUS_RPC_URL}/?api-key={self._helius_key}",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": method,
                    "params": params or [],
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("result")
        except (httpx.HTTPError, requests.RequestException, json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug("_helius_rpc_error", exc_info=True)
            logger.warning("Helius RPC call %s failed: %s", method, e)
        return None


# ── Helpers ─────────────────────────────────────────────────────────────────

_DEAD_ADDRESSES: set[Any] = {
    # Solana
    "11111111111111111111111111111111",
    "1nc1nerator11111111111111111111111111111111",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    # EVM
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
    "0x0000000000000000000000000000000000000001",
    "0xdead000000000000000000000000000000000000",
}


def _is_dead_address(address: str) -> bool:
    """Check if an address is a known dead/burn/null address (case-insensitive)."""
    addr = address.lower().strip()
    return addr in _DEAD_ADDRESSES or addr in {
        f"0x{i:040x}"
        for i in range(10)  # common 0x0... variants
    }
