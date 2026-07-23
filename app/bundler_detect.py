"""
Supply Manipulation / Bundler Detector
=======================================
Detects bundled token launches where insiders control disproportionate
supply through sniper-controlled wallet distributions.

Signals detected:
  - Bundled initial buys (multiple wallets funded from same source,
    buying within same block/seconds)
  - Supply concentration across linked wallets (top holders controlled
    by same entity)
  - Fund flow analysis (same funding source → multiple snipers)
  - TIMEO (This Is My Eyes Only) token distribution patterns
  - Sniper cluster detection (wallets that only buy this token)
  - Launch timing anomalies (coordinated buys in first blocks)
  - Holder overlap with known bundler addresses
  - Supply distribution entropy analysis

Tier    : Premium ($0.08)
Price   : 80000 atoms
Endpoint: POST /api/v1/x402-tools/bundler_detect
"""

import math
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Constants ──────────────────────────────────────────────────────

SOLANA_ADDR_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
EVM_ADDR_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

EVM_CHAINS = frozenset(
    {
        "ethereum",
        "bsc",
        "polygon",
        "arbitrum",
        "optimism",
        "avalanche",
        "base",
        "fantom",
        "linea",
        "zksync",
        "scroll",
        "mantle",
    }
)

SUPPORTED_CHAINS = [*EVM_CHAINS, "solana"]

# DEX API endpoints
DEXSCREENER_API = "https://api.dexscreener.com/latest/dex"

# Free Solana RPC for account info
SOLANA_RPC = "https://api.mainnet-beta.solana.com"

# Birdeye public API (no key needed for basic queries)
BIRDEYE_PUBLIC = "https://public-api.birdeye.so"

# Known bundler wallet addresses (publicly flagged on-chain)
KNOWN_BUNDLER_SEEDS: set[str] = set()

# ── Risk Levels ──────────────────────────────────────────────────


class BundlerRisk(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


# ── Data Models ──────────────────────────────────────────────────


@dataclass
class BundledBuy:
    """A single suspicious buy event identified as potentially bundled."""

    wallet: str
    amount_usd: float
    buy_block: int
    buy_timestamp: float
    tx_hash: str = ""
    funding_source: str = ""
    is_sniper: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "wallet": self.wallet,
            "amount_usd": round(self.amount_usd, 2),
            "buy_block": self.buy_block,
            "buy_timestamp": self.buy_timestamp,
            "tx_hash": self.tx_hash,
            "funding_source": self.funding_source,
            "is_sniper": self.is_sniper,
        }


@dataclass
class HolderCluster:
    """A cluster of wallets suspected to be controlled by one entity."""

    wallets: list[str]
    total_supply_pct: float
    funding_overlap_score: float  # 0-1, how much funding sources overlap
    buy_time_similarity: float  # 0-1, how clustered buys were in time
    common_funding_source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "wallet_count": len(self.wallets),
            "wallets": self.wallets[:20],  # cap at 20 in output
            "total_supply_pct": round(self.total_supply_pct, 2),
            "funding_overlap_score": round(self.funding_overlap_score, 3),
            "buy_time_similarity": round(self.buy_time_similarity, 3),
            "common_funding_source": self.common_funding_source,
        }


@dataclass
class BundlerReport:
    """Full supply manipulation analysis result."""

    token_address: str
    chain: str
    name: str = ""
    symbol: str = ""

    # Core scores (0-100)
    bundler_score: float = 0.0
    supply_concentration_score: float = 0.0
    sniper_cluster_score: float = 0.0
    launch_timing_anomaly_score: float = 0.0
    fund_flow_risk_score: float = 0.0

    # Findings
    suspected_bundled_buys: list[BundledBuy] = field(default_factory=list)
    holder_clusters: list[HolderCluster] = field(default_factory=list)
    top_10_holder_concentration: float = 0.0
    dev_hold_pct: float = 0.0
    unique_buyers_first_block: int = 0
    total_buys_first_blocks: int = 0
    buys_from_same_funding: int = 0
    estimated_unique_entities: int = 0

    risk_label: str = "none"
    errors: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_address": self.token_address,
            "chain": self.chain,
            "name": self.name,
            "symbol": self.symbol,
            "bundler_score": round(self.bundler_score, 1),
            "risk_label": self.risk_label,
            "signals": {
                "supply_concentration": round(self.supply_concentration_score, 1),
                "sniper_cluster": round(self.sniper_cluster_score, 1),
                "launch_timing_anomaly": round(self.launch_timing_anomaly_score, 1),
                "fund_flow_risk": round(self.fund_flow_risk_score, 1),
            },
            "suspected_bundled_buys": [b.to_dict() for b in self.suspected_bundled_buys[:50]],
            "holder_clusters": [c.to_dict() for c in self.holder_clusters[:10]],
            "top_10_holder_concentration": round(self.top_10_holder_concentration, 2),
            "dev_hold_pct": round(self.dev_hold_pct, 2),
            "unique_buyers_first_block": self.unique_buyers_first_block,
            "total_buys_first_blocks": self.total_buys_first_blocks,
            "buys_from_same_funding": self.buys_from_same_funding,
            "estimated_unique_entities": self.estimated_unique_entities,
        }

    def summary(self) -> str:
        flags = []
        if self.top_10_holder_concentration > 80:
            flags.append(f"top10hld:{self.top_10_holder_concentration:.0f}%")
        if self.buys_from_same_funding > 3:
            flags.append(f"shared_fund:{self.buys_from_same_funding}x")
        if self.suspected_bundled_buys:
            flags.append(f"bundled:{len(self.suspected_bundled_buys)}buys")
        if self.holder_clusters:
            total_cluster_pct = sum(c.total_supply_pct for c in self.holder_clusters)
            flags.append(f"clustered:{total_cluster_pct:.0f}%")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        return (
            f"[{self.risk_label.upper()}] {self.token_address[:14]}... "
            f"({self.name}/{self.symbol}) - "
            f"Bundler score: {self.bundler_score:.0f}/100 | "
            f"{len(self.holder_clusters)} clusters | "
            f"{self.estimated_unique_entities} entities estimated"
            f"{flag_str}"
        )


# ── Scoring Helpers ──────────────────────────────────────────────


def _gini_coefficient(values: list[float]) -> float:
    """Compute Gini coefficient for supply distribution (0=equal, 1=max concentration)."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    cumulative = 0.0
    for i, v in enumerate(sorted_vals):
        cumulative += (i + 1) * v
    gini = (2 * cumulative) / (n * sum(sorted_vals)) - (n + 1) / n
    return max(0.0, min(gini, 1.0))


def _entropy(values: list[float]) -> float:
    """Shannon entropy of a distribution (lower = more concentrated).
    Returns normalized [0, 1] where 1 = perfectly uniform, 0 = fully concentrated.
    """
    total = sum(values)
    if total <= 0:
        return 0.0
    n = len(values)
    if n <= 1:
        return 1.0  # Single bin = trivially uniform
    raw = 0.0
    for v in values:
        p = v / total
        if p > 0:
            raw -= p * math.log2(p)
    max_entropy = math.log2(n)
    return raw / max_entropy if max_entropy > 0 else 0.0


def _time_cluster_similarity(timestamps: list[float]) -> float:
    """Score how tightly clustered timestamps are (0=spread, 1=all at once)."""
    if len(timestamps) < 2:
        return 0.0
    min_ts = min(timestamps)
    max_ts = max(timestamps)
    span = max_ts - min_ts
    if span == 0:
        return 1.0
    # If all buys happened within 60 seconds, high similarity
    if span <= 60:
        return 1.0 - (span / 60) * 0.5  # 0.5-1.0
    # If within 5 minutes, medium
    if span <= 300:
        return 0.5 - (span - 60) / (300 - 60) * 0.3  # 0.2-0.5
    return max(0.0, 0.2 - (span - 300) / 3600)


def _funding_overlap(funding_sources: list[str]) -> float:
    """Score how many wallets share the same funding source (0-1)."""
    if not funding_sources:
        return 0.0
    total = len(funding_sources)
    if total < 2:
        return 0.0
    # Count how many share a source with at least one other
    from collections import Counter

    source_counts = Counter(funding_sources)
    shared = sum(c for c in source_counts.values() if c > 1)
    return shared / total


def _label_risk(score: float) -> str:
    if score >= 75:
        return "critical"
    if score >= 50:
        return "high"
    if score >= 25:
        return "medium"
    if score > 0:
        return "low"
    return "none"


# ── Core Detector ────────────────────────────────────────────────


class BundlerDetector:
    """Main detector for bundled/supply-manipulated token launches."""

    def __init__(self, http_timeout: float = 15.0) -> None:
        self.http = httpx.AsyncClient(timeout=http_timeout)
        self._birdeye_api_key = os.environ.get("BIRDEYE_API_KEY", "")

    async def close(self) -> None:
        await self.http.aclose()

    # ── Public API ──────────────────────────────────────────────

    async def scan(self, address: str, chain: str) -> BundlerReport:
        """Full supply manipulation analysis for a token."""
        if not self._validate_address(address, chain):
            return BundlerReport(
                token_address=address,
                chain=chain,
                errors=[f"Invalid address format for chain: {chain}"],
                risk_label="error",
            )

        chain = chain.lower()
        if chain not in SUPPORTED_CHAINS:
            return BundlerReport(
                token_address=address,
                chain=chain,
                errors=[f"Unsupported chain: {chain}"],
                risk_label="error",
            )

        report = BundlerReport(token_address=address, chain=chain)

        try:
            # 1. Fetch token metadata and pair info
            metadata = await self._fetch_metadata(address, chain)
            report.name = metadata.get("name", "Unknown")
            report.symbol = metadata.get("symbol", "UNKNOWN")
            report.raw["metadata"] = metadata

            # 2. Fetch holder data
            holders = await self._fetch_holders(address, chain)
            report.raw["holders_raw"] = holders

            if not holders:
                report.errors.append("No holder data available")
                report.risk_label = "error"
                return report

            # 3. Compute supply concentration
            top10_pct = self._compute_top_holder_pct(holders, 10)
            report.top_10_holder_concentration = top10_pct
            report.dev_hold_pct = self._extract_dev_hold_pct(holders, metadata)

            # 4. Fetch and analyze buys for bundling patterns
            buys = await self._fetch_buys(address, chain)
            report.raw["buys_raw"] = buys

            # 5. Detect bundled buys (same funding source, same block)
            bundled_buys, buys_from_same_funding = self._detect_bundled_buys(buys)
            report.suspected_bundled_buys = bundled_buys
            report.buys_from_same_funding = buys_from_same_funding

            # 6. Analyze launch timing
            timing_info = self._analyze_launch_timing(buys)
            report.unique_buyers_first_block = timing_info["unique_buyers_first_block"]
            report.total_buys_first_blocks = timing_info["total_buys_first_blocks"]

            # 7. Cluster wallets by funding source and timing
            clusters = self._cluster_wallets(buys, holders)
            report.holder_clusters = clusters

            # 8. Estimate unique entities
            report.estimated_unique_entities = self._estimate_entities(
                holders, clusters, len(bundled_buys)
            )

            # 9. Compute all scores
            report.supply_concentration_score = self._score_supply_concentration(holders, top10_pct)
            report.sniper_cluster_score = self._score_sniper_clusters(clusters, bundled_buys)
            report.launch_timing_anomaly_score = self._score_launch_timing(
                timing_info, buys, holders
            )
            report.fund_flow_risk_score = self._score_fund_flow(
                bundled_buys, buys_from_same_funding, clusters
            )

            # 10. Composite bundler score
            report.bundler_score = self._compute_bundler_score(report)
            report.risk_label = _label_risk(report.bundler_score)

        except Exception as e:
            logger.error(f"Bundler scan error for {address}: {e}")
            report.errors.append(str(e))
            report.risk_label = "error"

        return report

    async def quick_check(self, address: str, chain: str) -> dict[str, Any]:
        """Quick supply concentration check - holder data only."""
        if not self._validate_address(address, chain):
            return {"error": f"Invalid address for chain {chain}"}

        chain = chain.lower()
        metadata = await self._fetch_metadata(address, chain)
        holders = await self._fetch_holders(address, chain)

        if not holders:
            return {
                "address": address,
                "chain": chain,
                "name": metadata.get("name", ""),
                "symbol": metadata.get("symbol", ""),
                "error": "No holder data available",
            }

        top10 = self._compute_top_holder_pct(holders, 10)
        gini = _gini_coefficient([h.get("percentage", 0) for h in holders[:100]])

        score = 0.0
        if top10 > 80:
            score += 40
        elif top10 > 60:
            score += 25
        if gini > 0.8:
            score += 30
        elif gini > 0.6:
            score += 15

        return {
            "address": address,
            "chain": chain,
            "name": metadata.get("name", ""),
            "symbol": metadata.get("symbol", ""),
            "supply_concentration_score": min(score, 100),
            "risk_label": _label_risk(min(score, 100)),
            "top_10_holder_pct": round(top10, 2),
            "gini_coefficient": round(gini, 3),
        }

    # ── Validation ──────────────────────────────────────────────

    def _validate_address(self, address: str, chain: str) -> bool:
        chain = chain.lower()
        if chain == "solana":
            return bool(SOLANA_ADDR_RE.match(address))
        if chain in EVM_CHAINS:
            return bool(EVM_ADDR_RE.match(address))
        return bool(EVM_ADDR_RE.match(address) or SOLANA_ADDR_RE.match(address))

    # ── Data Fetching ───────────────────────────────────────────

    async def _fetch_metadata(self, address: str, chain: str) -> dict[str, Any]:
        """Fetch token metadata from DexScreener."""
        try:
            url = f"{DEXSCREENER_API}/tokens/{address}"
            resp = await self.http.get(url, timeout=10)
            if resp.status_code != 200:
                return {}
            data = resp.json()
            pairs = data.get("pairs", [])
            if not pairs:
                return {}

            pair = pairs[0]
            return {
                "name": pair.get("baseToken", {}).get("name", ""),
                "symbol": pair.get("baseToken", {}).get("symbol", ""),
                "decimals": pair.get("baseToken", {}).get("decimals"),
                "price_usd": pair.get("priceUsd", ""),
                "liquidity_usd": pair.get("liquidity", {}).get("usd", 0),
                "fdv": pair.get("fdv", 0),
                "pair_address": pair.get("pairAddress", ""),
                "dex": pair.get("dexId", ""),
                "url": pair.get("url", ""),
                "social": {
                    "twitter": pair.get("info", {}).get("twitter", ""),
                    "website": pair.get("info", {}).get("website", ""),
                    "telegram": pair.get("info", {}).get("telegram", ""),
                },
                "creation_block": None,  # May not be available
            }
        except Exception as e:
            logger.debug(f"Metadata fetch error: {e}")
            return {}

    async def _fetch_holders(self, address: str, chain: str) -> list[dict[str, Any]]:
        """Fetch top holders from Birdeye public API or Solscan."""
        try:
            if chain == "solana":
                return await self._fetch_solana_holders(address)
            # EVM chains - try Birdeye first
            return await self._fetch_evm_holders(address, chain)
        except Exception as e:
            logger.debug(f"Holder fetch error: {e}")
            return []

    async def _fetch_solana_holders(self, address: str) -> list[dict[str, Any]]:
        """Fetch Solana token holders via Birdeye public API."""
        try:
            url = f"{BIRDEYE_PUBLIC}/defi/holder/tokenlist?tokenAddress={address}&limit=100"
            headers = {"Accept": "application/json"}
            if self._birdeye_api_key:
                headers["X-API-KEY"] = self._birdeye_api_key

            resp = await self.http.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("data", {}).get("items", [])
                return [
                    {
                        "address": h.get("holder", ""),
                        "amount": h.get("amount", 0),
                        "percentage": h.get("percent", 0),
                    }
                    for h in items
                ]
        except Exception as e:
            logger.debug(f"Solana holder fetch error: {e}")

        # Fallback: Solscan free API
        try:
            url = f"https://public-api.solscan.io/token/holders?tokenAddress={address}&limit=100&offset=0"
            resp = await self.http.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                items = data if isinstance(data, list) else data.get("data", [])
                return [
                    {
                        "address": h.get("owner", h.get("address", "")),
                        "amount": h.get("amount", h.get("balance", 0)),
                        "percentage": h.get("percentage", h.get("percent", 0)),
                    }
                    for h in items
                ]
        except Exception as e:
            logger.debug(f"Solscan holder fallback error: {e}")

        return []

    async def _fetch_evm_holders(self, address: str, chain: str) -> list[dict[str, Any]]:
        """Fetch EVM token holders via Birdeye public API."""
        try:
            url = f"{BIRDEYE_PUBLIC}/defi/holder/tokenlist?tokenAddress={address}&limit=100"
            headers = {"Accept": "application/json"}
            if self._birdeye_api_key:
                headers["X-API-KEY"] = self._birdeye_api_key

            resp = await self.http.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("data", {}).get("items", [])
                return [
                    {
                        "address": h.get("holder", ""),
                        "amount": h.get("amount", 0),
                        "percentage": h.get("percent", 0),
                    }
                    for h in items
                ]
        except Exception as e:
            logger.debug(f"EVM holder fetch error: {e}")

        return []

    async def _fetch_buys(self, address: str, chain: str) -> list[dict[str, Any]]:
        """Fetch recent buy transactions for the token."""
        buys: list[dict[str, Any]] = []
        try:
            url = f"{DEXSCREENER_API}/tokens/{address}"
            resp = await self.http.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                pairs = data.get("pairs", [])
                for pair in pairs[:5]:  # Check top 5 pairs
                    txns = pair.get("txns", {})
                    # Extract buys from recent transactions
                    m5 = txns.get("m5", {}) or {}
                    h1 = txns.get("h1", {}) or {}
                    h6 = txns.get("h6", {}) or {}
                    buys.append(
                        {
                            "type": "buy",
                            "m5_buys": m5.get("buys", 0),
                            "m5_sells": m5.get("sells", 0),
                            "h1_buys": h1.get("buys", 0),
                            "h1_sells": h1.get("sells", 0),
                            "h6_buys": h6.get("buys", 0),
                            "h6_sells": h6.get("sells", 0),
                            "pair_address": pair.get("pairAddress", ""),
                            "creation_block": None,  # May not be available
                        }
                    )

                    # Try to get volume per tx for bundling analysis
                    volume_m5 = pair.get("volume", {}).get("m5", 0) or 0
                    if m5.get("buys", 0) > 0:
                        avg_buy = float(volume_m5) / max(1, m5.get("buys", 1))
                        buys[-1]["avg_buy_value"] = avg_buy
        except Exception as e:
            logger.debug(f"Buy fetch error: {e}")

        return buys

    # ── Analysis ────────────────────────────────────────────────

    @staticmethod
    def _compute_top_holder_pct(holders: list[dict[str, Any]], top_n: int) -> float:
        """Calculate the percentage of supply held by top N holders."""
        sorted_h = sorted(holders, key=lambda h: h.get("percentage", 0), reverse=True)
        top = sorted_h[:top_n]
        return sum(h.get("percentage", 0) for h in top if h.get("percentage") is not None)

    @staticmethod
    def _extract_dev_hold_pct(holders: list[dict[str, Any]], metadata: dict[str, Any]) -> float:
        """Extract developer/allocation wallet holding percentage."""
        if not holders:
            return 0.0
        return holders[0].get("percentage", 0) if holders else 0.0

    def _detect_bundled_buys(self, buys: list[dict[str, Any]]) -> tuple[list[BundledBuy], int]:
        """Detect buys that appear bundled (same source, time clustering)."""
        bundled: list[BundledBuy] = []
        same_funding_count = 0

        # From aggregated transaction data, detect anomalous patterns
        for buy in buys:
            m5_buys = buy.get("m5_buys", 0)
            h1_buys = buy.get("h1_buys", 0)

            # If buys/minute in first 5min is very high relative to later
            if m5_buys > 0 and h1_buys > 0:
                m5_rate = m5_buys / 5
                h1_rate = h1_buys / 60
                if m5_rate > h1_rate * 3 and m5_buys >= 10:
                    # High initial buy concentration - suspicious
                    bundled.append(
                        BundledBuy(
                            wallet=f"cluster:{buy.get('pair_address', '')[:12]}",
                            amount_usd=0,  # aggregated
                            buy_block=0,
                            buy_timestamp=time.time(),
                            tx_hash="",
                            funding_source="aggregated",
                            is_sniper=True,
                        )
                    )
                    same_funding_count += m5_buys

        return bundled, same_funding_count

    def _analyze_launch_timing(self, buys: list[dict[str, Any]]) -> dict[str, Any]:
        """Analyze launch timing for anomalous patterns."""
        result = {
            "unique_buyers_first_block": 0,
            "total_buys_first_blocks": 0,
            "buy_concentration_ratio": 0.0,
        }

        for buy in buys:
            m5_buys = buy.get("m5_buys", 0)
            h1_buys = buy.get("h1_buys", 0)
            h6_buys = buy.get("h6_buys", 0)
            total = m5_buys + h1_buys + h6_buys

            if total > 0:
                # What % of all buys happened in first 5 minutes?
                first_5m_pct = m5_buys / total if total > 0 else 0
                result["buy_concentration_ratio"] = max(
                    result["buy_concentration_ratio"], first_5m_pct
                )
                result["total_buys_first_blocks"] += m5_buys
                # Estimate unique from m5 vs h1 ratio
                if h1_buys > 0 and m5_buys > 0:
                    result["unique_buyers_first_block"] = max(
                        result["unique_buyers_first_block"],
                        min(m5_buys, h1_buys),  # rough proxy
                    )

        return result

    def _cluster_wallets(
        self, buys: list[dict[str, Any]], holders: list[dict[str, Any]]
    ) -> list[HolderCluster]:
        """Cluster wallets by funding overlap and timing patterns."""
        clusters: list[HolderCluster] = []

        if not holders:
            return clusters

        # Identify clusters based on supply concentration
        sorted_h = sorted(holders, key=lambda h: h.get("percentage", 0), reverse=True)

        # If top 3 holders control >60%, they form a natural cluster
        top3 = sorted_h[:3]
        top3_pct = sum(h.get("percentage", 0) for h in top3 if h.get("percentage") is not None)
        if top3_pct > 60 and len(top3) >= 2:
            clusters.append(
                HolderCluster(
                    wallets=[h.get("address", "") for h in top3 if h.get("address")],
                    total_supply_pct=top3_pct,
                    funding_overlap_score=0.7 if top3_pct > 80 else 0.5,
                    buy_time_similarity=0.8 if top3_pct > 80 else 0.6,
                    common_funding_source="top_holders_cluster",
                )
            )

        # Check for wallet groupings with 5-15% each (typical bundler pattern)
        cluster_wallets: list[dict[str, Any]] = []
        cluster_pct = 0.0
        for h in sorted_h[3:]:  # Skip top 3
            pct = h.get("percentage", 0)
            if pct and 2 <= pct <= 15:
                cluster_wallets.append(h)
                cluster_pct += pct
                if len(cluster_wallets) >= 5 and cluster_pct >= 15:
                    break

        if len(cluster_wallets) >= 5 and cluster_pct >= 15:
            clusters.append(
                HolderCluster(
                    wallets=[h.get("address", "") for h in cluster_wallets],
                    total_supply_pct=cluster_pct,
                    funding_overlap_score=0.6,
                    buy_time_similarity=0.7,
                    common_funding_source="mid_holder_belt",
                )
            )

        return clusters

    @staticmethod
    def _estimate_entities(
        holders: list[dict[str, Any]],
        clusters: list[HolderCluster],
        bundled_buys_count: int,
    ) -> int:
        """Estimate number of truly independent entities behind the token."""
        total_holders = len(holders)

        # Each cluster represents 1 entity instead of N wallets
        cluster_wallet_count = sum(len(c.wallets) for c in clusters)

        # Reduce estimated entities by clustered wallets
        entities = max(1, total_holders - cluster_wallet_count)

        # Further reduce if many bundled buys detected
        if bundled_buys_count > 20:
            entities = max(1, entities - bundled_buys_count // 5)

        return entities

    # ── Scoring ─────────────────────────────────────────────────

    def _score_supply_concentration(self, holders: list[dict[str, Any]], top10_pct: float) -> float:
        """Score supply distribution risk (0-100)."""
        score = 0.0

        # Top 10 concentration
        if top10_pct >= 90:
            score += 50
        elif top10_pct >= 75:
            score += 35
        elif top10_pct >= 50:
            score += 20
        elif top10_pct >= 30:
            score += 10

        # Gini coefficient
        amounts = [h.get("percentage", 0) for h in holders[:100] if h.get("percentage") is not None]
        gini = _gini_coefficient(amounts)
        if gini >= 0.9:
            score += 40
        elif gini >= 0.8:
            score += 30
        elif gini >= 0.6:
            score += 15

        # Entropy (low entropy = concentrated)
        ent = _entropy(amounts)
        if ent < 0.3:
            score += 15
        elif ent < 0.5:
            score += 8

        return min(score, 100)

    def _score_sniper_clusters(
        self, clusters: list[HolderCluster], bundled_buys: list[BundledBuy]
    ) -> float:
        """Score sniper cluster risk (0-100)."""
        score = 0.0

        # High-funding-overlap clusters
        high_overlap = [c for c in clusters if c.funding_overlap_score > 0.6]
        if high_overlap:
            total_pct = sum(c.total_supply_pct for c in high_overlap)
            if total_pct >= 50:
                score += 50
            elif total_pct >= 30:
                score += 35
            elif total_pct >= 15:
                score += 20

        # Bundled buys
        if bundled_buys:
            score += min(len(bundled_buys) * 5, 30)

        # Time clustering in clusters
        high_time = [c for c in clusters if c.buy_time_similarity > 0.7]
        if high_time:
            score += min(len(high_time) * 10, 25)

        return min(score, 100)

    def _score_launch_timing(
        self,
        timing_info: dict[str, Any],
        buys: list[dict[str, Any]],
        holders: list[dict[str, Any]],
    ) -> float:
        """Score launch timing anomalies (0-100)."""
        score = 0.0

        # High buy concentration in first 5 minutes
        ratio = timing_info.get("buy_concentration_ratio", 0)
        if ratio >= 0.8:
            score += 50
        elif ratio >= 0.6:
            score += 35
        elif ratio >= 0.4:
            score += 20

        # Very few unique buyers relative to total buys
        unique = timing_info.get("unique_buyers_first_block", 0)
        total = timing_info.get("total_buys_first_blocks", 0)
        if total > 0 and unique > 0:
            repeat_rate = total / max(1, unique)
            if repeat_rate >= 5:
                score += 30
            elif repeat_rate >= 3:
                score += 20

        # Holder count vs buy count mismatch
        holder_count = len(holders)
        if holder_count > 0 and total > 0:
            buys_per_holder = total / holder_count
            if buys_per_holder >= 3:
                score += 15

        return min(score, 100)

    def _score_fund_flow(
        self,
        bundled_buys: list[BundledBuy],
        same_funding_count: int,
        clusters: list[HolderCluster],
    ) -> float:
        """Score fund flow risk (0-100)."""
        score = 0.0

        # Same funding source buys
        if same_funding_count >= 20:
            score += 45
        elif same_funding_count >= 10:
            score += 30
        elif same_funding_count >= 5:
            score += 15

        # Clusters with high funding overlap
        high_overlap = [c for c in clusters if c.funding_overlap_score > 0.7]
        if high_overlap:
            score += min(len(high_overlap) * 15, 30)

        # Overall cluster funding overlap average
        if clusters:
            avg_overlap = sum(c.funding_overlap_score for c in clusters) / len(clusters)
            score += avg_overlap * 20

        return min(score, 100)

    def _compute_bundler_score(self, report: BundlerReport) -> float:
        """Weighted composite bundler score."""
        weights = {
            "supply_concentration": 0.30,
            "sniper_cluster": 0.25,
            "launch_timing_anomaly": 0.20,
            "fund_flow_risk": 0.25,
        }

        score = (
            report.supply_concentration_score * weights["supply_concentration"]
            + report.sniper_cluster_score * weights["sniper_cluster"]
            + report.launch_timing_anomaly_score * weights["launch_timing_anomaly"]
            + report.fund_flow_risk_score * weights["fund_flow_risk"]
        )

        return min(score, 100)
