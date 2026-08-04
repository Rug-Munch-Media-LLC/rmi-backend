"""
SENTINEL - Holder Distribution & HHI Analysis
=============================================
Calculates Herfindahl-Hirschman Index for token concentration,
detects fake diversification through funding/creation/behavior clustering.

HHI = Σ(s_i²) x 10,000 where s_i = market share of holder i  # noqa: RUF002
  < 1,500  = Low risk (well-distributed)
  1,500-3,000 = Moderate risk
  3,000-5,000 = High risk (whale dominance)
  > 5,000  = Critical (near-monopoly)

Uses direct API calls: DexScreener (pairs/market), Birdeye (overview),
Solscan (holders), Helius (account metadata).
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, ClassVar

import httpx
import requests

from app.birdeye_client import BirdeyeClient
from app.chain_registry import is_solana
from app.core.logging import get_logger
from app.free_solscan_client import FreeSolscanClient

from app.domains.scanners._sentinel_registry import sentinel_module

logger = get_logger("holder_analyzer")


@dataclass
class HolderInfo:
    address: str
    balance: float
    percentage: float  # of total supply
    first_tx_timestamp: int | None = None  # unix ms
    funding_source: str | None = None
    is_exchange: bool = False
    is_contract: bool = False
    label: str | None = None


@dataclass
class ClusterGroup:
    wallets: list[str]
    combined_percentage: float
    confidence: float  # 0.0-1.0
    cluster_type: str  # "funding", "creation", "behavior", "exchange"
    evidence: list[str] = field(default_factory=list)


@dataclass
class HHIReport:
    hhi: float
    risk_level: str  # "low", "moderate", "high", "critical"
    top_10_percentage: float
    top_20_percentage: float
    top_50_percentage: float
    total_holders: int
    clusters: list[ClusterGroup] = field(default_factory=list)
    fake_diversification_score: float = 0.0  # 0-100, higher = more fake
    exchange_funded_percentage: float = 0.0
    new_wallets_percentage: float = 0.0  # wallets < 7 days old
    warnings: list[str] = field(default_factory=list)


@sentinel_module(key="holder_analysis", tier="free", description="Holder distribution & HHI concentration analysis")
class HolderAnalyzer:
    """Analyzes holder distribution, calculates HHI, detects fake diversification.

    Fetches data directly from DexScreener, Birdeye, Solscan, and Helius.
    """

    # Known dead/burn addresses
    DEAD_ADDRESSES: ClassVar[dict[str, list[str]]] = {
        "solana": [
            "11111111111111111111111111111111",  # System program
            "1nc1nerator11111111111111111111111111111111",  # Incinerator
        ],
        "ethereum": [
            "0x000000000000000000000000000000000000dEaD",
            "0x0000000000000000000000000000000000000000",
        ],
    }

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=15.0)
        self._birdeye = BirdeyeClient()
        self._solscan = FreeSolscanClient()
        self._helius_key = os.getenv("HELIUS_API_KEY", "")

    async def close(self) -> None:
        await self._http.aclose()
        await self._birdeye.close()

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

    async def _fetch_birdeye_overview(self, token_address: str) -> dict | None:
        """Fetch token overview from Birdeye."""
        try:
            result = await self._birdeye.get_token_overview(token_address)
            if isinstance(result, dict) and "data" in result:
                return result["data"]
            return result
        except (httpx.HTTPError, KeyError, TypeError) as e:
            logger.debug("_fetch_birdeye_overview_error", exc_info=True)
            logger.warning(f"Birdeye overview failed for {token_address}: {e}")
            return None

    async def _fetch_solscan_holders(self, token_address: str, top_n: int = 50) -> list[dict]:
        """Fetch top holders from Solscan (Solana only)."""
        try:
            holders = self._solscan.get_holder_wallets(token_address, top_n=top_n)
            return holders or []
        except httpx.HTTPError as e:
            logger.debug("_fetch_solscan_holders_error", exc_info=True)
            logger.warning(f"Solscan holders fetch failed for {token_address}: {e}")
            return []

    async def _fetch_helius_token_metadata(self, mint: str) -> dict | None:
        """Fetch token metadata from Helius DAS API."""
        if not self._helius_key:
            return None
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(
                    f"https://mainnet.helius-rpc.com/?api-key={self._helius_key}",
                    json={
                        "jsonrpc": "2.0",
                        "id": "1",
                        "method": "getAsset",
                        "params": {"id": mint},
                    },
                )
                if r.status_code == 200:
                    data = r.json()
                    result = data.get("result") if isinstance(data, dict) else None
                    if result and isinstance(result, dict):
                        return result
        except (
            httpx.HTTPError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ConnectionError,
            OSError,
            TimeoutError,
        ) as e:
            logger.debug("_fetch_helius_token_metadata_error", exc_info=True)
            logger.warning(f"Helius token metadata failed for {mint}: {e}")
        return None

    async def _fetch_holder_data(self, token_address: str, chain: str) -> list[HolderInfo]:
        """Aggregate holder data from multiple sources into HolderInfo list.

        Strategy by chain:
          - solana: Solscan holders + Birdeye overview for % + DexScreener for supply context
          - evm: DexScreener pairs (top holder info limited for EVM; mostly Birdeye)
        """
        holders: list[HolderInfo] = []

        if is_solana(chain):
            # Primary: Solscan holders (has address, balance, percentage)
            raw_holders = await self._fetch_solscan_holders(token_address, top_n=50)
            if raw_holders:
                for h in raw_holders:
                    # Solscan returns dict with 'address', 'amount', 'pct' etc.
                    addr = h.get("address", h.get("owner", ""))
                    balance = float(h.get("amount", h.get("balance", 0)))
                    pct = float(h.get("pct", h.get("percentage", 0)))
                    # Check if exchange
                    from app.free_solscan_client import is_known_exchange

                    exchange_label = is_known_exchange(addr)
                    holders.append(
                        HolderInfo(
                            address=addr,
                            balance=balance,
                            percentage=pct,
                            first_tx_timestamp=h.get("block_time"),
                            funding_source=h.get("funding_source"),
                            is_exchange=bool(exchange_label),
                            is_contract=h.get("is_contract", False),
                            label=exchange_label or h.get("label"),
                        )
                    )

            # Supplement with Birdeye overview for total holder count + holder distribution
            birdeye_data = await self._fetch_birdeye_overview(token_address)
            if birdeye_data and isinstance(birdeye_data, dict) and not holders:
                # If we got no Solscan holders, try Birdeye's distribution
                dist = birdeye_data.get("holderDistribution") or []
                total_supply = float(birdeye_data.get("totalSupply", 1) or 1)
                for entry in dist:
                    addr = entry.get("address", entry.get("owner", ""))
                    balance = float(entry.get("amount", entry.get("balance", 0)))
                    pct = (balance / total_supply * 100) if total_supply else 0
                    holders.append(
                        HolderInfo(
                            address=addr,
                            balance=balance,
                            percentage=pct,
                        )
                    )

        else:
            # EVM chains: DexScreener for pair info, Birdeye for overview
            await self._fetch_dexscreener_token(token_address)
            birdeye_data = await self._fetch_birdeye_overview(token_address)

            # DexScreener doesn't provide holders directly, but Birdeye may
            if birdeye_data and isinstance(birdeye_data, dict):
                dist = birdeye_data.get("holderDistribution") or []
                total_supply = float(birdeye_data.get("totalSupply", 1) or 1)
                for entry in dist:
                    addr = entry.get("address", entry.get("owner", ""))
                    balance = float(entry.get("amount", entry.get("balance", 0)))
                    pct = (balance / total_supply * 100) if total_supply else 0
                    holders.append(
                        HolderInfo(
                            address=addr,
                            balance=balance,
                            percentage=pct,
                        )
                    )

        return holders

    # ── Pure computation methods (unchanged) ──────────────────────────────

    def calculate_hhi(self, holders: list[HolderInfo]) -> float:
        """Calculate Herfindahl-Hirschman Index for token concentration.

        HHI ranges from 0 (perfect distribution) to 10,000 (single holder).
        """
        if not holders:
            return 10000.0  # No holders = maximum concentration

        total_supply = sum(h.balance for h in holders)
        if total_supply == 0:
            return 10000.0

        hhi = sum((h.balance / total_supply) ** 2 for h in holders) * 10000
        return round(hhi, 2)

    def classify_hhi(self, hhi: float) -> str:
        """Classify HHI score into risk level."""
        if hhi < 1500:
            return "low"
        elif hhi < 3000:
            return "moderate"
        elif hhi < 5000:
            return "high"
        else:
            return "critical"

    def detect_fake_diversification(self, holders: list[HolderInfo]) -> list[ClusterGroup]:
        """Detect wallets that appear separate but are controlled by the same entity.

        Uses three clustering dimensions:
        1. Common funding source (wallets funded from same address)
        2. Temporal correlation (wallets active in same time windows)
        3. Behavioral similarity (same DEX, same trade sizes, same patterns)
        """
        clusters = []

        # DIMENSION 1: Common funding source
        funding_clusters = self._cluster_by_funding(holders)
        clusters.extend(funding_clusters)

        # DIMENSION 2: Temporal correlation
        temporal_clusters = self._cluster_by_creation_time(holders)
        clusters.extend(temporal_clusters)

        # DIMENSION 3: Behavioral similarity (same trade patterns)
        behavior_clusters = self._cluster_by_behavior(holders)
        clusters.extend(behavior_clusters)

        # Merge overlapping clusters
        merged = self._merge_overlapping_clusters(clusters)

        return merged

    def _cluster_by_funding(self, holders: list[HolderInfo]) -> list[ClusterGroup]:
        """Group wallets that were funded from the same source address."""
        source_groups = defaultdict(list)

        for holder in holders:
            if holder.funding_source:
                source_groups[holder.funding_source].append(holder)

        clusters = []
        for source, group in source_groups.items():
            if len(group) >= 2:  # 2+ wallets from same source
                combined_pct = sum(h.percentage for h in group)
                # Higher confidence if wallets are new and funded close together
                confidence = min(0.95, 0.5 + 0.1 * len(group))
                clusters.append(
                    ClusterGroup(
                        wallets=[h.address for h in group],
                        combined_percentage=round(combined_pct, 2),
                        confidence=confidence,
                        cluster_type="funding",
                        evidence=[f"All {len(group)} wallets funded from {source[:8]}...{source[-6:]}"],
                    )
                )

        return clusters

    def _cluster_by_creation_time(self, holders: list[HolderInfo]) -> list[ClusterGroup]:
        """Group wallets that were created within the same time window.
        Suspicious if multiple wallets were created within hours/minutes of each other.
        """
        if not all(h.first_tx_timestamp for h in holders):
            return []

        # Sort by creation time
        sorted_holders = sorted(holders, key=lambda h: h.first_tx_timestamp or 0)

        clusters = []
        current_cluster = [sorted_holders[0]]

        for i in range(1, len(sorted_holders)):
            prev_time = current_cluster[-1].first_tx_timestamp
            curr_time = sorted_holders[i].first_tx_timestamp

            # If created within 1 hour of each other
            if curr_time and prev_time and abs(curr_time - prev_time) < 3600_000:
                current_cluster.append(sorted_holders[i])
            else:
                if len(current_cluster) >= 3:
                    combined_pct = sum(h.percentage for h in current_cluster)
                    clusters.append(
                        ClusterGroup(
                            wallets=[h.address for h in current_cluster],
                            combined_percentage=round(combined_pct, 2),
                            confidence=0.7,
                            cluster_type="creation",
                            evidence=[f"{len(current_cluster)} wallets created within 1 hour of each other"],
                        )
                    )
                current_cluster = [sorted_holders[i]]

        # Don't forget the last cluster
        if len(current_cluster) >= 3:
            combined_pct = sum(h.percentage for h in current_cluster)
            clusters.append(
                ClusterGroup(
                    wallets=[h.address for h in current_cluster],
                    combined_percentage=round(combined_pct, 2),
                    confidence=0.7,
                    cluster_type="creation",
                    evidence=[f"{len(current_cluster)} wallets created within 1 hour"],
                )
            )

        return clusters

    def _cluster_by_behavior(self, holders: list[HolderInfo]) -> list[ClusterGroup]:
        """Group wallets with identical trading patterns.
        Placeholder for on-chain behavior analysis - requires transaction data.
        """
        # This requires fetching transaction history for each holder
        # which is expensive. For now, return empty clusters.
        # Full implementation will use trade frequency, DEX routing, and size patterns.
        return []

    def _merge_overlapping_clusters(self, clusters: list[ClusterGroup]) -> list[ClusterGroup]:
        """Merge clusters that share wallets using union-find."""
        if not clusters:
            return []

        # Build adjacency: wallets that appear in multiple clusters
        wallet_to_clusters = defaultdict(set)
        for i, cluster in enumerate(clusters):
            for wallet in cluster.wallets:
                wallet_to_clusters[wallet].add(i)

        # Union-Find to merge connected clusters
        parent = list(range(len(clusters)))

        def find(x) -> Any:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x, y) -> None:
            px, py = find(x), find(y)
            if px != py:
                parent[px] = py

        for _wallet, cluster_indices in wallet_to_clusters.items():
            if len(cluster_indices) > 1:
                indices = list(cluster_indices)
                for i in range(1, len(indices)):
                    union(indices[0], indices[i])

        # Group clusters by root
        merged = defaultdict(list)
        for i in range(len(clusters)):
            merged[find(i)].append(clusters[i])

        result = []
        for _root, group in merged.items():
            all_wallets = list({w for c in group for w in c.wallets})
            combined_pct = (
                sum(c.combined_percentage for c in group)
                / len(group)
                * len(all_wallets)
                / sum(len(c.wallets) for c in group)
            )
            max_confidence = max(c.confidence for c in group)
            all_evidence = [e for c in group for e in c.evidence]
            cluster_types = "/".join({c.cluster_type for c in group})

            result.append(
                ClusterGroup(
                    wallets=all_wallets,
                    combined_percentage=round(min(combined_pct, 100), 2),
                    confidence=min(max_confidence + 0.1 * len(group) - 0.1, 0.99),
                    cluster_type=cluster_types,
                    evidence=all_evidence,
                )
            )

        return sorted(result, key=lambda c: c.combined_percentage, reverse=True)

    # ── Main analysis entry point ─────────────────────────────────────────

    async def analyze(self, token_address: str, chain: str) -> HHIReport:
        """Full holder analysis for a token.

        Steps:
        1. Fetch top holders from DexScreener/Birdeye/Solscan
        2. Calculate HHI
        3. Detect fake diversification
        4. Identify exchange-funded wallets
        5. Calculate new wallet percentage
        6. Generate warnings
        """
        # Fetch holder data from direct APIs
        holders = await self._fetch_holder_data(token_address, chain)

        if not holders:
            return HHIReport(
                hhi=10000.0,
                risk_level="critical",
                top_10_percentage=100.0,
                top_20_percentage=100.0,
                top_50_percentage=100.0,
                total_holders=0,
                warnings=["No holder data available"],
            )

        # Calculate HHI
        hhi = self.calculate_hhi(holders)
        risk_level = self.classify_hhi(hhi)

        # Calculate concentration percentages
        sorted_holders = sorted(holders, key=lambda h: h.percentage, reverse=True)
        top_10_pct = sum(h.percentage for h in sorted_holders[:10])
        top_20_pct = sum(h.percentage for h in sorted_holders[:20])
        top_50_pct = sum(h.percentage for h in sorted_holders[:50])

        # Detect fake diversification
        clusters = self.detect_fake_diversification(sorted_holders)

        # Calculate fake diversification score (0-100)
        cluster_pct = sum(c.combined_percentage for c in clusters)
        fake_div_score = min(100, cluster_pct * 2)  # 2x multiplier for hidden concentration

        # Calculate exchange-funded percentage
        exchange_pct = sum(h.percentage for h in holders if h.is_exchange)

        # Calculate new wallets percentage (< 7 days old)
        import time

        now_ms = int(time.time() * 1000)
        week_ms = 7 * 24 * 3600 * 1000
        new_wallets = [h for h in holders if h.first_tx_timestamp and (now_ms - h.first_tx_timestamp) < week_ms]
        new_wallet_pct = sum(h.percentage for h in new_wallets)

        # Generate warnings
        warnings = []
        if hhi > 5000:
            warnings.append(f"CRITICAL: HHI {hhi:.0f} - extreme concentration, likely coordinated")
        elif hhi > 3000:
            warnings.append(f"HIGH: HHI {hhi:.0f} - significant whale control, dump risk")
        elif hhi > 1500:
            warnings.append(f"MODERATE: HHI {hhi:.0f} - some concentration, monitor whale activity")

        if cluster_pct > 10:
            warnings.append(f"FAKE DIVERSIFICATION: {cluster_pct:.1f}% of supply in detected clusters")

        if new_wallet_pct > 30:
            warnings.append(f"NEW WALLETS: {new_wallet_pct:.1f}% held by wallets < 7 days old")

        return HHIReport(
            hhi=hhi,
            risk_level=risk_level,
            top_10_percentage=round(top_10_pct, 2),
            top_20_percentage=round(top_20_pct, 2),
            top_50_percentage=round(top_50_pct, 2),
            total_holders=len(holders),
            clusters=clusters,
            fake_diversification_score=round(fake_div_score, 1),
            exchange_funded_percentage=round(exchange_pct, 2),
            new_wallets_percentage=round(new_wallet_pct, 2),
            warnings=warnings,
        )
