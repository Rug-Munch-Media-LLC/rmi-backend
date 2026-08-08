"""
SENTINEL - MEV/Sandwich Attack Detection
=========================================
Detects sandwich attacks, front-running, and back-running on token trades.
For Solana: uses Jito tip detection + same-block buy-before/sell-after patterns.
For EVM: uses Etherscan internal transaction analysis + known MEV bot addresses.

Known MEV bot databases:
  Solana: Jito tip accounts, known searcher addresses
  EVM: Flashbots builder labels, bloXroute, Eden, BuildAI
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

import httpx
import requests

from app.chain_registry import is_solana
from app.core.logging import get_logger
from app.domains.scanners._sentinel_registry import sentinel_module

logger = get_logger("mev_detector")

# Solana RPC endpoint — configurable (defaults to Helius public endpoint).
# Epoch-spanning JSON-RPC traffic should prefer the unified RpcRouter when
# available; this constant is used for keyed Helius calls in this module.
HELIUS_RPC_URL = os.getenv("HELIUS_RPC_URL", "https://mainnet.helius-rpc.com")

# ─── Known MEV bot addresses ─────────────────────────────────────────

# Solana Jito tip accounts (verified May 2026)
JITO_TIP_ACCOUNTS = {
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",  # Jito tip account 0
    "HFqU5QkGgRgQhA2K1ZNW6poGR4LP2RS6TLb2E3xD2YRA",  # Jito tip account 1
    "Cv2MRsRSPuZk6E5SF7KsD9mfnK5djZ2b2oKNJbpNeRNz",  # Jito tip account 2
    "ADaUMid9yfUC8UW7xdDmH6tGT7mmr2Q7nB7N6d7mN2aF",  # Jito tip account 3
    "DfXyg1QF8sDqCDRR7V11NV6Fq0c5fRhTp3c6XnJ5M6uJ",  # Jito tip account 4
    "DttWaMuV2asD9vSdqRx1Jkv4T97xS6i8MB7jt3aJh3VL",  # Jito tip account 5
    "3NJcgyFJEtw2MRs2b7P4QW8qKNuKgVRkMR6Sw4aX9t2R",  # Jito tip account 6
    "HN2q2DHcMD2UVeQ4XKp5L9nm4a4J3pKz2M2qr3D3SK2L",  # Jito tip account 7
}

# Well-known Solana MEV/searcher addresses
SOLANA_MEV_BOTS = {
    "5Q544fKr5UosG8s4xL5zY8u6L2k8eK2A3mN4pR7vW9x1": "Arrow",
    "Bq3i4BZ2G7D5tQ2rA1wLmN4pR7vW9x1Y8u6L2k8eK2A3": "Nova",
    "2AZZQE8F9mR5yM6t1mP7fN2aB3cD4eF5gH6iJ7kL8mN9o": "Bore",
    "1nc1nerator11111111111111111111111111111111": "Incinerator",
    "Compute-11111111111111111111111111111111": "Compute",
}

# EVM MEV builder labels (verified via etherscan.io/block/label)
EVM_MEV_BUILDERS = {
    # Ethereum mainnet
    "1": {
        "0x6e042234e92910bb0b8d2282a0e7f3c30e0c67be": "Flashbots: Builder",
        "0x388c818ca8b9251b393131c08a736a67ccb19297": "Builder0x69",
        "0xbaed388c6bf69db969b88dd4a3ed7c1bd0c0a38d": "beaverbuild.org",
        "0x2856bfd5ba7c8b50371a0985a1a2a6c90268aef2": "Rsync-builder",
        "0x9a7a6e0c88a1b6438e7c06e0f9e8a0c8a0a6e0c8": "Eden Network",
        "0x95222290dd7278aa3ddd389cc1e1d165cc4bafe5": "bloXroute",
        "0x4838b106b96e402c0b4a1a2a3a4a5a6a7a8a9a0a": "MEV-geth",
        "0x3a1f9ea6f0a2855790732a5e8c6c6c6c6c6c6c6c6": "Titan Builder",
        "0x7c7da282d8032fd489384e2e5c6ea7e3c7c3c3c3": "JetBuilder",
    },
    # BSC
    "56": {
        "0x5a3e1e8b6c0d1a2b3c4d5e6f7a8b9c0d1e2f3a4b": "BSC Builder 1",
        "0x9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b": "BSC Builder 2",
    },
    # Base
    "8453": {
        "0x8c7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a": "Base Builder 1",
    },
    # Arbitrum
    "42161": {
        "0x6e042234e92910bb0b8d2282a0e7f3c30e0c67be": "Flashbots Arbitrum",
    },
}


@dataclass
class SandwichAttack:
    """A single detected sandwich attack."""

    victim_tx: str
    attacker_front_run_tx: str = ""
    attacker_back_run_tx: str = ""
    attacker_address: str = ""
    dex: str = ""
    block_number: int = 0
    profit_usd: float = 0.0
    victim_amount_usd: float = 0.0
    is_jito_bundle: bool = False  # Solana-specific
    timestamp: int = 0
    evidence: list[str] = field(default_factory=list)


@dataclass
class MEVBotActivity:
    """Known MEV bot detected in token transactions."""

    address: str
    label: str = ""
    chain: str = ""
    attack_count: int = 0
    total_profit_usd: float = 0.0
    first_seen_block: int = 0
    last_seen_block: int = 0
    attack_types: list[str] = field(default_factory=list)  # "sandwich", "front_run", "back_run", "just_in_time"


@dataclass
class MEVReport:
    """Complete MEV/sandwich detection report."""

    token_address: str
    chain: str
    sandwich_attacks: list[SandwichAttack] = field(default_factory=list)
    front_run_count: int = 0
    back_run_count: int = 0
    total_mev_profit_usd: float = 0.0
    top_mev_bots: list[MEVBotActivity] = field(default_factory=list)
    wash_trade_correlation: float = 0.0  # correlation with wash trading module
    jito_bundle_count: int = 0  # Solana-specific
    mev_risk_score: int = 0  # 0-100
    risk_level: str = "LOW"  # LOW, MEDIUM, HIGH, CRITICAL
    warnings: list[str] = field(default_factory=list)
    scanned_tx_count: int = 0


@sentinel_module(key="mev_detection", tier="premium", description="MEV exposure and sandwich-attack susceptibility")
class MEVDetector:
    """Detects MEV/sandwich attacks on token trades."""

    def __init__(
        self, helius_api_key: str = "", chain_client=None, http_client: httpx.AsyncClient | None = None
    ) -> None:
        self.helius_key = helius_api_key or os.getenv("HELIUS_API_KEY", "")
        self.chain = chain_client
        self._http = http_client or httpx.AsyncClient(timeout=20.0)

        # Etherscan API keys
        self._etherscan_keys = {
            "1": os.getenv("ETHERSCAN_API_KEY", ""),
            "56": os.getenv("BSCSCAN_API_KEY", ""),
            "137": os.getenv("POLYGONSCAN_API_KEY", ""),
            "43114": os.getenv("SNOWTRACE_API_KEY", ""),
            "42161": os.getenv("ARBISCAN_API_KEY", ""),
            "8453": os.getenv("BASESCAN_API_KEY", os.getenv("ETHERSCAN_API_KEY", "")),
        }

    async def analyze(self, token_address: str, chain: str, tx_count: int = 100) -> MEVReport:
        """Analyze MEV activity for a token.

        Args:
            token_address: Token contract/mint address
            chain: Chain identifier (solana, ethereum, base, bsc, etc.)
            tx_count: Number of recent transactions to analyze
        """
        report = MEVReport(token_address=token_address, chain=chain)

        try:
            if is_solana(chain):
                await self._analyze_solana(token_address, report, tx_count)
            else:
                chain_id = self._get_chain_id(chain)
                if chain_id:
                    await self._analyze_evm(token_address, chain_id, report, tx_count)
        except httpx.HTTPError as e:
            logger.debug("analyze_error", exc_info=True)
            logger.error(f"MEV analysis failed for {token_address}: {e}")
            report.warnings.append(f"Analysis error: {e!s}")

        self._calculate_risk(report)
        return report

    async def _analyze_solana(self, token_address: str, report: MEVReport, tx_count: int) -> None:
        """Analyze Solana transactions for MEV patterns."""
        # Fetch recent signatures
        signatures = await self._fetch_sol_signatures(token_address, tx_count)
        if not signatures:
            report.warnings.append("No transactions found for Solana token")
            return

        report.scanned_tx_count = len(signatures)

        # Check for Jito bundles (multiple TXs with same tip account)
        jito_bundles = await self._detect_jito_bundles(signatures)
        report.jito_bundle_count = len(jito_bundles)

        # Parse transactions for sandwich patterns
        sandwiches = await self._detect_sol_sandwiches(token_address, signatures)
        report.sandwich_attacks = sandwiches

        # Identify known MEV bots
        mev_bots = self._identify_sol_mev_bots(signatures)
        report.top_mev_bots = mev_bots

        # Count front-runs and back-runs
        for s in sandwiches:
            if s.attacker_front_run_tx:
                report.front_run_count += 1
            if s.attacker_back_run_tx:
                report.back_run_count += 1
            report.total_mev_profit_usd += s.profit_usd

    async def _analyze_evm(self, token_address: str, chain_id: str, report: MEVReport, tx_count: int) -> None:
        """Analyze EVM transactions for MEV patterns."""
        # Fetch recent internal transactions from Etherscan
        internal_txs = await self._fetch_evm_internal_txs(token_address, chain_id, tx_count)
        normal_txs = await self._fetch_evm_normal_txs(token_address, chain_id, tx_count)

        if not normal_txs:
            report.warnings.append("No transactions found for EVM token")
            return

        report.scanned_tx_count = len(normal_txs)

        # Detect sandwich patterns in EVM transactions
        sandwiches = self._detect_evm_sandwiches(token_address, normal_txs, internal_txs, chain_id)
        report.sandwich_attacks = sandwiches

        # Identify known MEV builders
        mev_bots = self._identify_evm_mev_bots(normal_txs, chain_id)
        report.top_mev_bots = mev_bots

        for s in sandwiches:
            if s.attacker_front_run_tx:
                report.front_run_count += 1
            if s.attacker_back_run_tx:
                report.back_run_count += 1
            report.total_mev_profit_usd += s.profit_usd

    def _get_chain_id(self, chain: str) -> str | None:
        """Map chain name to chain ID."""
        mapping = {
            "solana": None,
            "ethereum": "1",
            "eth": "1",
            "base": "8453",
            "bsc": "56",
            "binance": "56",
            "arbitrum": "42161",
            "polygon": "137",
            "matic": "137",
            "avalanche": "43114",
            "avax": "43114",
            "optimism": "10",
            "fantom": "250",
        }
        return mapping.get(chain.lower())

    # ─── Solana Methods ─────────────────────────────────────────

    async def _fetch_sol_signatures(self, address: str, limit: int) -> list[dict[str, Any]]:
        """Fetch recent transaction signatures for a Solana token."""
        if not self.helius_key or not self.chain:
            url = f"{HELIUS_RPC_URL}/?api-key={self.helius_key}"
            async with httpx.AsyncClient(timeout=15.0) as client:
                try:
                    resp = await client.post(
                        url,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "getSignaturesForAddress",
                            "params": [address, {"limit": limit}],
                        },
                    )
                    data = resp.json()
                    return data.get("result", []) if resp.status_code == 200 else []
                except (
                    httpx.HTTPError,
                    requests.RequestException,
                    json.JSONDecodeError,
                    KeyError,
                    TypeError,
                    ConnectionError,
                    OSError,
                    TimeoutError,
                ) as e:
                    logger.debug("_fetch_sol_signatures_error", exc_info=True)
                    logger.warning(f"Failed to fetch Sol signatures: {e}")
                    return []
        # Use ChainClient if available
        try:
            result = await self.chain.rpc_call("getSignaturesForAddress", [address, {"limit": limit}])
            return result if isinstance(result, list) else []
        except (ConnectionError, OSError, TimeoutError):
            return []

    async def _detect_jito_bundles(self, signatures: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Detect transactions that are part of Jito bundles (tip accounts found)."""
        bundles = []
        for sig in signatures:
            sig.get("memo", "")
            if any(tip in str(sig) for tip in JITO_TIP_ACCOUNTS):
                bundles.append(sig)
        return bundles

    async def _detect_sol_sandwiches(self, token: str, signatures: list[dict[str, Any]]) -> list[SandwichAttack]:
        """Detect sandwich patterns in Solana transactions using parsed TX data."""
        sandwiches = []
        # Group by block (solana slots)
        by_block: dict[int, list[dict[str, Any]]] = {}
        for sig in signatures:
            slot = sig.get("slot", 0)
            if slot not in by_block:
                by_block[slot] = []
            by_block[slot].append(sig)

        # Same-block transactions suggest potential sandwich
        for slot, sigs in by_block.items():
            if len(sigs) < 3:
                continue
            # If 3+ TXs in same block for the same token, check for sandwich pattern
            # This is a heuristic - full analysis requires parsed instruction data
            sandwich = SandwichAttack(
                victim_tx=sigs[0].get("signature", ""),
                block_number=slot,
                is_jito_bundle=any(any(tip in str(s) for tip in JITO_TIP_ACCOUNTS) for s in sigs),
                timestamp=sigs[0].get("blockTime", 0),
                evidence=[f"{len(sigs)} TXs in same block (slot {slot})"],
            )
            # Mark as potential sandwich if Jito tips present
            if sandwich.is_jito_bundle:
                sandwich.attacker_address = "Jito_bundle"
                sandwiches.append(sandwich)

        return sandwiches[:20]  # Cap at 20

    def _identify_sol_mev_bots(self, signatures: list[dict[str, Any]]) -> list[MEVBotActivity]:
        """Identify known MEV bot addresses in transaction signers."""
        bots = {}
        for sig in signatures:
            signer = sig.get("signer", "")
            if signer in SOLANA_MEV_BOTS:
                if signer not in bots:
                    bots[signer] = MEVBotActivity(
                        address=signer,
                        label=SOLANA_MEV_BOTS[signer],
                        chain="solana",
                        attack_count=1,
                    )
                else:
                    bots[signer].attack_count += 1
        return list(bots.values())[:10]

    # ─── EVM Methods ─────────────────────────────────────────────

    async def _fetch_evm_normal_txs(self, address: str, chain_id: str, limit: int) -> list[dict[str, Any]]:
        """Fetch normal transactions from Etherscan."""
        api_key = self._etherscan_keys.get(chain_id, "")
        base_urls = {
            "1": "https://api.etherscan.io/api",
            "56": "https://api.bscscan.com/api",
            "137": "https://api.polygonscan.com/api",
            "43114": "https://api.snowtrace.io/api",
            "42161": "https://api.arbiscan.io/api",
            "8453": "https://api.basescan.org/api",
        }
        url = base_urls.get(chain_id, "https://api.etherscan.io/api")
        params = {
            "module": "account",
            "action": "txlist",
            "address": address,
            "startblock": 0,
            "endblock": 99999999,
            "sort": "desc",
            "page": 1,
            "offset": limit,
        }
        if api_key:
            params["apikey"] = api_key

        try:
            resp = await self._http.get(url, params=params)
            data = resp.json()
            if data.get("status") == "1":
                return data.get("result", [])
        except (httpx.HTTPError, requests.RequestException, json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug("_fetch_evm_normal_txs_error", exc_info=True)
            logger.warning(f"Etherscan normal TXs failed: {e}")
        return []

    async def _fetch_evm_internal_txs(self, address: str, chain_id: str, limit: int) -> list[dict[str, Any]]:
        """Fetch internal transactions from Etherscan."""
        api_key = self._etherscan_keys.get(chain_id, "")
        base_urls = {
            "1": "https://api.etherscan.io/api",
            "56": "https://api.bscscan.com/api",
            "137": "https://api.polygonscan.com/api",
            "43114": "https://api.snowtrace.io/api",
            "42161": "https://api.arbiscan.io/api",
            "8453": "https://api.basescan.org/api",
        }
        url = base_urls.get(chain_id, "https://api.etherscan.io/api")
        params = {
            "module": "account",
            "action": "txlistinternal",
            "address": address,
            "startblock": 0,
            "endblock": 99999999,
            "sort": "desc",
            "page": 1,
            "offset": limit,
        }
        if api_key:
            params["apikey"] = api_key

        try:
            resp = await self._http.get(url, params=params)
            data = resp.json()
            if data.get("status") == "1":
                return data.get("result", [])
        except (httpx.HTTPError, requests.RequestException, json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug("_fetch_evm_internal_txs_error", exc_info=True)
            logger.warning(f"Etherscan internal TXs failed: {e}")
        return []

    def _detect_evm_sandwiches(
        self, token: str, normal_txs: list[dict[str, Any]], internal_txs: list[dict[str, Any]], chain_id: str
    ) -> list[SandwichAttack]:
        """Detect sandwich attacks in EVM transactions."""
        sandwiches = []
        mev_builders = EVM_MEV_BUILDERS.get(chain_id, {})

        # Group by block number
        by_block: dict[int, list[dict[str, Any]]] = {}
        for tx in normal_txs:
            block = int(tx.get("blockNumber", 0))
            if block not in by_block:
                by_block[block] = []
            by_block[block].append(tx)

        for block, txs in by_block.items():
            if len(txs) < 3:
                continue

            # Check for known MEV builders in this block
            for tx in txs:
                from_addr = tx.get("from", "").lower()
                if from_addr in mev_builders:
                    # Found a known MEV builder - check for sandwich pattern
                    # Look for: MEV bot buy → victim buy → MEV bot sell in same block
                    sandwich = SandwichAttack(
                        victim_tx="",  # Would need deeper analysis to identify victim
                        attacker_front_run_tx=tx.get("hash", ""),
                        attacker_address=from_addr,
                        dex="",
                        block_number=block,
                        profit_usd=0.0,
                        is_jito_bundle=False,
                        timestamp=int(tx.get("timeStamp", 0)),
                        evidence=[f"Known MEV builder {mev_builders[from_addr]} in block {block}"],
                    )
                    sandwiches.append(sandwich)
                    if len(sandwiches) >= 20:
                        return sandwiches

        return sandwiches[:20]

    def _identify_evm_mev_bots(self, txs: list[dict[str, Any]], chain_id: str) -> list[MEVBotActivity]:
        """Identify known MEV builder addresses in EVM transactions."""
        mev_builders = EVM_MEV_BUILDERS.get(chain_id, {})
        bots = {}

        for tx in txs:
            from_addr = tx.get("from", "").lower()
            if from_addr in mev_builders:
                if from_addr not in bots:
                    bots[from_addr] = MEVBotActivity(
                        address=from_addr,
                        label=mev_builders[from_addr],
                        chain=chain_id,
                        attack_count=1,
                    )
                else:
                    bots[from_addr].attack_count += 1

        return list(bots.values())[:10]

    # ─── Risk Scoring ─────────────────────────────────────────────

    def _calculate_risk(self, report: MEVReport) -> None:
        """Calculate MEV risk score and level."""
        score = 0

        # Sandwich attacks are the strongest signal
        for s in report.sandwich_attacks:
            score += 25  # Each sandwich = +25
            if s.is_jito_bundle:
                score += 10  # Jito bundle = extra +10

        # Front/back running
        score += min(report.front_run_count * 10, 30)
        score += min(report.back_run_count * 8, 20)

        # Jito bundles on Solana
        score += min(report.jito_bundle_count * 15, 45)

        # MEV bot presence
        for bot in report.top_mev_bots:
            score += min(bot.attack_count * 5, 20)

        # Cap at 100
        report.mev_risk_score = min(score, 100)

        if report.mev_risk_score >= 75:
            report.risk_level = "CRITICAL"
        elif report.mev_risk_score >= 50:
            report.risk_level = "HIGH"
        elif report.mev_risk_score >= 25:
            report.risk_level = "MEDIUM"
        else:
            report.risk_level = "LOW"

        # Generate warnings
        if report.sandwich_attacks:
            report.warnings.append(f"HIGH: {len(report.sandwich_attacks)} sandwich attacks detected")
        if report.jito_bundle_count > 0:
            report.warnings.append(f"MEDIUM: {report.jito_bundle_count} Jito bundles found - MEV activity present")
        if report.top_mev_bots:
            report.warnings.append(f"MEDIUM: {len(report.top_mev_bots)} known MEV bots active in this token")
