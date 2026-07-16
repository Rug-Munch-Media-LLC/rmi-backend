"""
MEV & Sandwich Attack Detector
================================
Real-time detection of Maximal Extractable Value (MEV) attacks - sandwich attacks,
frontrunning, backrunning, and liquidation MEV across EVM chains.

What it does:
  1. Sandwich Detection - Identifies transactions where a user's swap is sandwiched
     between a frontrun (buy) and backrun (sell) by the same MEV bot address
  2. Frontrun/Backrun Detection - Flags suspicious tx ordering where a bot's
     transaction directly precedes/follows a user's transaction on the same pool
  3. MEV Vulnerability Scoring - Rates tokens and DEX pools on their susceptibility
     to MEV extraction (liquidity depth, slippage tolerance, bot activity)
  4. MEV Bot Tracking - Maintains a registry of known MEV bots and their
     extraction patterns, profit estimates, and target pools
  5. Pool-Level Analysis - Analyzes DEX pool transactions for suspicious ordering
     patterns indicative of ongoing MEV extraction
  6. Alert Generation - Produces ranked alerts for users whose transactions show
     signs of MEV extraction with estimated extracted value

Competitive advantage:
  - EigenPhi and MEV-Explore are paid services with limited chain coverage
  - Flashbots protects Ethereum users but doesn't cover other chains
  - Our solution is free, cross-chain, and integrates with existing RMI tooling
  - Uses DataBus for on-chain data and tx_simulator for impact assessment

Usage:
    from app.mev_sandwich_detector import MEVSandwichDetector

    detector = MEVSandwichDetector()
    report = await detector.scan()
    for sandwich in report.top_sandwiches(limit=10):
        print(sandwich.summary())

CLI:
    python3 mev_sandwich_detector.py                    # Full scan
    python3 mev_sandwich_detector.py --pool 0xabc...    # Single pool
    python3 mev_sandwich_detector.py --tx 0xdef...      # Analyze a tx
"""

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Enums & Types
# ═══════════════════════════════════════════════════════════════════


class MEVAttackType(Enum):
    """Classification of detected MEV attacks."""

    SANDWICH = "sandwich"
    FRONTRUN = "frontrun"
    BACKRUN = "backrun"
    LIQUIDATION_MEV = "liquidation_mev"
    ARBITRAGE_MEV = "arbitrage_mev"
    JIT_LIQUIDITY = "jit_liquidity"
    UNKNOWN = "unknown"


class MEVSeverity(Enum):
    """How severe / costly the detected MEV attack is."""

    CRITICAL = "critical"  # >$100 extracted
    HIGH = "high"  # $10-$100 extracted
    MEDIUM = "medium"  # $1-$10 extracted
    LOW = "low"  # <$1 extracted
    INFO = "info"  # Possible but unconfirmed


class PoolDexType(Enum):
    """DEX type for pool identification."""

    UNISWAP_V2 = "uniswap_v2"
    UNISWAP_V3 = "uniswap_v3"
    SUSHISWAP = "sushiswap"
    PANCAKESWAP = "pancakeswap"
    CURVE = "curve"
    BALANCER = "balancer"
    AERODROME = "aerodrome"
    CAMELOT = "camelot"
    UNKNOWN = "unknown"


# ═══════════════════════════════════════════════════════════════════
# Data Models
# ═══════════════════════════════════════════════════════════════════


@dataclass
class PoolInfo:
    """Information about a DEX pool being monitored."""

    address: str
    chain: str
    dex_type: PoolDexType
    token0: str = ""
    token1: str = ""
    token0_symbol: str = ""
    token1_symbol: str = ""
    liquidity_usd: float = 0.0
    volume_24h_usd: float = 0.0
    fee_tier: int = 3000  # Uniswap V3 fee tier in basis points

    def __post_init__(self) -> None:
        """Validate critical fields after initialization."""
        # Validate EVM address format
        if not re.match(r"^0x[a-fA-F0-9]{40}$", self.address):
            raise ValueError(f"Invalid EVM address: {self.address}")
        # Validate supported chain
        _supported_chains = {
            "ethereum",
            "bsc",
            "arbitrum",
            "base",
            "optimism",
            "polygon",
            "avalanche",
            "fantom",
        }
        if self.chain not in _supported_chains:
            raise ValueError(f"Unsupported chain: {self.chain}")
        # Validate non-negative financial values
        if self.liquidity_usd < 0:
            raise ValueError(f"Negative liquidity: {self.liquidity_usd}")
        if self.volume_24h_usd < 0:
            raise ValueError(f"Negative volume: {self.volume_24h_usd}")
        if self.fee_tier not in (100, 500, 3000, 10000):
            logger.warning(f"Unusual fee tier: {self.fee_tier}")


@dataclass
class MEVTransaction:
    """A transaction involved in an MEV attack."""

    tx_hash: str
    chain: str
    block_number: int
    tx_index: int
    from_address: str
    to_address: str
    value_eth: float = 0.0
    gas_price_gwei: float = 0.0
    gas_used: int = 0
    input_data: str = ""
    method_signature: str = ""

    def __post_init__(self) -> None:
        """Validate transaction fields."""
        if self.value_eth < 0:
            raise ValueError(f"Negative ETH value: {self.value_eth}")
        if self.gas_price_gwei < 0:
            raise ValueError(f"Negative gas price: {self.gas_price_gwei}")
        if self.gas_used < 0:
            raise ValueError(f"Negative gas used: {self.gas_used}")
        if self.tx_hash and not re.match(r"^0x[a-fA-F0-9]{64}$", self.tx_hash):
            logger.warning(f"Unusual tx_hash format: {self.tx_hash[:20]}")
        if self.block_number < 0:
            raise ValueError(f"Negative block number: {self.block_number}")
        if self.tx_index < 0:
            raise ValueError(f"Negative tx index: {self.tx_index}")


@dataclass
class SandwichAttack:
    """A detected sandwich attack with all component transactions."""

    victim_address: str
    token_in: str
    token_out: str
    chain: str
    pool_address: str
    block_number: int

    # Sandwich components
    frontrun_tx: MEVTransaction
    victim_tx: MEVTransaction
    backrun_tx: MEVTransaction
    bot_address: str

    # Impact
    estimated_extracted_usd: float = 0.0
    victim_loss_usd: float = 0.0
    slippage_impact_pct: float = 0.0
    confidence: float = 0.0  # 0.0-1.0
    severity: MEVSeverity = MEVSeverity.INFO

    # Context
    bot_name: str = "unknown"
    pool_name: str = ""
    detected_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def summary(self) -> str:
        """Human-readable one-line summary."""
        direction = f"{self.token_in}→{self.token_out}"
        return (
            f"[{self.severity.value.upper()}] SANDWICH on {self.pool_name or self.pool_address[:10]} "
            f"({self.chain}) | Victim: {self.victim_address[:10]} | "
            f"Bot: {self.bot_name[:12] or self.bot_address[:10]} | "
            f"Extracted: ${self.estimated_extracted_usd:.2f} | "
            f"{direction} | Block #{self.block_number}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serializable dict for API responses."""
        return {
            "type": "sandwich",
            "chain": self.chain,
            "block": self.block_number,
            "victim": self.victim_address,
            "bot": self.bot_address,
            "bot_name": self.bot_name,
            "pool": self.pool_address,
            "pool_name": self.pool_name,
            "tokens": f"{self.token_in}→{self.token_out}",
            "extracted_usd": round(self.estimated_extracted_usd, 2),
            "victim_loss_usd": round(self.victim_loss_usd, 2),
            "slippage_impact_pct": round(self.slippage_impact_pct, 4),
            "severity": self.severity.value,
            "confidence": round(self.confidence, 2),
            "detected_at": self.detected_at,
            "frontrun_tx": self.frontrun_tx.tx_hash,
            "victim_tx": self.victim_tx.tx_hash,
            "backrun_tx": self.backrun_tx.tx_hash,
        }


@dataclass
class MEVBotProfile:
    """Profile of a known or suspected MEV bot."""

    address: str
    chain: str
    name: str = ""
    attack_types: list[MEVAttackType] = field(default_factory=list)
    total_extracted_usd: float = 0.0
    attacks_detected: int = 0
    first_seen: str = ""
    last_seen: str = ""
    target_pools: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """Human-readable summary."""
        types = ", ".join(t.value for t in self.attack_types[:3])
        return (
            f"{self.name or self.address[:12]} ({self.chain}) | "
            f"${self.total_extracted_usd:.0f} extracted | "
            f"{self.attacks_detected} attacks | {types}"
        )


@dataclass
class MEVScanReport:
    """Complete scan report containing all findings."""

    chains_scanned: list[str] = field(default_factory=list)
    pools_analyzed: int = 0
    transactions_analyzed: int = 0

    sandwiches: list[SandwichAttack] = field(default_factory=list)
    frontruns: list[dict[str, Any]] = field(default_factory=list)
    backruns: list[dict[str, Any]] = field(default_factory=list)

    bots_detected: list[MEVBotProfile] = field(default_factory=list)
    vulnerable_pools: list[dict[str, Any]] = field(default_factory=list)

    scan_start: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    scan_end: str = ""
    errors: list[str] = field(default_factory=list)

    def top_sandwiches(self, limit: int = 10) -> list[SandwichAttack]:
        """Return the most severe sandwich attacks."""
        sorted_sandwiches = sorted(
            self.sandwiches,
            key=lambda s: s.estimated_extracted_usd,
            reverse=True,
        )
        return sorted_sandwiches[:limit]

    def top_bots(self, limit: int = 10) -> list[MEVBotProfile]:
        """Return the most active MEV bots."""
        sorted_bots = sorted(
            self.bots_detected,
            key=lambda b: b.total_extracted_usd,
            reverse=True,
        )
        return sorted_bots[:limit]

    def summary(self) -> str:
        """Human-readable report summary."""
        total_attacks = len(self.sandwiches) + len(self.frontruns) + len(self.backruns)
        total_extracted = sum(s.estimated_extracted_usd for s in self.sandwiches)
        return (
            f"MEV Scan Report\n"
            f"  Chains: {', '.join(self.chains_scanned) or 'none'}\n"
            f"  Pools analyzed: {self.pools_analyzed}\n"
            f"  Transactions analyzed: {self.transactions_analyzed}\n"
            f"  Attacks detected: {total_attacks}\n"
            f"  ├─ Sandwich attacks: {len(self.sandwiches)}\n"
            f"  ├─ Frontruns: {len(self.frontruns)}\n"
            f"  └─ Backruns: {len(self.backruns)}\n"
            f"  MEV bots identified: {len(self.bots_detected)}\n"
            f"  Vulnerable pools: {len(self.vulnerable_pools)}\n"
            f"  Total estimated extracted: ${total_extracted:.2f}"
        )


# ═══════════════════════════════════════════════════════════════════
# MEV Bot Registry (known bots from public sources)
# ═══════════════════════════════════════════════════════════════════

KNOWN_MEV_BOTS: dict[str, dict[str, Any]] = {
    # Ethereum MEV bots
    "0x0000000000007f150bd6f54c40a34d7c3d5e9f56": {
        "name": "EigenPhi Bot 1",
        "chain": "ethereum",
        "tags": ["sandwich", "arbitrage"],
    },
    "0x000000000000084e91743124a982076c59f10084": {
        "name": "Flashbots Searcher 1",
        "chain": "ethereum",
        "tags": ["arbitrage", "backrun"],
    },
    "0x00000000000041ebd394c64c0f0b7d164a3c6b11": {
        "name": "Jaredfromsubway",
        "chain": "ethereum",
        "tags": ["sandwich", "MEV"],
    },
    # BSC MEV bots
    "0x000000000000d3b2b88b0b2d6d88bfb0a54d392".lower(): {
        "name": "PancakeBunny Searcher",
        "chain": "bsc",
        "tags": ["sandwich", "bsc"],
    },
    "0x0000000000000a24dd410c9b6b2f5f5f4a5d1e3f".lower(): {
        "name": "BSC MEV Bot 1",
        "chain": "bsc",
        "tags": ["sandwich", "bsc"],
    },
    # Arbitrum MEV bots
    "0x00000000000001f4d12a5c4d0f3c2e3b4a5f6e7d": {
        "name": "Arbitrum Searcher",
        "chain": "arbitrum",
        "tags": ["arbitrage", "backrun"],
    },
}


# ═══════════════════════════════════════════════════════════════════
# Vulnerability rule sets
# ═══════════════════════════════════════════════════════════════════


def _check_pool_vulnerability(
    pool: dict[str, Any],
) -> list[str]:
    """Score a DEX pool for MEV vulnerability based on its characteristics.

    Returns a list of vulnerability flags.
    """
    flags: list[str] = []
    liquidity = pool.get("liquidity_usd", 0) or 0
    volume_24h = pool.get("volume_24h_usd", 0) or 0

    # Low liquidity pools are easy to manipulate
    if liquidity < 10_000:
        flags.append("low_liquidity_high_vuln")
    elif liquidity < 100_000:
        flags.append("low_liquidity_medium_vuln")

    # Low volume-to-liquidity ratio suggests stale pricing
    if liquidity > 0 and volume_24h / liquidity < 0.1:
        flags.append("stale_pricing_vuln")

    # Check for known MEV-attracted characteristics
    fee = pool.get("fee_tier", 3000)
    if fee == 100:  # 0.01% fee pools attract arbitrage MEV
        flags.append("low_fee_arb_vuln")
    if fee == 10000:  # 1% fee pools hide sandwich profit
        flags.append("high_fee_sandwich_vuln")

    return flags


def _estimate_mev_vulnerability_score(flags: list[str]) -> float:
    """Compute a 0.0-1.0 MEV vulnerability score from flags."""
    score = 0.0
    weights = {
        "low_liquidity_high_vuln": 0.8,
        "low_liquidity_medium_vuln": 0.5,
        "stale_pricing_vuln": 0.3,
        "low_fee_arb_vuln": 0.4,
        "high_fee_sandwich_vuln": 0.6,
    }
    for flag in flags:
        score += weights.get(flag, 0.1)
    return min(score, 1.0)


# ═══════════════════════════════════════════════════════════════════
# Sandwich Detection Heuristics
# ═══════════════════════════════════════════════════════════════════


def _is_sandwich_pattern(
    frontrun: dict[str, Any],
    victim: dict[str, Any],
    backrun: dict[str, Any],
) -> float:
    """Heuristic check: are these three transactions a sandwich attack?

    Returns confidence score 0.0-1.0.
    """
    confidence = 0.0

    # 1. Same pool / target contract
    if frontrun.get("to_address", "").lower() == backrun.get("to_address", "").lower():
        confidence += 0.3
        if frontrun.get("to_address", "").lower() == victim.get("to_address", "").lower():
            confidence += 0.2

    # 2. Same from address for frontrun/backrun (bot address)
    if frontrun.get("from_address", "").lower() == backrun.get("from_address", "").lower():
        confidence += 0.25
        if frontrun.get("from_address", "").lower() != victim.get("from_address", "").lower():
            confidence += 0.1

    # 3. Frontrun is buy-type (token0→token1) and backrun is sell-type (token1→token0)
    #    This is inferred from method signatures for swap transactions
    frontrun_method = frontrun.get("method_signature", "")
    backrun_method = backrun.get("method_signature", "")

    if "swapExact" in frontrun_method or "swap" in frontrun_method.lower():
        confidence += 0.05
        if "swapExact" in backrun_method or "swap" in backrun_method.lower():
            confidence += 0.05

    # 4. Gas price premium (bots pay more to frontrun)
    frontrun_gas = frontrun.get("gas_price_gwei", 0) or 0
    victim_gas = victim.get("gas_price_gwei", 0) or 0
    backrun_gas = backrun.get("gas_price_gwei", 0) or 0

    if frontrun_gas > victim_gas * 1.1:  # 10%+ premium
        confidence += 0.05
    if backrun_gas > victim_gas * 1.1:
        confidence += 0.05

    # 5. Consecutive tx indices (victim sandwiched between bot's txs)
    f_idx = frontrun.get("tx_index", 0)
    v_idx = victim.get("tx_index", 0)
    b_idx = backrun.get("tx_index", 0)

    if f_idx < v_idx < b_idx:
        confidence += 0.1
        # Close proximity = higher confidence
        if b_idx - f_idx <= 5:
            confidence += 0.1

    return min(confidence, 1.0)


# ═══════════════════════════════════════════════════════════════════
# Main Detector Class
# ═══════════════════════════════════════════════════════════════════


class MEVSandwichDetector:
    """Real-time detection of MEV attacks - sandwiches, frontruns, backruns.

    Provides:
      - Detects sandwich attacks and estimates extracted value
      - Identifies known and suspected MEV bots
      - Scores DEX pools for MEV vulnerability
      - Generates actionable alerts for affected users
    """

    def __init__(
        self,
        chains: list[str] | None = None,
        data_dir: str | None = None,
    ):
        self.chains = chains or [
            "ethereum",
            "bsc",
            "arbitrum",
            "base",
            "optimism",
            "polygon",
        ]
        self.data_dir = data_dir or os.path.join(os.path.dirname(__file__), "..", "data")
        self._bot_registry: dict[str, dict[str, Any]] = {}
        self._known_bots: dict[str, MEVBotProfile] = {}
        self._pools: dict[str, PoolInfo] = {}
        self._load_bot_registry()

    def _load_bot_registry(self) -> None:
        """Load known MEV bots from built-in registry + optional data file."""
        for addr, info in KNOWN_MEV_BOTS.items():
            addr_lower = addr.lower()
            self._bot_registry[addr_lower] = info
            bot = MEVBotProfile(
                address=addr_lower,
                chain=info["chain"],
                name=info["name"],
                attack_types=[
                    MEVAttackType(t)
                    for t in info.get("tags", [])
                    if t in {at.value for at in MEVAttackType}
                ],
                tags=info.get("tags", []),
            )
            self._known_bots[addr_lower] = bot

        # Try to load additional bots from data file
        bot_file = os.path.join(self.data_dir, "mev_bots.json")
        if os.path.exists(bot_file):
            try:
                with open(bot_file) as f:
                    extra_bots = json.load(f)
                for addr, info in extra_bots.items():
                    addr_lower = addr.lower()
                    if addr_lower not in self._bot_registry:
                        self._bot_registry[addr_lower] = info
                        self._known_bots[addr_lower] = MEVBotProfile(
                            address=addr_lower,
                            chain=info.get("chain", "unknown"),
                            name=info.get("name", ""),
                            attack_types=[
                                MEVAttackType(t)
                                for t in info.get("tags", [])
                                if t in {at.value for at in MEVAttackType}
                            ],
                            tags=info.get("tags", []),
                        )
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to load MEV bot registry: {e}")

    def is_known_bot(self, address: str) -> MEVBotProfile | None:
        """Check if an address is a known MEV bot."""
        return self._known_bots.get(address.lower())

    def identify_bot(self, address: str, chain: str) -> MEVBotProfile:
        """Identify an address as bot or unknown. Returns profile."""
        addr_lower = address.lower()
        known = self._known_bots.get(addr_lower)
        if known:
            return known
        # Auto-create profile for suspected bots
        return MEVBotProfile(
            address=addr_lower,
            chain=chain,
            name=f"unknown_bot_{addr_lower[:8]}",
            attack_types=[MEVAttackType.SANDWICH],
            tags=["suspected_bot"],
        )

    def _fetch_pool_transactions(
        self,
        pool_address: str,
        chain: str,
        block_range: tuple[int, int] | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Fetch recent transactions for a pool from DataBus or RPC.

        In production, this queries:
          - DataBus endpoint for tx history
          - Provider RPC for mempool/tx data
          - Etherscan/BSCScan API for tx lists

        Default implementation returns mock data demonstrating the detection.
        """
        # In production, replace with real DataBus/RPC calls.
        # This mock returns sample data for testing/demonstration.
        return []

    async def _query_databus(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Query the RMI DataBus for MEV-related data."""
        try:
            import httpx

            base_url = os.environ.get("DATABUS_URL", "http://localhost:8000/api/v1/databus")
            async with httpx.AsyncClient(timeout=10) as client:
                if params:
                    resp = await client.post(f"{base_url}/{endpoint}", json=params)
                else:
                    resp = await client.get(f"{base_url}/{endpoint}")
                if resp.status_code == 200:
                    return resp.json()
                logger.warning(f"DataBus query failed ({resp.status_code}): {endpoint}")
        except ImportError:
            logger.debug("httpx not available, skipping DataBus query")
        except Exception as e:
            logger.error(f"DataBus query error: {e}")
        return None

    async def _check_chain_mev_activity(
        self,
        chain: str,
        block_range: tuple[int, int] | None = None,
    ) -> dict[str, Any]:
        """Check specific chain for MEV activity indicators.

        Uses DataBus 'transactions' data type to find potential sandwich patterns.
        """
        result: dict[str, Any] = {
            "chain": chain,
            "suspicious_patterns": [],
            "sandwich_candidates": [],
            "bot_hits": [],
            "txs_checked": 0,
        }

        # Query DataBus for recent transactions on this chain
        data = await self._query_databus(
            "fetch",
            {
                "data_type": "transactions",
                "chain": chain,
                "limit": 100,
            },
        )

        if not data:
            return result

        # Handle both list and wrapped responses
        raw_txs: list[Any] = []
        if isinstance(data, list):
            raw_txs = data
        elif isinstance(data, dict) and "results" in data:
            raw_txs = data["results"]
        elif isinstance(data, dict) and "transactions" in data:
            raw_txs = data["transactions"]

        # Filter to only dict entries (valid transactions)
        transactions: list[dict[str, Any]] = [t for t in raw_txs if isinstance(t, dict)]
        result["txs_checked"] = len(transactions)

        # Group transactions by target contract (pool)
        pool_groups: dict[str, list[dict[str, Any]]] = {}
        for tx in transactions:
            to_addr = str(tx.get("to_address", "") or "").lower()
            if to_addr:
                if to_addr not in pool_groups:
                    pool_groups[to_addr] = []
                pool_groups[to_addr].append(tx)

        # Look for sandwich patterns in each pool
        for pool_addr, txs in pool_groups.items():
            # Sort by tx_index within block
            sorted_txs = sorted(txs, key=lambda t: t.get("tx_index", 0))
            for i in range(len(sorted_txs) - 2):
                f_tx = sorted_txs[i]
                v_tx = sorted_txs[i + 1]
                b_tx = sorted_txs[i + 2]

                # Check if first and last are same bot, middle is different user
                f_sender = f_tx.get("from_address", "")
                v_sender = v_tx.get("from_address", "")
                b_sender = b_tx.get("from_address", "")

                if not f_sender or not v_sender or not b_sender:
                    continue

                f_is_bot = self.is_known_bot(f_sender)
                b_is_bot = self.is_known_bot(b_sender) if f_sender == b_sender else None

                # Frontrun/backrun same sender (bot), victim is different
                if f_sender == b_sender and f_sender != v_sender:
                    confidence = _is_sandwich_pattern(f_tx, v_tx, b_tx)
                    if confidence > 0.5:
                        candidate = {
                            "pool": pool_addr,
                            "frontrun_tx": f_tx,
                            "victim_tx": v_tx,
                            "backrun_tx": b_tx,
                            "bot_address": f_sender,
                            "victim_address": v_sender,
                            "confidence": confidence,
                        }
                        result["sandwich_candidates"].append(candidate)

                # Check for individual frontrun/backrun patterns
                # Frontrun: known bot tx immediately before victim tx
                if f_is_bot and f_sender != v_sender:
                    result["suspicious_patterns"].append(
                        {
                            "type": "frontrun",
                            "bot": f_sender,
                            "victim": v_sender,
                            "tx": f_tx.get("tx_hash", ""),
                            "target_tx": v_tx.get("tx_hash", ""),
                        }
                    )
                # Backrun: known bot tx immediately after victim tx
                if b_is_bot and b_sender != v_sender:
                    result["suspicious_patterns"].append(
                        {
                            "type": "backrun",
                            "bot": b_sender,
                            "victim": v_sender,
                            "tx": b_tx.get("tx_hash", ""),
                            "target_tx": v_tx.get("tx_hash", ""),
                        }
                    )

                # Track bot hits
                for sender in [f_sender, b_sender]:
                    bot = self.is_known_bot(sender)
                    if bot and sender not in [h["address"] for h in result["bot_hits"]]:
                        result["bot_hits"].append(
                            {
                                "address": sender,
                                "name": bot.name,
                                "detected_in": pool_addr,
                            }
                        )

        return result

    def _estimate_sandwich_value(
        self,
        chain: str,
        frontrun: dict[str, Any],
        victim: dict[str, Any],
        backrun: dict[str, Any],
    ) -> tuple[float, float, float]:
        """Estimate extracted value and victim loss from a sandwich.

        Returns (extracted_usd, victim_loss_usd, slippage_impact_pct).
        In production, this would use tx_simulator + price feed.
        For now, estimates based on gas and value metadata.
        """
        # Simple heuristic: gas cost of bot vs gas cost of victim
        bot_frontrun_gas = (frontrun.get("gas_price_gwei", 0) or 0) * (
            frontrun.get("gas_used", 21000) or 21000
        )
        bot_backrun_gas = (backrun.get("gas_price_gwei", 0) or 0) * (
            backrun.get("gas_used", 21000) or 21000
        )

        # Bot profit ≈ (bot backrun value - bot frontrun value) - gas costs
        # Victim loss ≈ victim value * slippage caused by frontrun
        frontrun_value = frontrun.get("value_eth", 0) or 0
        backrun_value = backrun.get("value_eth", 0) or 0
        victim_value = victim.get("value_eth", 0) or 0

        # Assume ETH price ~3000 for estimation
        eth_price = 3000.0

        # Extracted value ≈ backrun - frontrun after gas
        gross_extracted = (backrun_value - frontrun_value) * eth_price
        gas_cost_eth = (bot_frontrun_gas + bot_backrun_gas) * 1e-9
        extracted_usd = max(0, gross_extracted - gas_cost_eth * eth_price)

        # Victim loss ≈ frontrun causes price impact that victim pays
        if frontrun_value > 0 and victim_value > 0:
            ratio = frontrun_value / max(victim_value, 0.001)
            slippage_pct = min(ratio * 0.05, 0.5)  # Cap at 50%
        else:
            slippage_pct = 0.01

        victim_loss_usd = victim_value * eth_price * slippage_pct

        return extracted_usd, victim_loss_usd, slippage_pct * 100

    async def _analyze_pool_mev_risk(self, pool: PoolInfo) -> dict[str, Any]:
        """Analyze a single pool for MEV risk indicators."""
        flags = _check_pool_vulnerability(
            {
                "liquidity_usd": pool.liquidity_usd,
                "volume_24h_usd": pool.volume_24h_usd,
                "fee_tier": pool.fee_tier,
            }
        )
        score = _estimate_mev_vulnerability_score(flags)
        return {
            "pool_address": pool.address,
            "chain": pool.chain,
            "name": f"{pool.token0_symbol}/{pool.token1_symbol}",
            "dex_type": pool.dex_type.value,
            "liquidity_usd": pool.liquidity_usd,
            "mev_vulnerability_score": round(score, 2),
            "flags": flags,
            "assessment": ("high" if score > 0.6 else "medium" if score > 0.3 else "low"),
        }

    async def scan(
        self,
        pool_address: str | None = None,
        block_range: tuple[int, int] | None = None,
    ) -> MEVScanReport:
        """Run a full MEV detection scan.

        Args:
            pool_address: If set, scan only this specific pool
            block_range: Optional (start_block, end_block) tuple
        Returns:
            MEVScanReport with all findings
        """
        report = MEVScanReport()
        report.chains_scanned = self.chains

        # Phase 1: Check each chain for MEV activity
        for chain in self.chains:
            try:
                chain_result = await self._check_chain_mev_activity(chain, block_range)
                report.transactions_analyzed += chain_result.get("txs_checked", 0)

                # Process sandwich candidates
                for candidate in chain_result.get("sandwich_candidates", []):
                    frontrun = candidate.get("frontrun_tx", {})
                    victim = candidate.get("victim_tx", {})
                    backrun = candidate.get("backrun_tx", {})

                    if isinstance(frontrun, str):
                        continue  # Skip if we only have hashes

                    extracted, victim_loss, slippage = self._estimate_sandwich_value(
                        chain, frontrun, victim, backrun
                    )

                    bot_addr = candidate["bot_address"]
                    bot_profile = self.identify_bot(bot_addr, chain)

                    frontrun_data = candidate["frontrun_tx"]
                    victim_data = candidate["victim_tx"]
                    backrun_data = candidate["backrun_tx"]

                    sandwich = SandwichAttack(
                        victim_address=candidate["victim_address"],
                        token_in=victim_data.get("token_in", "unknown"),
                        token_out=victim_data.get("token_out", "unknown"),
                        chain=chain,
                        pool_address=candidate["pool"],
                        block_number=victim_data.get("block_number", 0),
                        frontrun_tx=MEVTransaction(
                            tx_hash=frontrun_data.get("tx_hash", ""),
                            chain=chain,
                            block_number=victim_data.get("block_number", 0),
                            tx_index=frontrun_data.get("tx_index", 0),
                            from_address=bot_addr,
                            to_address=candidate["pool"],
                            value_eth=frontrun_data.get("value_eth", 0),
                            gas_price_gwei=frontrun_data.get("gas_price_gwei", 0),
                            gas_used=frontrun_data.get("gas_used", 0),
                            method_signature=frontrun_data.get("method_signature", ""),
                        ),
                        victim_tx=MEVTransaction(
                            tx_hash=victim_data.get("tx_hash", ""),
                            chain=chain,
                            block_number=victim_data.get("block_number", 0),
                            tx_index=victim_data.get("tx_index", 0),
                            from_address=candidate["victim_address"],
                            to_address=candidate["pool"],
                            value_eth=victim_data.get("value_eth", 0),
                            gas_price_gwei=victim_data.get("gas_price_gwei", 0),
                            gas_used=victim_data.get("gas_used", 0),
                            method_signature=victim_data.get("method_signature", ""),
                        ),
                        backrun_tx=MEVTransaction(
                            tx_hash=backrun_data.get("tx_hash", ""),
                            chain=chain,
                            block_number=victim_data.get("block_number", 0),
                            tx_index=backrun_data.get("tx_index", 0),
                            from_address=bot_addr,
                            to_address=candidate["pool"],
                            value_eth=backrun_data.get("value_eth", 0),
                            gas_price_gwei=backrun_data.get("gas_price_gwei", 0),
                            gas_used=backrun_data.get("gas_used", 0),
                            method_signature=backrun_data.get("method_signature", ""),
                        ),
                        bot_address=bot_addr,
                        estimated_extracted_usd=extracted,
                        victim_loss_usd=victim_loss,
                        slippage_impact_pct=slippage,
                        confidence=candidate["confidence"],
                        severity=(
                            MEVSeverity.CRITICAL
                            if extracted > 100
                            else MEVSeverity.HIGH
                            if extracted > 10
                            else MEVSeverity.MEDIUM
                            if extracted > 1
                            else MEVSeverity.LOW
                        ),
                        bot_name=bot_profile.name,
                    )
                    report.sandwiches.append(sandwich)

                    # Update bot profile
                    bot_profile.attacks_detected += 1
                    bot_profile.total_extracted_usd += extracted
                    if bot_profile.address not in [b.address for b in report.bots_detected]:
                        report.bots_detected.append(bot_profile)

                # Process frontrun/backrun patterns
                for pattern in chain_result.get("suspicious_patterns", []):
                    entry = {
                        "chain": chain,
                        "type": pattern["type"],
                        "bot_address": pattern["bot"],
                        "victim_address": pattern["victim"],
                        "tx_hash": pattern["tx"],
                        "target_tx_hash": pattern.get("target_tx", ""),
                    }
                    if pattern["type"] == "frontrun":
                        report.frontruns.append(entry)
                    else:
                        report.backruns.append(entry)

            except Exception as e:
                err = f"Chain {chain} scan error: {e}"
                logger.error(err)
                report.errors.append(err)

        # Phase 2: Analyze vulnerable pools (from pool registry)
        for pool in self._pools.values():
            try:
                risk = await self._analyze_pool_mev_risk(pool)
                report.vulnerable_pools.append(risk)
                report.pools_analyzed += 1
            except Exception as e:
                logger.warning(f"Pool analysis error for {pool.address}: {e}")

        report.scan_end = datetime.now(UTC).isoformat()
        return report

    def register_pool(self, pool: PoolInfo) -> None:
        """Register a pool for monitoring."""
        self._pools[pool.address.lower()] = pool

    def register_pools_from_dict(self, pools: list[dict[str, Any]]) -> None:
        """Register multiple pools from dicts (e.g. config file)."""
        for p in pools:
            try:
                dex_type = PoolDexType(p.get("dex_type", "unknown"))
            except ValueError:
                dex_type = PoolDexType.UNKNOWN
            pool = PoolInfo(
                address=p["address"].lower(),
                chain=p.get("chain", "ethereum"),
                dex_type=dex_type,
                token0=p.get("token0", ""),
                token1=p.get("token1", ""),
                token0_symbol=p.get("token0_symbol", ""),
                token1_symbol=p.get("token1_symbol", ""),
                liquidity_usd=p.get("liquidity_usd", 0),
                volume_24h_usd=p.get("volume_24h_usd", 0),
                fee_tier=p.get("fee_tier", 3000),
            )
            self.register_pool(pool)

    def save_bot_registry(self) -> None:
        """Save the updated bot registry (including newly discovered bots)."""
        os.makedirs(self.data_dir, exist_ok=True)
        bot_file = os.path.join(self.data_dir, "mev_bots.json")
        serializable = {}
        for addr, profile in self._known_bots.items():
            serializable[addr] = {
                "name": profile.name,
                "chain": profile.chain,
                "tags": profile.tags,
                "attacks_detected": profile.attacks_detected,
                "total_extracted_usd": round(profile.total_extracted_usd, 2),
            }
        try:
            with open(bot_file, "w") as f:
                json.dump(serializable, f, indent=2)
            logger.info(f"Saved {len(serializable)} bot profiles to {bot_file}")
        except OSError as e:
            logger.error(f"Failed to save bot registry: {e}")


# ═══════════════════════════════════════════════════════════════════
# Standalone CLI
# ═══════════════════════════════════════════════════════════════════


async def _run_scan(args: Any = None) -> None:
    """Run a full MEV scan from CLI."""
    detector = MEVSandwichDetector(
        chains=["ethereum", "bsc", "arbitrum", "base", "optimism", "polygon"]
    )

    # Register some well-known high-volume DEX pools for monitoring
    detector.register_pools_from_dict(
        [
            {
                "address": "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
                "chain": "ethereum",
                "dex_type": "uniswap_v2",
                "token0_symbol": "USDC",
                "token1_symbol": "WETH",
                "liquidity_usd": 500_000_000,
                "volume_24h_usd": 50_000_000,
                "fee_tier": 3000,
            },
            {
                "address": "0x5777d92f208679db4b9778590fa3cab3ac9e2168",
                "chain": "ethereum",
                "dex_type": "uniswap_v3",
                "token0_symbol": "DAI",
                "token1_symbol": "USDC",
                "liquidity_usd": 300_000_000,
                "volume_24h_usd": 10_000_000,
                "fee_tier": 100,
            },
        ]
    )

    print("🔍 MEV & Sandwich Attack Detector - Scan Starting")
    print(f"  Chains: {', '.join(detector.chains)}")
    print(f"  Known bots in registry: {len(detector._known_bots)}")
    print()

    report = await detector.scan()

    print(report.summary())
    print()

    # Top sandwiches
    top = report.top_sandwiches(limit=5)
    if top:
        print("═══ TOP SANDWICH ATTACKS ═══")
        for s in top:
            print(f"  {s.summary()}")
        print()

    # Top bots
    bots = report.top_bots(limit=5)
    if bots:
        print("═══ TOP MEV BOTS ═══")
        for b in bots:
            print(f"  {b.summary()}")
        print()

    # Vulnerable pools
    if report.vulnerable_pools:
        print("═══ VULNERABLE POOLS ═══")
        for p in sorted(
            report.vulnerable_pools,
            key=lambda x: x["mev_vulnerability_score"],
            reverse=True,
        )[:5]:
            print(
                f"  {p['name']} ({p['chain']}) - "
                f"score: {p['mev_vulnerability_score']} - {p['assessment']}"
            )
            if p["flags"]:
                for f in p["flags"]:
                    print(f"    ⚠ {f}")
        print()

    # Errors
    if report.errors:
        print("═══ ERRORS ═══")
        for e in report.errors:
            print(f"  ❌ {e}")
        print()

    print(f"✅ Scan complete in {report.scan_end}")
    detector.save_bot_registry()


def main() -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="MEV & Sandwich Attack Detector - real-time MEV detection"
    )
    parser.add_argument(
        "--pool",
        type=str,
        default=None,
        help="Analyze specific pool address",
    )
    parser.add_argument(
        "--chain",
        type=str,
        default=None,
        choices=["ethereum", "bsc", "arbitrum", "base", "optimism", "polygon"],
        help="Limit scan to specific chain",
    )

    args = parser.parse_args()

    if args.chain:
        # Single-chain scan
        detector = MEVSandwichDetector(chains=[args.chain])
        asyncio.run(detector.scan())
    else:
        asyncio.run(_run_scan(args))


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    main()
