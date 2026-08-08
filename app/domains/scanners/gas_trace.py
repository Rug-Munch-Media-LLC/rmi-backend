"""
SENTINEL - Three Hop Gas Trace Module
======================================
Traces deployer gas money up to 3 wallet hops deep to identify
funding sources. Flags projects where the gas chain dead-ends at a
freshly created exchange hot wallet with zero prior history - a classic
sniper/rug deployment pattern.

Key insight: professional ruggers fund deployer wallets through
a chain of intermediary wallets (often from CEX hot wallets) to
obfuscate the source. If the chain collapses to a fresh wallet
with no history, that's a CRITICAL signal.

Uses:
  - Etherscan-family gettxlist API for EVM chains
  - Solscan account transfers/transactions for Solana
  - Known CEX hot wallet registry from chain_registry
"""

from __future__ import annotations
from typing import Any

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
import requests

from app.chain_registry import (
    get_cex_wallets,
    is_evm,
    is_solana,
)
from app.core.logging import get_logger
from app.domains.scanners._sentinel_registry import sentinel_module
from app.free_solscan_client import FreeSolscanClient, is_known_exchange

logger = get_logger("gas_trace")

# ── Etherscan Network Config (mirrors dev_reputation pattern) ─────────────

ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")
BSCSCAN_API_KEY = os.getenv("BSCSCAN_API_KEY", "")
POLYGONSCAN_API_KEY = os.getenv("POLYGONSCAN_API_KEY", "")
SNOWTRACE_API_KEY = os.getenv("SNOWTRACE_API_KEY", "")
FTMSCAN_API_KEY = os.getenv("FTMSCAN_API_KEY", "")
BASESCAN_API_KEY = os.getenv("BASESCAN_API_KEY", "")
ARBISCAN_API_KEY = os.getenv("ARBISCAN_API_KEY", "")

ETHERSCAN_NETWORKS = {
    "ethereum": {"url": "https://api.etherscan.io/api", "key": ETHERSCAN_API_KEY},
    "eth": {"url": "https://api.etherscan.io/api", "key": ETHERSCAN_API_KEY},
    "bsc": {"url": "https://api.bscscan.com/api", "key": BSCSCAN_API_KEY},
    "polygon": {"url": "https://api.polygonscan.com/api", "key": POLYGONSCAN_API_KEY},
    "avalanche": {"url": "https://api.snowtrace.io/api", "key": SNOWTRACE_API_KEY},
    "fantom": {"url": "https://api.ftmscan.com/api", "key": FTMSCAN_API_KEY},
    "arbitrum": {"url": "https://api.arbiscan.io/api", "key": ARBISCAN_API_KEY},
    "optimism": {"url": "https://api-optimistic.etherscan.io/api", "key": ETHERSCAN_API_KEY},
    "base": {"url": "https://api.basescan.org/api", "key": BASESCAN_API_KEY},
    "linea": {"url": "https://api.lineascan.build/api", "key": ETHERSCAN_API_KEY},
    "zksync": {"url": "https://api.zksync.blockscout.run/api", "key": ""},
    "scroll": {"url": "https://api.scrollscan.com/api", "key": ETHERSCAN_API_KEY},
}

# ── Data Models ────────────────────────────────────────────────────────────


@dataclass
class TraceHop:
    """A single hop in the gas trace chain."""

    address: str
    relationship: str  # "deployer", "funder", "funder_of_funder", "cex_hot_wallet", etc.
    first_seen: str  # ISO timestamp or "unknown"
    tx_hash: str = ""  # The transaction that connected this hop


@dataclass
class GasTraceReport:
    """Report for the 3-hop gas trace analysis."""

    deployer_address: str
    chain: str
    trace_complete: bool  # True if we resolved the chain fully
    hop_count: int  # Number of hops traced (0-3+)
    dead_end: bool  # True if chain dead-ends at a fresh/empty wallet
    dead_end_reason: str  # Why it dead-ended ("fresh_wallet", "cex_hot_wallet", "no_funding_tx", "api_error")
    risk_label: str  # "LOW", "MEDIUM", "HIGH", "CRITICAL"
    cex_funded: bool  # True if any hop touches a known CEX hot wallet
    cex_name: str = ""  # Name of the CEX identified (if applicable)
    traced_chain: list[TraceHop] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return {
            "deployer_address": self.deployer_address,
            "chain": self.chain,
            "trace_complete": self.trace_complete,
            "hop_count": self.hop_count,
            "traced_chain": [
                {
                    "address": h.address,
                    "relationship": h.relationship,
                    "first_seen": h.first_seen,
                    "tx_hash": h.tx_hash,
                }
                for h in self.traced_chain
            ],
            "dead_end": self.dead_end,
            "dead_end_reason": self.dead_end_reason,
            "risk_label": self.risk_label,
            "cex_funded": self.cex_funded,
            "cex_name": self.cex_name,
            "errors": self.errors,
        }


# ── Gas Trace Analyzer ─────────────────────────────────────────────────────


@sentinel_module(
    key="gas_trace", tier="premium", description="Three-hop gas trace of deployer funding", needs_deployer=True
)
class GasTraceAnalyzer:
    """
    Traces deployer gas money up to 3 wallet hops deep.

    For EVM chains: uses Etherscan-family 'txlist' API to find
    incoming transactions and walks back the sender chain.

    For Solana: uses FreeSolscanClient to find incoming transfers
    and traces the source.

    Logic:
      - Start at deployer_address → find earliest incoming TX → get sender
      - That sender is hop 1 → find their earliest incoming TX → get sender
      - Repeat up to 3 hops
      - At each hop, classify the wallet:
        * CEX hot wallet with >1000 TXs → legitimate, stop
        * Fresh wallet (<7 days old, <2 prior TXs) → dead_end, CRITICAL
        * No incoming funding TX found → dead_end
    """

    MAX_HOPS = 3
    FRESH_WALLET_DAYS = 7
    CEX_TX_THRESHOLD = 1000

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=20.0)

    async def analyze(self, deployer_address: str, chain: str) -> GasTraceReport:
        """
        Full 3-hop gas trace analysis for a deployer address.

        Args:
            deployer_address: The deployer's wallet address.
            chain: Chain name (e.g. "ethereum", "solana", "bsc", "base").

        Returns:
            GasTraceReport with trace chain, dead-end flags, and risk label.
        """
        logger.info(f"GasTrace analyzing {deployer_address} on {chain}")

        report = GasTraceReport(
            deployer_address=deployer_address,
            chain=chain,
            trace_complete=False,
            hop_count=0,
            dead_end=False,
            dead_end_reason="",
            risk_label="LOW",
            cex_funded=False,
        )

        # Record deployer as hop 0
        deployer_info = await self._get_wallet_first_seen(deployer_address, chain)
        report.traced_chain.append(
            TraceHop(
                address=deployer_address,
                relationship="deployer",
                first_seen=deployer_info.get("first_seen", "unknown"),
            )
        )

        # Trace backwards
        current_address = deployer_address
        chain_visited = {deployer_address.lower()}

        for hop in range(1, self.MAX_HOPS + 1):
            try:
                # Find the funding source for the current address
                funding = await self._find_funding_source(current_address, chain)
            except httpx.HTTPError as e:
                logger.debug("unknown_error", exc_info=True)
                logger.warning(f"Gas trace hop {hop} error for {current_address}: {e}")
                report.errors.append(f"Hop {hop} error: {e}")
                report.dead_end = True
                report.dead_end_reason = "api_error"
                break

            if funding is None:
                # No funding source found - dead end
                report.dead_end = True
                report.dead_end_reason = "no_funding_tx"
                report.trace_complete = False
                logger.info(f"Gas trace dead end at hop {hop}: no funding TX for {current_address}")
                break

            source_address = funding["from"]
            tx_hash = funding.get("tx_hash", "")

            # Detect cycles (shouldn't happen in a funding chain but be safe)
            if source_address.lower() in chain_visited:
                logger.info(f"Gas trace cycle detected at hop {hop} → {source_address}")
                report.dead_end = True
                report.dead_end_reason = "cycle_detected"
                break
            chain_visited.add(source_address.lower())

            # Classify the source wallet
            classification = await self._classify_wallet(source_address, chain)

            relationship = classification.get("relationship", "funder")
            first_seen = classification.get("first_seen", "unknown")
            is_cex = classification.get("is_cex", False)
            cex_name = classification.get("cex_name", "")
            classification.get("is_fresh", False)
            classification.get("tx_count", 0)
            is_dead_end = classification.get("is_dead_end", False)
            dead_end_reason = classification.get("dead_end_reason", "")

            # Record the hop
            report.traced_chain.append(
                TraceHop(
                    address=source_address,
                    relationship=relationship,
                    first_seen=first_seen,
                    tx_hash=tx_hash,
                )
            )
            report.hop_count = hop

            if is_cex:
                report.cex_funded = True
                report.cex_name = cex_name or report.cex_name

            # Determine if this is terminal
            if is_dead_end:
                report.dead_end = True
                report.dead_end_reason = dead_end_reason
                report.trace_complete = True
                logger.info(f"Gas trace terminated at hop {hop}: {dead_end_reason} ({source_address})")
                break

            # Continue tracing
            current_address = source_address

            # If we've reached the max hops, stop
            if hop == self.MAX_HOPS:
                report.trace_complete = True
                logger.info(f"Gas trace reached max {self.MAX_HOPS} hops to {source_address}")
        else:
            # Loop completed without break - all hops exhausted
            report.trace_complete = True

        # Calculate risk label
        report.risk_label = self._calculate_risk_label(report)

        logger.info(
            f"GasTrace complete: {report.hop_count} hops, "
            f"dead_end={report.dead_end}, "
            f"risk={report.risk_label}, "
            f"cex_funded={report.cex_funded}"
        )

        return report

    # ── Funding Source Resolution ───────────────────────────────────────────

    async def _find_funding_source(self, address: str, chain: str) -> dict | None:
        """
        Find the funding source for a given address.

        Returns dict with 'from', 'tx_hash', and optionally 'timestamp'
        or None if no funding source can be found.

        For EVM: uses gettxlist to find the earliest incoming transaction.
        For Solana: uses Solscan account transfers filtered by 'in' flow.
        """
        if is_solana(chain):
            return await self._find_funding_source_solana(address)
        elif is_evm(chain):
            return await self._find_funding_source_evm(address, chain)
        else:
            logger.warning(f"Unsupported chain: {chain}")
            return None

    async def _find_funding_source_evm(self, address: str, chain: str) -> dict | None:
        """Find the earliest incoming funding TX for an EVM address via block explorer API."""
        net = ETHERSCAN_NETWORKS.get(chain.lower())
        if not net:
            logger.warning(f"No Etherscan network config for chain: {chain}")
            return None

        url = net["url"]
        api_key = net["key"]

        params = {
            "module": "account",
            "action": "txlist",
            "address": address,
            "startblock": 0,
            "endblock": 99999999,
            "page": 1,
            "offset": 50,
            "sort": "asc",  # Oldest first
        }
        if api_key:
            params["apikey"] = api_key

        try:
            resp = await self._http.get(url, params=params, timeout=15.0)
            if resp.status_code != 200:
                logger.warning(f"Etherscan API returned {resp.status_code} for {address}")
                return None

            data = resp.json()
            if data.get("status") != "1":
                logger.debug(f"Etherscan API status not OK for {address}: {data.get('message', '')}")
                return None

            txs = data.get("result", [])
            if not txs:
                return None

            # Find first incoming (value > 0) transaction to this address
            addr_lower = address.lower()
            for tx in txs:
                to_addr = tx.get("to", "").lower()
                value = int(tx.get("value", "0"))
                if to_addr == addr_lower and value > 0:
                    return {
                        "from": tx.get("from", ""),
                        "tx_hash": tx.get("hash", ""),
                        "timestamp": int(tx.get("timeStamp", 0)),
                    }

            return None
        except httpx.TimeoutException:
            logger.warning(f"Etherscan API timeout for {address} on {chain}")
            return None
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
            logger.warning(f"Etherscan API error for {address}: {e}")
            return None

    async def _find_funding_source_solana(self, address: str) -> dict | None:
        """Find the earliest incoming funding transfer for a Solana address."""
        try:
            # Try account transfers first (incoming SOL/SPL)
            transfers = FreeSolscanClient.account_transfers(address, page=1, page_size=50, flow="in")
            if transfers and len(transfers) > 0:
                # Sort by block_time ascending (oldest first)
                sorted_tx = sorted(transfers, key=lambda x: x.get("block_time", 0))
                earliest = sorted_tx[0]
                return {
                    "from": earliest.get("fromAddress", earliest.get("from", "")),
                    "tx_hash": earliest.get("trans_id", earliest.get("txHash", "")),
                    "timestamp": earliest.get("block_time", 0),
                }

            # Fall back to account transactions
            txs = FreeSolscanClient.account_transactions(address, page=1, page_size=50)
            if txs and len(txs) > 0:
                sorted_tx = sorted(txs, key=lambda x: x.get("block_time", 0))
                earliest = sorted_tx[0]
                # Determine sender - depends on Solscan's response format
                sender = (
                    earliest.get("signer", [None])[0]
                    if isinstance(earliest.get("signer"), list)
                    else earliest.get("signer", "")
                )
                if not sender:
                    sender = earliest.get("fromAddress", "")
                if not sender:
                    sender = earliest.get("owner", "")
                return {
                    "from": sender,
                    "tx_hash": earliest.get("trans_id", earliest.get("txHash", "")),
                    "timestamp": earliest.get("block_time", 0),
                }

            return None
        except (httpx.HTTPError, KeyError, TypeError) as e:
            logger.debug("_find_funding_source_solana_error", exc_info=True)
            logger.warning(f"Solscan funding source error for {address}: {e}")
            return None

    # ── Wallet Classification ──────────────────────────────────────────────

    async def _classify_wallet(self, address: str, chain: str) -> dict:
        """
        Classify a wallet in the trace chain.

        Returns dict with:
          - relationship: human-readable label
          - first_seen: ISO timestamp or "unknown"
          - is_cex: bool
          - cex_name: str
          - is_fresh: bool
          - tx_count: int
          - is_dead_end: bool
          - dead_end_reason: str
        """
        result = {
            "relationship": "funder",
            "first_seen": "unknown",
            "is_cex": False,
            "cex_name": "",
            "is_fresh": False,
            "tx_count": 0,
            "is_dead_end": False,
            "dead_end_reason": "",
        }

        # ── Step 1: Check if known CEX hot wallet ──
        cex_name = await self._identify_cex_wallet(address, chain)
        if cex_name:
            result["is_cex"] = True
            result["cex_name"] = cex_name
            result["relationship"] = f"cex_hot_wallet_{cex_name}"

            # Get TX count for CEX wallet
            tx_count = await self._get_wallet_tx_count(address, chain)
            result["tx_count"] = tx_count

            # CEX with >1000 TXs = legitimate, stop here
            if tx_count >= self.CEX_TX_THRESHOLD:
                result["is_dead_end"] = True
                result["dead_end_reason"] = "cex_legitimate"
                result["relationship"] = f"cex_hot_wallet_{cex_name}"
            else:
                # CEX wallet with low TX count - suspicious, flag as dead end
                result["is_dead_end"] = True
                result["dead_end_reason"] = "fresh_cex_wallet"
                result["is_fresh"] = True
                result["relationship"] = f"fresh_cex_wallet_{cex_name}"

            # Get first_seen
            first_seen = await self._get_wallet_first_seen(address, chain)
            result["first_seen"] = first_seen.get("first_seen", "unknown")

            return result

        # ── Step 2: Get wallet age and TX count ──
        if is_solana(chain):
            wallet_info = await self._get_solana_wallet_info(address)
        else:
            wallet_info = await self._get_evm_wallet_info(address, chain)

        first_seen_ts = wallet_info.get("first_seen_ts", 0)
        tx_count = wallet_info.get("tx_count", 0)
        result["tx_count"] = tx_count
        result["first_seen"] = wallet_info.get("first_seen", "unknown")

        # ── Step 3: Check if fresh wallet (created within 7 days, <2 TXs) ──
        is_fresh = await self._check_fresh_wallet(first_seen_ts, tx_count, chain)
        result["is_fresh"] = is_fresh

        if is_fresh:
            result["is_dead_end"] = True
            result["dead_end_reason"] = "fresh_wallet"
            result["relationship"] = "fresh_wallet (no history)"
            return result

        # ── Step 4: Not CEX, not fresh - continue tracing
        if tx_count == 0:
            # Wallet with 0 TXs - dead end
            result["is_dead_end"] = True
            result["dead_end_reason"] = "empty_wallet"
            result["relationship"] = "empty_wallet"

        return result

    async def _identify_cex_wallet(self, address: str, chain: str) -> str:
        """Check if an address is a known CEX hot wallet. Returns CEX name or empty string."""
        addr_lower = address.lower()

        # Check chain's CEX wallets from registry
        cex_wallets = get_cex_wallets(chain)
        for exchange_name, addresses in cex_wallets.items():
            for cex_addr in addresses:
                if addr_lower == cex_addr.lower():
                    return exchange_name

        # Also check Solscan's known exchange list for Solana
        if is_solana(chain):
            result = is_known_exchange(address) or ""
            return result

        return ""

    async def _check_fresh_wallet(self, first_seen_ts: int, tx_count: int, chain: str) -> bool:
        """
        Determine if a wallet is 'fresh' - created within FRESH_WALLET_DAYS
        and has virtually no history.
        """
        if tx_count > 2:
            return False  # Has activity

        if first_seen_ts == 0:
            return True  # Cannot determine age → treat as fresh

        now = datetime.now(UTC)
        try:
            # first_seen_ts can be in seconds or milliseconds
            if first_seen_ts > 1_000_000_000_000:  # Milliseconds
                wallet_created = datetime.fromtimestamp(first_seen_ts / 1000, tz=UTC)
            else:  # Seconds
                wallet_created = datetime.fromtimestamp(first_seen_ts, tz=UTC)

            age = now - wallet_created
            return age <= timedelta(days=self.FRESH_WALLET_DAYS)
        except (ValueError, OSError):
            return True  # On error, treat as fresh

    # ── Wallet Info Fetchers ───────────────────────────────────────────────────

    async def _get_wallet_first_seen(self, address: str, chain: str) -> dict:
        """Get the first seen timestamp for a wallet."""
        if is_solana(chain):
            return await self._get_solana_wallet_info(address)
        else:
            return await self._get_evm_wallet_info(address, chain)

    async def _get_wallet_tx_count(self, address: str, chain: str) -> int:
        """Get the total transaction count for a wallet."""
        if is_solana(chain):
            info = await self._get_solana_wallet_info(address)
        else:
            info = await self._get_evm_wallet_info(address, chain)
        return info.get("tx_count", 0)

    async def _get_solana_wallet_info(self, address: str) -> dict:
        """
        Get Solana wallet info: first_seen timestamp and approximate TX count.

        Uses Solscan's account info and transaction/transfer endpoints.
        """
        result = {"first_seen": "unknown", "first_seen_ts": 0, "tx_count": 0}

        try:
            # Get account info (includes creation info on Solscan)
            acct_info = FreeSolscanClient.account_info(address)
            if acct_info and isinstance(acct_info, dict):
                # Some Solscan responses have 'createdTime' or similar
                created = acct_info.get("createdTime", 0)
                if created:
                    result["first_seen_ts"] = int(created)
                    try:
                        dt = datetime.fromtimestamp(
                            int(created) / 1000 if int(created) > 1_000_000_000_000 else int(created),
                            tz=UTC,
                        )
                        result["first_seen"] = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                    except (ConnectionError, OSError, TimeoutError):
                        pass

            # Get transaction count (first page gives us an idea)
            txs = FreeSolscanClient.account_transactions(address, page=1, page_size=20)
            if txs and isinstance(txs, list):
                result["tx_count"] = len(txs)

                # If we have txs, get the earliest timestamp
                if not result["first_seen_ts"]:
                    sorted_tx = sorted(txs, key=lambda x: x.get("block_time", 0))
                    if sorted_tx:
                        bt = sorted_tx[0].get("block_time", 0)
                        if bt:
                            result["first_seen_ts"] = int(bt)
                            try:
                                dt = datetime.fromtimestamp(int(bt), tz=UTC)
                                result["first_seen"] = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                            except (ConnectionError, OSError, TimeoutError):
                                pass

            # Also check transfers to get a more complete picture
            transfers = FreeSolscanClient.account_transfers(address, page=1, page_size=20)
            if transfers and isinstance(transfers, list):
                tx_count = result.get("tx_count", 0)
                result["tx_count"] = max(tx_count, len(transfers))

        except (httpx.HTTPError, ValueError, TypeError, KeyError) as e:
            logger.debug("unknown_error", exc_info=True)
            logger.warning(f"Solscan wallet info error for {address}: {e}")

        return result

    async def _get_evm_wallet_info(self, address: str, chain: str) -> dict:
        """
        Get EVM wallet info from block explorer API.

        Uses txlist to find first transaction and count total TXs.
        """
        result = {"first_seen": "unknown", "first_seen_ts": 0, "tx_count": 0}

        net = ETHERSCAN_NETWORKS.get(chain.lower())
        if not net:
            return result

        url = net["url"]
        api_key = net["key"]

        # Get first transaction (oldest) for first_seen timestamp
        params = {
            "module": "account",
            "action": "txlist",
            "address": address,
            "startblock": 0,
            "endblock": 99999999,
            "page": 1,
            "offset": 1,
            "sort": "asc",
        }
        if api_key:
            params["apikey"] = api_key

        try:
            resp = await self._http.get(url, params=params, timeout=15.0)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "1":
                    txs = data.get("result", [])
                    if txs:
                        first_tx = txs[0]
                        ts = int(first_tx.get("timeStamp", 0))
                        if ts:
                            result["first_seen_ts"] = ts
                            try:
                                dt = datetime.fromtimestamp(ts, tz=UTC)
                                result["first_seen"] = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                            except (ConnectionError, OSError, TimeoutError):
                                pass
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as e:
            logger.debug("unknown_error", exc_info=True)
            logger.warning(f"EVM wallet info error for {address}: {e}")

        # Get total TX count using txlist count endpoint
        try:
            count_params = dict(params)  # Copy base params
            count_params["action"] = "txlist"
            count_params["offset"] = 10000  # Max results
            count_params["page"] = 1
            count_params["sort"] = "desc"

            count_resp = await self._http.get(url, params=count_params, timeout=15.0)
            if count_resp.status_code == 200:
                count_data = count_resp.json()
                if count_data.get("status") == "1":
                    all_txs = count_data.get("result", [])
                    result["tx_count"] = len(all_txs)
        except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug("unknown_error", exc_info=True)
            logger.warning(f"EVM TX count error for {address}: {e}")

        return result

    # ── Risk Calculation ───────────────────────────────────────────────────

    def _calculate_risk_label(self, report: GasTraceReport) -> str:
        """
        Calculate risk label based on trace results.

        CRITICAL: Fresh wallet dead end (sniper/rug pattern)
        HIGH: Dead end for other reasons, or CEX funded with short chain
        MEDIUM: CEX funded but chain stops at legitimate CEX
        LOW: Chain resolves naturally or unknown
        """
        if not report.dead_end and report.cex_funded:
            return "MEDIUM"

        if not report.dead_end:
            return "LOW"

        if report.dead_end_reason == "fresh_wallet":
            return "CRITICAL"

        if report.dead_end_reason == "fresh_cex_wallet":
            return "CRITICAL"

        if report.dead_end_reason == "empty_wallet":
            return "CRITICAL"

        if report.dead_end_reason == "no_funding_tx":
            # Deployer funded directly with no traceable source
            if report.hop_count == 0:
                return "HIGH"
            return "MEDIUM"

        if report.dead_end_reason == "api_error":
            return "MEDIUM"

        if report.dead_end_reason == "cex_legitimate":
            return "LOW"

        if report.dead_end_reason == "cycle_detected":
            return "LOW"

        return "LOW"


# ── Standalone helper (matching pipeline pattern) ─────────────────────────


async def run_gas_trace_analysis(
    deployer_address: str,
    chain: str,
) -> GasTraceReport:
    """
    Standalone entry point for gas trace analysis.

    Creates a GasTraceAnalyzer, runs analysis, returns the report.
    Matches the SENTINEL pipeline callable pattern.
    """
    analyzer = GasTraceAnalyzer()
    return await analyzer.analyze(deployer_address, chain)
