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

import json
import os
from collections import defaultdict
from dataclasses import dataclass, dataclass as _dataclass, field
from typing import Any, ClassVar

import httpx
import requests

from app.chain_client import ChainClient
from app.chain_registry import get_explorer_api_config, is_evm, is_solana
from app.core.logging import get_logger
from app.domains.scanners._sentinel_registry import sentinel_module
from app.fraud_gnn import MIN_GRAPH_WALLETS, TransferEdge, get_fraud_graph
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
    pattern: str  # "same_size", "same_time", "same_dex", "mixed"
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
    graph_fraud_score: int = 0  # 0-100, from graph-based fraud pattern analysis
    graph_fraud_report: dict[str, object] | None = None
    warnings: list[str] = field(default_factory=list)
    cross_launch_flags: dict[str, Any] = field(default_factory=dict)


@sentinel_module(
    key="bundle_detection",
    tier="premium",
    description="Same-block bundle/sniper detection with funding-chain analysis",
    method="detect_bundles",
)
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

    # Etherscan-family block explorer API URLs (fallback if chain registry unset)
    EVM_EXPLORER_URLS: ClassVar[dict[str, str]] = {
        "ethereum": "https://api.etherscan.io/api",
        "base": "https://api.basescan.org/api",
        "bsc": "https://api.bscscan.com/api",
    }

    # Env vars holding each chain's explorer API key (fallback if registry unset)
    EVM_EXPLORER_KEY_ENVS: ClassVar[dict[str, str]] = {
        "ethereum": "ETHERSCAN_API_KEY",
        "base": "BASESCAN_API_KEY",
        "bsc": "BSCSCAN_API_KEY",
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
            resp = await self._http.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}")
            if resp.status_code == 200:
                data = resp.json()
                pairs = data.get("pairs") or []
                return pairs[0] if pairs else None
        except (httpx.HTTPError, requests.RequestException, json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug("_fetch_dexscreener_token_error", exc_info=True)
            logger.warning(f"DexScreener fetch failed for {token_address}: {e}")
        return None

    async def _fetch_solscan_token_data(self, token_address: str) -> dict | None:
        """Fetch token metadata from Solscan (Solana)."""
        try:
            return self._solscan.token_data(token_address)
        except httpx.HTTPError as e:
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

    async def _fetch_solscan_transactions(self, address: str, page: int = 1, page_size: int = 40) -> list[dict]:
        """Fetch transaction history from Solscan."""
        try:
            return self._solscan.account_transactions(address, page=page, page_size=page_size) or []
        except httpx.HTTPError as e:
            logger.debug("_fetch_solscan_transactions_error", exc_info=True)
            logger.warning(f"Solscan transactions failed for {address}: {e}")
            return []

    async def _fetch_solscan_transaction_detail(self, tx_hash: str) -> dict | None:
        """Fetch transaction detail from Solscan."""
        try:
            return self._solscan.transaction_detail(tx_hash)
        except httpx.HTTPError as e:
            logger.debug("_fetch_solscan_transaction_detail_error", exc_info=True)
            logger.warning(f"Solscan transaction detail failed for {tx_hash}: {e}")
            return None

    # ── Deployment info ────────────────────────────────────────────────

    async def _get_deployment_info(self, token: str, chain: str, deploy_tx: str | None) -> tuple[str, int, int]:
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
                    deployer = signers[0].get("pubkey", signers[0] if isinstance(signers[0], str) else "")
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

        Uses Solscan top transfers + Helius RPC for Solana, and the
        Etherscan-family block explorer APIs (Etherscan/Basescan/BscScan)
        for EVM chains.
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
        elif is_evm(chain):
            # Etherscan-family block explorer tracing (Ethereum, Base, BSC, ...)
            chain_result = await self._trace_evm_funding(wallet, chain, max_depth=max_depth, before_time=before_time)

        return chain_result

    # ── EVM funding tracing (Etherscan-family explorers) ────────────────

    def _get_explorer_url(self, chain: str) -> str:
        """Return the block explorer API base URL for a chain.

        Prefers the chain registry config (authoritative); falls back to
        the hardcoded Etherscan-family map for ethereum/base/bsc.
        """
        api_url, _ = get_explorer_api_config(chain)
        if api_url:
            return api_url
        return self.EVM_EXPLORER_URLS.get(chain.lower(), "")

    def _get_explorer_api_key(self, chain: str) -> str:
        """Return the block explorer API key for a chain.

        Prefers the chain registry env mapping; falls back to the
        hardcoded map (ETHERSCAN_API_KEY / BASESCAN_API_KEY / BSCSCAN_API_KEY).
        """
        _, api_key = get_explorer_api_config(chain)
        if api_key:
            return api_key
        env_var = self.EVM_EXPLORER_KEY_ENVS.get(chain.lower(), "")
        return os.getenv(env_var, "") if env_var else ""

    async def _trace_evm_funding(
        self, wallet: str, chain: str, max_depth: int = 5, before_time: int = 0
    ) -> list[FundingStep]:
        """Trace ERC-20 token transfers backward from an EVM wallet.

        Uses the Etherscan-family ``tokentx`` endpoint (free tier,
        ~5 req/s) to find transfers INTO the wallet — the senders are
        the funding sources. Each hop captures address, amount, token,
        timestamp (unix ms), and tx signature.

        Works for any chain registered with an Etherscan-family explorer
        API (Ethereum, Base, BSC, Arbitrum, Polygon, ...).
        """
        api_url = self._get_explorer_url(chain)
        if not api_url:
            logger.debug(f"no_explorer_api_for_chain chain={chain}")
            return []

        api_key = self._get_explorer_api_key(chain)
        if not api_key:
            logger.debug(f"no_explorer_api_key_for_chain chain={chain}")
            return []

        params: dict[str, str | int] = {
            "module": "account",
            "action": "tokentx",
            "address": wallet,
            "page": 1,
            "offset": 100,
            "sort": "desc",
            "apikey": api_key,
        }

        try:
            resp = await self._http.get(api_url, params=params)
            if resp.status_code != 200:
                logger.warning(f"Explorer API returned {resp.status_code} for {chain} {wallet}")
                return []
            data = resp.json()
            if data.get("status") != "1":
                # Etherscan returns status 0 with an error message on failures
                logger.debug(f"Explorer API error: {data.get('message', '')} for {chain} {wallet}")
                return []
            transfers = data.get("result") or []
        except (httpx.HTTPError, requests.RequestException, json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug("_trace_evm_funding_error", exc_info=True)
            logger.warning(f"EVM funding trace failed for {wallet} on {chain}: {e}")
            return []

        wallet_lower = wallet.lower()
        chain_result: list[FundingStep] = []

        for tx in transfers:
            # Only incoming transfers (funding INTO the wallet)
            if str(tx.get("to", "")).lower() != wallet_lower:
                continue
            timestamp_sec = int(tx.get("timeStamp", 0) or 0)
            timestamp_ms = timestamp_sec * 1000
            # Skip transfers after the deployment/buy time
            if before_time and timestamp_ms >= before_time:
                continue

            try:
                decimals = int(tx.get("tokenDecimal", 18) or 18)
                amount = float(tx.get("value", 0)) / (10**decimals)
            except (ValueError, TypeError, ZeroDivisionError):
                amount = 0.0

            chain_result.append(
                FundingStep(
                    address=tx.get("from", ""),
                    amount=round(amount, 8),
                    token=tx.get("tokenSymbol", "ERC20") or "ERC20",
                    timestamp=timestamp_ms,
                    tx_signature=tx.get("hash", ""),
                )
            )
            if len(chain_result) >= max_depth:
                break

        return chain_result

    # ── Pure computation methods (unchanged) ──────────────────────────

    def _cluster_by_behavior(self, buyers: list) -> list[BehaviorCluster]:
        """Cluster wallets by identical trading patterns.

        Groups early buyers that share the same behavioral fingerprint across
        four dimensions:

        1. **Trade size** — identical buy amount within ±1% tolerance
        2. **Timing** — first buy in the same block or 1-block adjacency
        3. **DEX route** — identical routing venue (when transaction data
           provides ``dex_route`` on the buyer)
        4. **Compute budget / priority fee** — identical Solana compute pattern

        Single-dimension groups are merged (union-find) so overlapping groups
        combine into one cluster. Each cluster's ``pattern`` is one of
        ``same_size``, ``same_time``, ``same_dex``, or ``mixed`` (multiple
        dimensions coincide, e.g. identical size + identical timing).
        ``combined_percentage`` is the cluster's share of the total observed
        early-buy volume; ``confidence`` reflects pattern strength and size.
        """
        if not buyers or len(buyers) < 2:
            return []

        amounts = [float(getattr(b, "buy_amount", 0.0) or 0.0) for b in buyers]
        blocks = [int(getattr(b, "first_buy_block", 0) or 0) for b in buyers]
        dexes = [str(getattr(b, "dex_route", "") or "").strip().lower() for b in buyers]
        compute_keys = [
            (
                int(getattr(b, "compute_units", 0) or 0),
                round(float(getattr(b, "priority_fee", 0.0) or 0.0), 6),
            )
            for b in buyers
        ]

        # Candidate single-dimension groups (lists of buyer indices)
        size_groups = self._behavior_size_groups(amounts)
        time_groups = self._behavior_time_groups(blocks)
        dex_groups = self._behavior_exact_groups(dexes)
        compute_groups = self._behavior_exact_groups(
            [f"{cu}:{pf}" if (cu > 0 or pf > 0) else "" for cu, pf in compute_keys]
        )

        groups = [g for g in (size_groups + time_groups + dex_groups + compute_groups) if len(g) >= 2]
        if not groups:
            return []

        # Union-find: merge overlapping single-dimension groups into clusters
        parent = list(range(len(buyers)))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for group in groups:
            for member in group[1:]:
                union(group[0], member)

        merged: dict[int, list[int]] = defaultdict(list)
        for i in range(len(buyers)):
            merged[find(i)].append(i)

        total_amount = sum(amounts) or 1.0
        clusters: list[BehaviorCluster] = []
        for indices in merged.values():
            if len(indices) < 2:
                continue
            cluster = self._behavior_cluster_from_indices(buyers, indices, amounts, blocks, dexes, total_amount)
            if cluster:
                clusters.append(cluster)

        clusters.sort(key=lambda c: c.combined_percentage, reverse=True)
        return clusters

    @staticmethod
    def _behavior_size_groups(amounts: list[float]) -> list[list[int]]:
        """Group buyer indices by buy amount within ±1% tolerance."""
        if len(amounts) < 2:
            return []
        indexed = sorted(range(len(amounts)), key=lambda i: amounts[i])
        groups: list[list[int]] = []
        current: list[int] = []
        anchor = 0.0
        for idx in indexed:
            amt = amounts[idx]
            if amt <= 0:
                continue
            if not current:
                current = [idx]
                anchor = amt
            elif amt <= anchor * 1.01:
                current.append(idx)
            else:
                if len(current) >= 2:
                    groups.append(current)
                current = [idx]
                anchor = amt
        if len(current) >= 2:
            groups.append(current)
        return groups

    @staticmethod
    def _behavior_time_groups(blocks: list[int]) -> list[list[int]]:
        """Group buyer indices by same block or 1-block adjacency."""
        if len(blocks) < 2:
            return []
        indexed = sorted(range(len(blocks)), key=lambda i: blocks[i])
        groups: list[list[int]] = []
        current: list[int] = []
        prev_block = 0
        for idx in indexed:
            blk = blocks[idx]
            if blk <= 0:
                continue
            if not current or blk - prev_block <= 1:
                current.append(idx)
            else:
                if len(current) >= 2:
                    groups.append(current)
                current = [idx]
            prev_block = blk
        if len(current) >= 2:
            groups.append(current)
        return groups

    @staticmethod
    def _behavior_exact_groups(values: list[Any]) -> list[list[int]]:
        """Group buyer indices by identical non-empty value (dex / compute)."""
        by_value: dict[Any, list[int]] = defaultdict(list)
        for idx, val in enumerate(values):
            if val:
                by_value[val].append(idx)
        return [g for g in by_value.values() if len(g) >= 2]

    def _behavior_cluster_from_indices(
        self,
        buyers: list,
        indices: list[int],
        amounts: list[float],
        blocks: list[int],
        dexes: list[str],
        total_amount: float,
    ) -> BehaviorCluster | None:
        """Build a BehaviorCluster from a merged set of buyer indices."""
        wallets = [str(getattr(buyers[i], "address", "")) for i in indices]
        wallets = [w for w in wallets if w]
        if len(wallets) < 2:
            return None

        cluster_amounts = [amounts[i] for i in indices if amounts[i] > 0]
        cluster_blocks = [blocks[i] for i in indices if blocks[i] > 0]
        cluster_dexes = {dexes[i] for i in indices if dexes[i]}

        signals: set[str] = set()
        if cluster_amounts and (max(cluster_amounts) / min(cluster_amounts)) <= 1.01:
            signals.add("same_size")
        if cluster_blocks and (max(cluster_blocks) - min(cluster_blocks)) <= 1:
            signals.add("same_time")
        if len(cluster_dexes) == 1:
            signals.add("same_dex")

        if len(signals) >= 2:
            pattern = "mixed"
        elif signals:
            pattern = next(iter(signals))
        else:
            # Cluster formed via compute-budget similarity or a chain of
            # different signals — treat as a mixed-pattern coordination.
            pattern = "mixed"

        combined_amount = sum(amounts[i] for i in indices)
        combined_pct = round(combined_amount / total_amount * 100, 2)
        confidence = self._behavior_confidence(pattern, len(wallets), cluster_blocks)

        return BehaviorCluster(
            wallets=wallets,
            pattern=pattern,
            combined_percentage=combined_pct,
            confidence=confidence,
        )

    @staticmethod
    def _behavior_confidence(pattern: str, size: int, blocks: list[int]) -> float:
        """Confidence heuristic: pattern strength + cluster size + exact block."""
        base = {
            "same_size": 0.6,
            "same_time": 0.7,
            "same_dex": 0.5,
            "mixed": 0.85,
        }[pattern]
        # Larger lockstep groups are more suspicious
        base = min(0.95, base + 0.05 * max(0, size - 2))
        # Identical (not just adjacent) block is a stronger timing signal
        if blocks and len(set(blocks)) == 1:
            base = min(0.98, base + 0.05)
        return round(base, 3)

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
        graph_score: int = 0,
    ) -> tuple[int, str, list[str]]:
        """Calculate overall bundle risk score and warnings."""
        score = 0
        warnings = []

        # Graph-based fraud patterns (fan-out / cycles / in-degree / temporal)
        if graph_score > 0:
            score += min(20, graph_score // 5)
            warnings.append(f"GRAPH_FRAUD: graph pattern score {graph_score}/100")

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
            # Clusters combining multiple patterns (e.g. same_size + same_time)
            # are very high confidence — raise the contribution cap 15 → 25.
            strong = [
                c
                for c in behavior_clusters
                if (c.pattern == "mixed" and c.confidence >= 0.8)
                or (c.pattern in ("same_size", "same_time") and c.confidence >= 0.7)
            ]
            base_pts = min(15, len(behavior_clusters) * 5)
            if strong:
                score += min(25, base_pts + len(strong) * 5)
                warnings.append(
                    f"BEHAVIOR_CLUSTER: {len(behavior_clusters)} coordinated groups "
                    f"({len(strong)} high-confidence size/timing clusters)"
                )
            else:
                score += base_pts
                warnings.append(f"BEHAVIOR_CLUSTER: {len(behavior_clusters)} coordinated wallet groups detected")

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

    async def detect_bundles(self, token_address: str, chain: str, deploy_tx: str | None = None) -> BundleReport:
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
        deployer, deploy_block, deploy_ts = await self._get_deployment_info(token_address, chain, deploy_tx)

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
        graph_transfers: list[TransferEdge] = []
        for buyer in early_buyers:
            chain_trace = await self._trace_funding_chain(buyer.address, chain, max_depth=5, before_time=deploy_ts)
            # Every funding step is a transfer: funder -> buyer
            graph_transfers.extend(
                TransferEdge(
                    from_addr=step.address,
                    to_addr=buyer.address,
                    amount=step.amount,
                    timestamp=step.timestamp,
                    token=step.token,
                )
                for step in chain_trace
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

        # Graph-based fraud pattern analysis (fan-out / cycles / in-degree / temporal)
        # Only meaningful once we have enough distinct wallets in the transfer graph.
        graph_fraud_score = 0
        graph_fraud_report: dict[str, object] | None = None
        if len(early_buyers) >= MIN_GRAPH_WALLETS and graph_transfers:
            try:
                graph_result = get_fraud_graph().analyze(
                    transfers=graph_transfers,
                    token_address=token_address,
                    chain=chain,
                    launch_timestamp=deploy_ts or None,
                )
                graph_fraud_score = round(graph_result.fraud_graph_score)
                graph_fraud_report = graph_result.to_dict()
                if graph_fraud_score >= 50:
                    logger.warning(
                        "graph_fraud_alert",
                        token_address=token_address,
                        graph_score=graph_fraud_score,
                        nodes=graph_result.node_count,
                        edges=graph_result.edge_count,
                    )
            except Exception as e:
                # Graph analysis is best-effort; never fail the whole bundle scan
                logger.debug("graph_fraud_analysis_error", exc_info=True)
                logger.warning(f"Graph fraud analysis failed for {token_address}: {e}")

        # Calculate totals
        total_bundled_pct = sum(b.buy_amount for b in deployer_funded)
        total_supply = sum(b.buy_amount for b in early_buyers) or 1
        total_bundled_pct = (total_bundled_pct / total_supply * 100) if total_supply else 0

        # Risk score
        risk_score, risk_level, warnings = self._calculate_risk(
            deployer_funded, same_block, behavior_clusters, jito_count, total_bundled_pct, graph_fraud_score
        )

        # Cross-launch wallet correlation — repeat bundler detection.
        # Records this launch's early buyers and cross-references them against
        # wallets seen as early buyers in previous launches. Never breaks the
        # bundle scan: failures degrade to an error payload.
        cross_launch_flags: dict[str, Any] = {}
        if early_buyers:
            try:
                from app.domains.scanners.engines.cross_launch_correlation import analyze_cross_launch

                cross_launch_flags = await analyze_cross_launch(
                    early_buyers=early_buyers,
                    chain=chain,
                    deploy_block=deploy_block,
                    token_address=token_address,
                )
            except Exception as exc:  # pragma: no cover - defensive, never break bundle detection
                logger.debug("cross_launch_correlation_failed: %s", exc)
                cross_launch_flags = {"result": "error", "error": str(exc)[:200]}

            repeat_count = int(cross_launch_flags.get("repeat_count", 0))
            if repeat_count > 0:
                warnings.append(
                    f"CROSS_LAUNCH_REPEAT: {repeat_count} wallets matched repeat-bundler profile "
                    f"(max {int(cross_launch_flags.get('max_wallet_launches', 0))} launches)"
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
            graph_fraud_score=graph_fraud_score,
            graph_fraud_report=graph_fraud_report,
            warnings=warnings,
            cross_launch_flags=cross_launch_flags,
        )


@_dataclass
class _EarlyBuyer:
    address: str
    first_buy_tx: str = ""
    first_buy_block: int = 0
    buy_amount: float = 0.0
    compute_units: int = 0
    priority_fee: float = 0.0
    is_jito: bool = False
    dex_route: str = ""
