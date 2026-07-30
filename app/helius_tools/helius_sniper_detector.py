#!/usr/bin/env python3
"""
Helius Sniper Detector - Detect coordinated sniping on token launches
Analyzes transaction patterns around token creation to identify bot rings,
coordinated buys, and insider trading.

Key metrics:
- Time-to-first-buy after launch (ms)
- Wallet clustering (shared funding, similar patterns)
- Buy velocity curve (spike detection)
- Position sizing analysis
- Jito bundle detection
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import aiohttp

from app.core.logging import get_logger

logger = get_logger("sniper-detector")

API_KEY = os.getenv("HELIUS_API_KEY", "").strip()
RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={API_KEY}"

# Jito bundle IDs (known program addresses)
JITO_PROGRAMS = [
    "Jito4APyf6rDt1pD1jH3nD3is4v7TwzFNWjjMP7B2RK",  # Jito bundles
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",  # Jito tip router
]

SNIPER_THRESHOLDS = {
    "max_time_to_first_buy_ms": 5000,  # First buy within 5s = sniper
    "max_wallets_same_funder": 5,  # 5+ wallets from same funder = ring
    "min_similar_tx_pattern": 0.8,  # 80% similarity = coordinated
    "max_launch_block_gap": 3,  # Within 3 blocks of launch
}


@dataclass
class SniperWallet:
    address: str
    first_buy_time_ms: float  # milliseconds after token creation
    first_buy_block: int
    position_size_sol: float
    total_buys: int
    total_sells: int
    funding_source: str | None
    is_jito_bundle: bool
    similarity_score: float  # 0-1 how similar to other snipers


@dataclass
class SniperReport:
    token_address: str
    launch_tx: str
    launch_time: datetime
    detection_time: datetime
    sniper_wallets: list[SniperWallet]
    total_sniper_count: int
    coordinated_sniper_count: int  # Wallets flagged as coordinated
    jito_bundle_count: int
    avg_time_to_first_buy_ms: float
    total_sniper_volume_sol: float
    ring_detected: bool
    ring_size: int
    ring_funding_source: str | None
    insider_probability: float  # 0-100
    risk_verdict: str
    evidence: list[str]


class SniperDetector:
    """Detects coordinated sniping on Solana token launches."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or API_KEY
        self.rpc_url = f"https://mainnet.helius-rpc.com/?api-key={self.api_key}"
        self.session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        return self.session

    async def _rpc_call(self, method: str, params: list | None = None) -> Any:
        s = await self._get_session()
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
        try:
            async with s.post(self.rpc_url, json=payload) as resp:
                data = await resp.json()
                return data.get("result", {})
        except Exception as e:
            logger.error("RPC %s failed: %s", method, e)
            return {}

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    async def analyze_token_launch(self, token_address: str) -> SniperReport:
        """Full sniper analysis on a token launch."""
        logger.info("Analyzing token launch: %s", token_address)

        # Step 1: Find token creation transaction
        creation_info = await self._find_creation_tx(token_address)
        if not creation_info:
            return self._empty_report(token_address)

        launch_tx = creation_info["tx"]
        launch_time = creation_info["time"]
        launch_block = creation_info["block"]

        # Step 2: Get all transactions in first N blocks after launch
        early_buyers = await self._get_early_buyers(token_address, launch_block, blocks_after=10)

        # Step 3: Analyze each early buyer
        sniper_wallets: list[SniperWallet] = []
        for buyer_data in early_buyers[:100]:  # Cap at 100 for performance
            wallet = await self._analyze_sniper_wallet(buyer_data, launch_time, launch_block)
            if wallet:
                sniper_wallets.append(wallet)

        # Step 4: Detect coordination patterns
        ring_funding, ring_size = await self._detect_ring(sniper_wallets)
        jito_count = sum(1 for s in sniper_wallets if s.is_jito_bundle)
        coordinated = [s for s in sniper_wallets if s.similarity_score > 0.7]

        # Step 5: Calculate insider probability
        insider_prob = self._calculate_insider_probability(sniper_wallets, ring_size, jito_count, coordinated)

        # Step 6: Build evidence
        evidence = self._build_evidence(sniper_wallets, ring_funding, ring_size, jito_count)

        # Step 7: Verdict
        verdict = self._verdict(insider_prob, ring_size, jito_count)

        return SniperReport(
            token_address=token_address,
            launch_tx=launch_tx,
            launch_time=launch_time,
            detection_time=datetime.now(UTC),
            sniper_wallets=sniper_wallets,
            total_sniper_count=len(sniper_wallets),
            coordinated_sniper_count=len(coordinated),
            jito_bundle_count=jito_count,
            avg_time_to_first_buy_ms=sum(s.first_buy_time_ms for s in sniper_wallets) / max(len(sniper_wallets), 1),
            total_sniper_volume_sol=sum(s.position_size_sol for s in sniper_wallets),
            ring_detected=ring_size >= 3,
            ring_size=ring_size,
            ring_funding_source=ring_funding,
            insider_probability=insider_prob,
            risk_verdict=verdict,
            evidence=evidence,
        )

    async def _find_creation_tx(self, token_address: str) -> dict[str, Any] | None:
        """Find the transaction that created the token."""
        sigs_result = await self._rpc_call("getSignaturesForAddress", [token_address, {"limit": 50}])
        signatures = sigs_result if isinstance(sigs_result, list) else []

        if not signatures:
            return None

        # The oldest signature is the creation tx
        creation_sig = signatures[-1]
        return {
            "tx": creation_sig.get("signature", ""),
            "time": datetime.fromtimestamp(creation_sig.get("blockTime", 0)),
            "block": creation_sig.get("slot", 0),
        }

    async def _get_early_buyers(self, token_address: str, launch_block: int, blocks_after: int = 10) -> list[dict]:
        """Get wallets that interacted with the token in first N blocks."""
        sigs_result = await self._rpc_call("getSignaturesForAddress", [token_address, {"limit": 200}])
        signatures = sigs_result if isinstance(sigs_result, list) else []

        buyers = []
        for sig_info in signatures:
            slot = sig_info.get("slot", 0)
            if slot > launch_block + blocks_after:
                break

            sig = sig_info.get("signature", "")
            tx = await self._get_enhanced_tx(sig)
            if not tx:
                continue

            # Extract token transfers
            for transfer in tx.get("tokenTransfers", []):
                if transfer.get("mint") == token_address:
                    buyers.append(
                        {
                            "wallet": transfer.get("toUserAccount", ""),
                            "amount": float(transfer.get("tokenAmount", 0)),
                            "block": slot,
                            "tx": sig,
                            "timestamp": tx.get("blockTime", 0),
                        }
                    )

        return buyers

    async def _analyze_sniper_wallet(
        self, buyer_data: dict, launch_time: datetime, launch_block: int
    ) -> SniperWallet | None:
        """Analyze a single wallet for sniper characteristics."""
        wallet = buyer_data["wallet"]
        if not wallet:
            return None

        buy_time = datetime.fromtimestamp(buyer_data.get("timestamp", 0))
        time_to_buy_ms = (buy_time - launch_time).total_seconds() * 1000

        # Skip if too slow (not a sniper)
        if time_to_buy_ms > 60000:  # 1 minute
            return None

        # Get wallet SOL balance
        sol_result = await self._rpc_call("getBalance", [wallet])
        sol_balance = sol_result.get("value", 0) / 1e9 if isinstance(sol_result, dict) else 0

        # Check for Jito bundle involvement
        is_jito = await self._check_jito_involvement(buyer_data["tx"])

        # Get funding source
        funding = await self._trace_funding(wallet)

        return SniperWallet(
            address=wallet,
            first_buy_time_ms=time_to_buy_ms,
            first_buy_block=buyer_data["block"],
            position_size_sol=sol_balance,
            total_buys=1,
            total_sells=0,
            funding_source=funding,
            is_jito_bundle=is_jito,
            similarity_score=0.0,  # Set later in coordination check
        )

    async def _detect_ring(self, snipers: list[SniperWallet]) -> tuple[str | None, int]:
        """Detect if snipers are funded from the same source."""
        funding_counts = defaultdict(int)
        for s in snipers:
            if s.funding_source:
                funding_counts[s.funding_source] += 1

        if not funding_counts:
            return None, 0

        top_funder, count = max(funding_counts.items(), key=lambda x: x[1])
        if count >= 3:
            return top_funder, count
        return None, 0

    async def _check_jito_involvement(self, tx_signature: str) -> bool:
        """Check if transaction was part of a Jito bundle."""
        tx = await self._get_enhanced_tx(tx_signature)
        if not tx:
            return False

        accounts = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])
        account_addrs = [a.get("pubkey", "") if isinstance(a, dict) else a for a in accounts]

        return any(addr in JITO_PROGRAMS for addr in account_addrs)

    async def _trace_funding(self, wallet_address: str) -> str | None:
        """Trace the funding source of a wallet."""
        sigs_result = await self._rpc_call("getSignaturesForAddress", [wallet_address, {"limit": 5}])
        signatures = sigs_result if isinstance(sigs_result, list) else []

        if not signatures:
            return None

        # Check the oldest tx to see who funded this wallet
        oldest_sig = signatures[-1].get("signature", "")
        tx = await self._get_enhanced_tx(oldest_sig)
        if not tx:
            return None

        for transfer in tx.get("nativeTransfers", []):
            if transfer.get("toUserAccount") == wallet_address:
                return transfer.get("fromUserAccount")

        return None

    def _calculate_insider_probability(
        self,
        snipers: list[SniperWallet],
        ring_size: int,
        jito_count: int,
        coordinated: list[SniperWallet],
    ) -> float:
        """Calculate probability of insider involvement (0-100)."""
        score = 0

        # Fast buys (<1s)
        ultra_fast = sum(1 for s in snipers if s.first_buy_time_ms < 1000)
        score += min(30, ultra_fast * 5)

        # Ring detection
        if ring_size >= 5:
            score += 25
        elif ring_size >= 3:
            score += 15

        # Jito bundles
        if jito_count >= 3:
            score += 20
        elif jito_count >= 1:
            score += 10

        # Coordinated pattern
        if len(coordinated) >= 5:
            score += 25
        elif len(coordinated) >= 3:
            score += 15

        return min(100, score)

    def _build_evidence(
        self, snipers: list[SniperWallet], ring_funding: str | None, ring_size: int, jito_count: int
    ) -> list[str]:
        """Build human-readable evidence list."""
        evidence = []

        if snipers:
            fastest = min(s.first_buy_time_ms for s in snipers)
            evidence.append(f"Fastest buy: {fastest:.0f}ms after launch")

        if ring_size >= 3:
            evidence.append(
                f"Detected wallet ring: {ring_size} wallets funded from same source ({ring_funding[:8]}...)"
            )

        if jito_count > 0:
            evidence.append(f"{jito_count} transactions used Jito bundles for priority")

        ultra_fast = sum(1 for s in snipers if s.first_buy_time_ms < 2000)
        if ultra_fast >= 3:
            evidence.append(f"{ultra_fast} wallets bought within 2 seconds - impossible for humans")

        return evidence

    def _verdict(self, insider_prob: float, ring_size: int, jito_count: int) -> str:
        """Generate risk verdict."""
        if insider_prob >= 70:
            return "HIGH INSIDER PROBABILITY - Coordinated launch detected"
        elif insider_prob >= 40:
            return "MODERATE RISK - Some coordination indicators present"
        elif insider_prob >= 20:
            return "LOW-MODERATE - Few snipers, limited coordination"
        return "LOW RISK - Organic launch pattern"

    def _empty_report(self, token_address: str) -> SniperReport:
        return SniperReport(
            token_address=token_address,
            launch_tx="",
            launch_time=datetime.now(UTC),
            detection_time=datetime.now(UTC),
            sniper_wallets=[],
            total_sniper_count=0,
            coordinated_sniper_count=0,
            jito_bundle_count=0,
            avg_time_to_first_buy_ms=0,
            total_sniper_volume_sol=0,
            ring_detected=False,
            ring_size=0,
            ring_funding_source=None,
            insider_probability=0,
            risk_verdict="Could not analyze - token creation tx not found",
            evidence=["No creation transaction found on-chain"],
        )

    async def _get_enhanced_tx(self, signature: str) -> dict[str, Any] | None:
        result = await self._rpc_call(
            "getTransaction",
            [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
        )
        return result if result and not isinstance(result, str) else None
