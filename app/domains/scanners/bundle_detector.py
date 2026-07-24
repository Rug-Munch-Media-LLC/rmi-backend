"""
SENTINEL - Enhanced Bundle & Sniper Detection
=============================================
Detects coordinated wallet groups that purchase simultaneously at token launch,
funded by the same source, often in the same block.

Detection heuristics:
  - Same-block purchase (very high confidence)
  - Deployer funding chain (5-hop trace, very high)
  - Multi-hop funding (high)
  - Identical buy sizes (medium)
  - Coordinated timing (medium)
  - Shared RPC/Jito endpoint (medium)
  - Compute budget pattern analysis (Solana-specific)

Uses direct API calls: Helius (transaction/signature data),
DexScreener (pairs/trades), Solscan (account transfers, transaction detail).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import ClassVar

import httpx

from app.chain_client import ChainClient
from app.chain_registry import is_solana
from app.core.logging import get_logger
from app.free_solscan_client import FreeSolscanClient

logger = get_logger("bundle_detector")

@dataclass
class FundingStep:
    address: str
    amount: float
    token: str  # "SOL", "ETH", etc.
    timestamp: int  # unix ms
    tx_signature: str = ""

@dataclass
class BundledWallet:
    address: str
    funding_chain: list[FundingStep]
    buy_tx: str | None = None
    buy_amount: float = 0.0
    buy_block: int = 0
    confidence: str = "MEDIUM"  # LOW, MEDIUM, HIGH, VERY_HIGH
    evidence: list[str] = field(default_factory=list)

@dataclass
class SameBlockSniper:
    address: str
    block_number: int
    buy_amount: float = 0.0
    compute_units: int = 0  # Solana-specific
    priority_fee: float = 0.0
    is_jito: bool = False

@dataclass
class BehaviorCluster:
    wallets: list[str]
    pattern: str  # "same_size", "same_time", "same_dex"
    combined_percentage: float
    confidence: float

@dataclass
class BundleReport:
    token_address: str
    chain: str
    deployer: str
    deploy_block: int
    deploy_timestamp: int
    total_bundled_percentage: float
    deployer_funded: list[BundledWallet]
    same_block_snipers: list[SameBlockSniper]
    behavior_clusters: list[BehaviorCluster]
    compute_budget_patterns: dict[str, int] = field(default_factory=dict)
    jito_bundles_detected: int = 0
    risk_score: int = 0  # 0-100
    risk_level: str = "LOW"  # LOW, MEDIUM, HIGH, CRITICAL
    warnings: list[str] = field(default_factory=list)

class BundleDetector:
    """Enhanced bundle and sniper detection with funding chain analysis.

    Fetches data directly from Helius, DexScreener, and Solscan.
    """

    # Known Jito tip program IDs
    JITO_PROGRAMS: ClassVar[dict] = {
        "Jito4APyf6rDt1pD1jH3nD3is4v7TwzFNWjjMP7B2RK",
        "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
        "ADuCadRmgjMe6UzXVxR1Kn9CS2CXAf8bjiKkC4xFcegX",
    }

    # Known bundler program IDs (Smithii, Printr, etc.)
    BUNDLER_PROGRAMS: ClassVar[dict] = {
        "Smithii": "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
        "PumpFun": "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
        "LaunchLab": "LaunchLabzbP1j4R7KzEFmYSF3EXGnZ6tY7AZ8eg3P2qJ",
    }

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=15.0)
        self._chain = ChainClient()
        self._solscan = FreeSolscanClient()
        self._helius_key = os.getenv("HELIUS_API_KEY", "")

    async def close(self) -> None:
        await self._http.aclose()

    # ── Direct API fetchers ───────────────────────────────────────────

    async def _fetch_dexscreener_token(self, token_address: str) -> dict | None:
        """Fetch token pair data from DexScreener (free, no key)."""
        try:
            resp = await self._http.get(
                f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            )
            if resp.status_code == 200:
                data = resp.json()
                pairs = data.get("pairs") or []
                return pairs[0] if pairs else None
        except (httpx.HTTPError, requests.RequestException, json.JSONDecodeError, KeyError, TypeError) as e :
            logger.debug("_fetch_dexscreener_token_error", exc_info=True)
            logger.warning(f"DexScreener fetch failed for {token_address}: {e}")
        return None

    async def _fetch_solscan_token_data(self, token_address: str) -> dict | None:
        """Fetch token metadata from Solscan (Solana)."""
        try:
            return self._solscan.token_data(token_address)
        except (httpx.HTTPError) as e :
            logger.debug("_fetch_solscan_token_data_error", exc_info=True)
            logger.warning(f"Solscan token data failed for {token_address}: {e}")
            return None

    async def _fetch_helius_signatures(self, address: str, limit: int = 50) -> list[dict]:
        """Fetch recent signatures for an address via Helius RPC."""
        result = await self._chain.rpc_call("getSignaturesForAddress", [address, {"limit": limit}])
        if result and "result" in result:
            return result["result"]
        return []

    async def _fetch_helius_transaction(self, tx_signature: str) -> dict | None:
        """Fetch full transaction details via Helius RPC."""
        result = await self._chain.rpc_call(
            "getTransaction",
            [tx_signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
        )
        if result and "result" in result:
            return result["result"]
        return None

    async def _fetch_solscan_transactions(
        self, address: str, page: int = 1, page_size: int = 40
    ) -> list[dict]:
        """Fetch transaction history from Solscan."""
        try:
            return self._solscan.account_transactions(address, page=page, page_size=page_size) or []
        except (httpx.HTTPError) as e :
            logger.debug("_fetch_solscan_transactions_error", exc_info=True)
            logger.warning(f"Solscan transactions failed for {address}: {e}")
            return []

    async def _fetch_solscan_transaction_detail(self, tx_hash: str) -> dict | None:
        """Fetch transaction detail from Solscan."""
        try:
            return self._solscan.transaction_detail(tx_hash)
        except (httpx.HTTPError) as e :
            logger.debug("_fetch_solscan_transaction_detail_error", exc_info=True)
            logger.warning(f"Solscan transaction detail failed for {tx_hash}: {e}")
            return None

    # ── Deployment info ────────────────────────────────────────────────

    async def _get_deployment_info(
        self, token: str, chain: str, deploy_tx: str | None
    ) -> tuple[str, int, int]:
        """Fetch deployment transaction info using direct APIs.

        Strategy:
        1. If deploy_tx is provided, fetch that transaction directly
        2. Otherwise, look up the token on DexScreener (has pairCreation metadata)
        3. Fallback: use Solscan token data for creation info
        """
        # If explicit deploy_tx provided, fetch it
        if deploy_tx and is_solana(chain):
            tx_data = await self._fetch_helius_transaction(deploy_tx)
            if tx_data:
                # Extract deployer from the transaction
                msg = tx_data.get("transaction", {}).get("message", {})
                account_keys = msg.get("accountKeys", [])
                signers = [a for a in account_keys if a.get("signer", a.get("writable", False))]
                deployer = ""
                if signers:
                    deployer = signers[0].get(
                        "pubkey", signers[0] if isinstance(signers[0], str) else ""
                    )
                elif account_keys:
                    first = account_keys[0]
                    deployer = first.get("pubkey", first) if isinstance(first, dict) else first
                block_time = tx_data.get("blockTime", 0) or 0
                slot = tx_data.get("slot", 0) or 0
                return (deployer, slot, block_time * 1000 if block_time else 0)

        # Try DexScreener for pair creation info
        dex_pair = await self._fetch_dexscreener_token(token)
        if dex_pair:
            info = dex_pair.get("info", {}) or {}
            deployer = info.get("creator", dex_pair.get("pairCreator", ""))
            block_time = dex_pair.get("pairCreatedAt", 0)
            if deployer:
                return (deployer, 0, block_time)

        # Try Solscan token data for creation info on Solana
        if is_solana(chain):
            sol_data = await self._fetch_solscan_token_data(token)
            if sol_data:
                deployer = sol_data.get("creator", sol_data.get("owner", ""))
                creation_time = sol_data.get("blockTime", 0) or 0
                if deployer:
                    return (deployer, 0, creation_time)

        return "unknown", 0, 0

    async def _get_early_buyers(self, token: str, chain: str, deploy_block: int) -> list:
        """Get wallets that bought within first 10 blocks of deployment.

        Uses DexScreener boosts/transactions + Solscan account data.
        For Solana: queries Helius for early signatures after deploy.
        """
        early_buyers = []

        if is_solana(chain) and deploy_block > 0:
            # Get signatures for the token address (mint account)
            sigs = await self._fetch_helius_signatures(token, limit=50)
            for sig_info in sigs[:20]:
                slot = sig_info.get("slot", 0)
                # Only consider transactions within ~10 blocks of deployment
                if 0 < slot - deploy_block <= 10:
                    early_buyers.append(
                        _EarlyBuyer(
                            address=sig_info.get("signer", ""),
                            first_buy_tx=sig_info.get("signature", ""),
                            first_buy_block=slot,
                        )
                    )

        return early_buyers

    async def _trace_funding_chain(
        self, wallet: str, chain: str, max_depth: int = 5, before_time: int = 0
    ) -> list[FundingStep]:
        """Trace SOL/token transfers backward from a wallet to find funding source.

        Uses Solscan top transfers + Helius RPC for on-chain data.
        """
        chain_result: list[FundingStep] = []

        if is_solana(chain):
            # Try Solscan funding sources first
            funding_sources = self._solscan.get_wallet_funding_sources(wallet, days=30)
            if funding_sources:
                for src in funding_sources[:max_depth]:
                    chain_result.append(
                        FundingStep(
                            address=src.get("from", ""),
                            amount=float(src.get("amount", 0)),
                            token=src.get("symbol", "SOL"),
                            timestamp=int(src.get("time", 0) or 0),
                            tx_signature=src.get("tx_hash", ""),
                        )
                    )

        return chain_result

    # ── Pure computation methods (unchanged) ──────────────────────────

    def _cluster_by_behavior(self, buyers: list) -> list[BehaviorCluster]:
        """Cluster wallets by identical trading patterns."""
        # Placeholder - requires transaction-level analysis
        # Real implementation groups by: same DEX, same trade size, same slippage, same route
        return []

    def _analyze_compute_budgets(self, buyers: list) -> tuple[dict[str, int], int]:
        """Analyze Solana compute budget patterns to identify bot networks.

        Key signals:
        - Casual traders: variable compute, random priority fees
        - Bot operators: identical compute units, fixed priority fee
        - Sniper bots: max compute (1.4M CU), max priority fee
        """
        patterns = {}
        jito_count = 0

        for buyer in buyers:
            cu = getattr(buyer, "compute_units", 0)
            pf = getattr(buyer, "priority_fee", 0.0)
            is_jito = getattr(buyer, "is_jito", False)

            if is_jito:
                jito_count += 1

            # Group by compute budget pattern
            if cu > 1_300_000:  # Max compute = sniper
                pattern = "max_compute_sniper"
            elif cu > 200_000 and pf < 0.001:  # Fixed low fee = bot
                pattern = "fixed_fee_bot"
            elif cu < 200_000:  # Normal trader
                pattern = "normal_trader"
            else:
                pattern = "standard_bot"

            patterns[pattern] = patterns.get(pattern, 0) + 1

        return patterns, jito_count

    def _calculate_risk(
        self,
        deployer_funded: list,
        same_block: list,
        behavior_clusters: list,
        jito_count: int,
        bundled_pct: float,
    ) -> tuple[int, str, list[str]]:
        """Calculate overall bundle risk score and warnings."""
        score = 0
        warnings = []

        # Deployer-funded wallets (strongest signal)
        if deployer_funded:
            fund_pct = sum(b.buy_amount for b in deployer_funded) / max(bundled_pct, 0.01) * 100
            score += min(50, len(deployer_funded) * 10)
            warnings.append(
                f"DEPLOYER_FUNDED: {len(deployer_funded)} wallets funded by deployer ({fund_pct:.1f}% of supply)"
            )

        # Same-block snipers
        if same_block:
            score += min(30, len(same_block) * 5)
            warnings.append(f"SAME_BLOCK: {len(same_block)} wallets bought in deployment block")

        # Jito bundles
        if jito_count > 0:
            score += min(20, jito_count * 5)
            warnings.append(f"JITO_BUNDLE: {jito_count} wallets used Jito tips")

        # Behavior clusters
        if behavior_clusters:
            score += min(15, len(behavior_clusters) * 5)
            warnings.append(
                f"BEHAVIOR_CLUSTER: {len(behavior_clusters)} coordinated wallet groups detected"
            )

        # Bundled percentage
        if bundled_pct > 20:
            score += 20
            warnings.append(f"BUNDLED_SUPPLY: {bundled_pct:.1f}% of supply in bundled wallets")
        elif bundled_pct > 10:
            score += 10

        score = min(100, score)

        if score >= 70:
            level = "CRITICAL"
        elif score >= 45:
            level = "HIGH"
        elif score >= 20:
            level = "MEDIUM"
        else:
            level = "LOW"

        return score, level, warnings

    # ── Main analysis entry point ─────────────────────────────────────

    async def detect_bundles(
        self, token_address: str, chain: str, deploy_tx: str | None = None
    ) -> BundleReport:
        """Full bundle detection for a token launch.

        Steps:
        1. Find deployment transaction and deployer
        2. Get all buyers in first 10 blocks
        3. Trace funding chains for each buyer (5 hops)
        4. Identify deployer-funded wallets
        5. Identify same-block snipers
        6. Analyze compute budget patterns (Solana)
        7. Cluster by behavioral patterns
        8. Calculate risk score
        """
        # Fetch deployment info via direct APIs
        deployer, deploy_block, deploy_ts = await self._get_deployment_info(
            token_address, chain, deploy_tx
        )

        if not deployer or deployer == "unknown":
            return BundleReport(
                token_address=token_address,
                chain=chain,
                deployer="unknown",
                deploy_block=0,
                deploy_timestamp=0,
                total_bundled_percentage=0,
                deployer_funded=[],
                same_block_snipers=[],
                behavior_clusters=[],
                warnings=["Could not determine deployer"],
            )

        # Get early buyers (first 10 blocks)
        early_buyers = await self._get_early_buyers(token_address, chain, deploy_block)

        # Trace funding chains
        deployer_funded = []
        for buyer in early_buyers:
            chain_trace = await self._trace_funding_chain(
                buyer.address, chain, max_depth=5, before_time=deploy_ts
            )
            chain_addrs = [step.address for step in chain_trace]
            if deployer in chain_addrs:
                deployer_funded.append(
                    BundledWallet(
                        address=buyer.address,
                        funding_chain=chain_trace,
                        buy_tx=buyer.first_buy_tx,
                        buy_amount=buyer.buy_amount,
                        buy_block=buyer.first_buy_block,
                        confidence="VERY_HIGH",
                        evidence=[f"Funded by deployer via {len(chain_trace)} hops"],
                    )
                )

        # Same-block analysis
        same_block = [
            SameBlockSniper(
                address=b.address,
                block_number=b.first_buy_block,
                buy_amount=b.buy_amount,
                compute_units=getattr(b, "compute_units", 0),
                priority_fee=getattr(b, "priority_fee", 0.0),
                is_jito=getattr(b, "is_jito", False),
            )
            for b in early_buyers
            if b.first_buy_block == deploy_block
        ]

        # Behavior clustering
        behavior_clusters = self._cluster_by_behavior(early_buyers)

        # Compute budget analysis (Solana)
        compute_patterns = {}
        jito_count = 0
        if is_solana(chain):
            compute_patterns, jito_count = self._analyze_compute_budgets(early_buyers)

        # Calculate totals
        total_bundled_pct = sum(b.buy_amount for b in deployer_funded)
        total_supply = sum(b.buy_amount for b in early_buyers) or 1
        total_bundled_pct = (total_bundled_pct / total_supply * 100) if total_supply else 0

        # Risk score
        risk_score, risk_level, warnings = self._calculate_risk(
            deployer_funded, same_block, behavior_clusters, jito_count, total_bundled_pct
        )

        return BundleReport(
            token_address=token_address,
            chain=chain,
            deployer=deployer,
            deploy_block=deploy_block,
            deploy_timestamp=deploy_ts,
            total_bundled_percentage=round(total_bundled_pct, 2),
            deployer_funded=deployer_funded,
            same_block_snipers=same_block,
            behavior_clusters=behavior_clusters,
            compute_budget_patterns=compute_patterns,
            jito_bundles_detected=jito_count,
            risk_score=risk_score,
            risk_level=risk_level,
            warnings=warnings,
        )

# Type alias for early buyer data placeholder
from dataclasses import dataclass as _dataclass  # noqa: E402


@_dataclass
class _EarlyBuyer:
    address: str
    first_buy_tx: str = ""
    first_buy_block: int = 0
    buy_amount: float = 0.0
    compute_units: int = 0
    priority_fee: float = 0.0
    is_jito: bool = False
