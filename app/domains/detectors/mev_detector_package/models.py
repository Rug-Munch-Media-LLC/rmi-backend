"""
MEV Detector — Data Models
Enums, dataclasses for MEV attack detection.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class MEVAttackType(Enum):
    SANDWICH = "sandwich"
    FRONTRUN = "frontrun"
    BACKRUN = "backrun"
    LIQUIDATION_MEV = "liquidation_mev"
    ARBITRAGE_MEV = "arbitrage_mev"
    JIT_LIQUIDITY = "jit_liquidity"
    UNKNOWN = "unknown"

class MEVSeverity(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

class PoolDexType(Enum):
    UNISWAP_V2 = "uniswap_v2"
    UNISWAP_V3 = "uniswap_v3"
    SUSHISWAP = "sushiswap"
    PANCAKESWAP = "pancakeswap"
    CURVE = "curve"
    BALANCER = "balancer"
    AERODROME = "aerodrome"
    CAMELOT = "camelot"
    UNKNOWN = "unknown"

@dataclass
class PoolInfo:
    address: str
    chain: str
    dex_type: PoolDexType
    token0: str = ""
    token1: str = ""
    token0_symbol: str = ""
    token1_symbol: str = ""
    liquidity_usd: float = 0.0
    volume_24h_usd: float = 0.0
    fee_tier: int = 3000

    def __post_init__(self) -> None:
        _supported_chains = {"ethereum","bsc","arbitrum","base","optimism","polygon","avalanche","fantom"}
        if not re.match(r"^0x[a-fA-F0-9]{40}$", self.address):
            raise ValueError(f"Invalid EVM address: {self.address}")
        if self.chain not in _supported_chains:
            raise ValueError(f"Unsupported chain: {self.chain}")
        if self.liquidity_usd < 0:
            raise ValueError(f"Negative liquidity: {self.liquidity_usd}")
        if self.volume_24h_usd < 0:
            raise ValueError(f"Negative volume: {self.volume_24h_usd}")

@dataclass
class MEVTransaction:
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
        if self.value_eth < 0:
            raise ValueError(f"Negative ETH value: {self.value_eth}")
        if self.gas_price_gwei < 0:
            raise ValueError(f"Negative gas price: {self.gas_price_gwei}")
        if self.gas_used < 0:
            raise ValueError(f"Negative gas used: {self.gas_used}")

@dataclass
class SandwichAttack:
    victim_address: str
    token_in: str
    token_out: str
    chain: str
    pool_address: str
    block_number: int
    frontrun_tx: MEVTransaction
    victim_tx: MEVTransaction
    backrun_tx: MEVTransaction
    bot_address: str
    estimated_extracted_usd: float = 0.0
    victim_loss_usd: float = 0.0
    slippage_impact_pct: float = 0.0
    confidence: float = 0.0
    severity: MEVSeverity = MEVSeverity.INFO
    bot_name: str = "unknown"
    pool_name: str = ""
    detected_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def summary(self) -> str:
        direction = f"{self.token_in}→{self.token_out}"
        return (
            f"[{self.severity.value.upper()}] SANDWICH on {self.pool_name or self.pool_address[:10]} "
            f"({self.chain}) | Victim: {self.victim_address[:10]} | "
            f"Bot: {self.bot_name[:12] or self.bot_address[:10]} | "
            f"Extracted: ${self.estimated_extracted_usd:.2f} | "
            f"{direction} | Block #{self.block_number}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "sandwich", "chain": self.chain, "block": self.block_number,
            "victim": self.victim_address, "bot": self.bot_address, "bot_name": self.bot_name,
            "pool": self.pool_address, "pool_name": self.pool_name,
            "tokens": f"{self.token_in}→{self.token_out}",
            "extracted_usd": round(self.estimated_extracted_usd, 2),
            "victim_loss_usd": round(self.victim_loss_usd, 2),
            "slippage_impact_pct": round(self.slippage_impact_pct, 4),
            "severity": self.severity.value, "confidence": round(self.confidence, 2),
            "detected_at": self.detected_at,
            "frontrun_tx": self.frontrun_tx.tx_hash, "victim_tx": self.victim_tx.tx_hash,
            "backrun_tx": self.backrun_tx.tx_hash,
        }

@dataclass
class MEVBotProfile:
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
        types = ", ".join(t.value for t in self.attack_types[:3])
        return f"{self.name or self.address[:12]} ({self.chain}) | ${self.total_extracted_usd:.0f} extracted | {self.attacks_detected} attacks | {types}"

@dataclass
class MEVScanReport:
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
        return sorted(self.sandwiches, key=lambda s: s.estimated_extracted_usd, reverse=True)[:limit]

    def top_bots(self, limit: int = 10) -> list[MEVBotProfile]:
        return sorted(self.bots_detected, key=lambda b: b.total_extracted_usd, reverse=True)[:limit]

    def summary(self) -> str:
        total_attacks = len(self.sandwiches) + len(self.frontruns) + len(self.backruns)
        total_extracted = sum(s.estimated_extracted_usd for s in self.sandwiches)
        return (
            f"MEV Scan Report\n  Chains: {', '.join(self.chains_scanned) or 'none'}\n"
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
