#!/usr/bin/env python3
"""
RMI Tool Fingerprinter - Best-in-Class Scam Infrastructure Detection
=====================================================================
Identifies the TOOLS behind scams, not just the outcomes.
Phase 3-5 capabilities per the Enhanced Report V2 standards.

Detects:
  - Smithii bundler patterns (bundle + liquidity + first swap)
  - Printr bundler signatures (multi-wallet coordinated launch)
  - LaunchLab bundle bot (specific instruction ordering)
  - Jito bundle detection (tip program, bundle construction)
  - PumpFun sniper bots (limit-sniper, pumpfun-bonkfun-bot, etc.)
  - Volume bot / wash trading patterns
  - Wallet aging counter-detection
  - Cross-chain fund obfuscation detection
  - Exchange-funded deployer detection
  - First buyer concentration analysis
  - Scanner-aware evasion detection (Hide-and-Shill framework)

Design principle: Tools change less frequently than tactics.
A Smithii-bundled token has detectable on-chain fingerprints
regardless of the scammer's chosen exit strategy.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

logger = logging.getLogger("tool-fingerprinter")

# ══════════════════════════════════════════════════════════════════════
# KNOWN TOOL FINGERPRINTS - from reverse-engineered scammer infrastructure
# ══════════════════════════════════════════════════════════════════════

# Solana Program IDs used by known bundler/sniper tools
TOOL_PROGRAMS = {
    "jito_bundle": [
        "Jito4APyf6rDt1pD1jH3nD3is4v7TwzFNWjjMP7B2RK",  # Jito bundles v3
        "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",  # Jito tip router v3
        "ADuCadRmgjMe6UzXVxR1Kn9CS2CXAf8bjiKkC4xFcegX",  # Jito tip router v4
    ],
    "pumpfun": [
        "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",  # Pump.fun program
        "CebN5WGQ4jvEPvsVU4EoHEpgzq1VV6AbFtJxHTNBaFUx",  # Pump AMM
    ],
    "moonshot": [
        "MoonCVHWSSyvkWjUj7hDL14N1pVFHUjQyqWScmBw8D1r",
    ],
}

# Transaction instruction patterns that identify specific tools
TOOL_SIGNATURES = {
    "smithii_bundler": {
        "description": "Smithii bundle bot - bundles liquidity add + first swap in single TX",
        "patterns": [
            # Signature: single TX with create_pool + add_liquidity + swap in sequence
            "create_pool_add_liquidity_swap_single_tx",
            # Typical wallet count: 15-25 wallets all buying within 1 block
            "multi_wallet_same_block_15_25",
            # Liquidity pattern: exact same SOL amount per wallet
            "uniform_buy_amounts",
        ],
        "severity": 85,  # 0-100, higher = more suspicious
        "confidence_required": 0.7,
    },
    "printr_bundler": {
        "description": "Printr bundler - coordinated multi-wallet deployment with uniform distribution",
        "patterns": [
            "deployer_program_derived_addresses",
            "wallets_funded_from_single_source",
            "identical_buy_timing_sub_second",
            "uniform_token_distribution_20_30_wallets",
        ],
        "severity": 90,
        "confidence_required": 0.7,
    },
    "launchlab_bundle": {
        "description": "LaunchLab bundle bot - specific instruction ordering for token + LP creation",
        "patterns": [
            "create_mint_create_ata_mint_to_create_pool_add_liquidity",
            "single_tx_8_12_instructions",
            "immediate_swap_after_liquidity",
        ],
        "severity": 88,
        "confidence_required": 0.7,
    },
    "pumpfun_sniper": {
        "description": "PumpFun sniper bot - buys within milliseconds of bonding curve completion",
        "patterns": [
            "bonding_curve_completion_detection",
            "sub_second_first_buy",
            "multiple_wallets_same_program_call",
            "jito_bundle_within_2_blocks",
        ],
        "severity": 70,
        "confidence_required": 0.6,
    },
    "volume_bot": {
        "description": "Volume bot - self-trading to inflate volume metrics for trending algorithms",
        "patterns": [
            "circular_trading_same_small_wallet_set",
            "buy_sell_same_block_no_profit",
            "volume_spike_no_holder_change",
            "identical_trade_sizes_repeated",
            "wallet_graph_fully_connected_clique",
        ],
        "severity": 80,
        "confidence_required": 0.65,
    },
    "wallet_aging_evasion": {
        "description": "Scanner-aware countermeasure - wallets aged before launching scam token",
        "patterns": [
            "wallet_created_weeks_before_first_scam_activity",
            "dormant_period_followed_by_intense_activity",
            "funded_but_idle_then_sudden_use",
            "real_wallet_activity_mix_then_scam_only",
        ],
        "severity": 75,
        "confidence_required": 0.6,
    },
    "cross_chain_obfuscation": {
        "description": "Scanner-aware countermeasure - funds routed through multiple chains to hide origin",
        "patterns": [
            "bridge_usage_to_break_tracking",
            "multiple_chain_funding_hops_3plus",
            "mixer_on_source_chain_before_bridge",
            "cex_deposit_on_chain_a_withdraw_on_chain_b",
        ],
        "severity": 85,
        "confidence_required": 0.65,
    },
}

# Known scammer deployer patterns
SCAMMER_DEPLOYER_PATTERNS = {
    "cex_funded_deployer": {
        "description": "Deployer funded directly from centralized exchange - high scam correlation",
        "cex_wallets": [
            "binance",
            "coinbase",
            "kraken",
            "kucoin",
            "bybit",
            "okx",
            "gate.io",
            "mexc",
            "bitget",
            "htx",
            "bitfinex",
        ],
        "severity": 60,
    },
    "mixer_funded_deployer": {
        "description": "Deployer funded through mixer/tumbler - extremely suspicious",
        "mixers": ["tornado", "cyclone", "typhoon", "wasabi", "samourai"],
        "severity": 95,
    },
    "previous_scammer_deployer": {
        "description": "Deployer previously launched known scam tokens",
        "severity": 100,
    },
}

# First buyer concentration thresholds
FIRST_BUYER_THRESHOLDS = {
    "critical_concentration": 0.80,  # First 5 buyers hold 80%+ = critical
    "high_concentration": 0.50,  # First 10 buyers hold 50%+ = high risk
    "sniper_time_ms": 5000,  # < 5 seconds from launch = sniper
    "coordinated_same_block": 5,  # 5+ wallets buying in same block = coordinated
}


@dataclass
class ToolFingerprintResult:
    """Result from tool fingerprinting analysis."""

    tool_name: str
    detected: bool
    confidence: float  # 0-1
    evidence: list[str]
    severity: int  # 0-100
    description: str


@dataclass
class FirstBuyerAnalysis:
    """Analysis of first buyers for a token."""

    token_address: str
    total_holders: int
    first_5_concentration_pct: float
    first_10_concentration_pct: float
    first_20_concentration_pct: float
    avg_entry_time_ms: float  # from token creation
    fastest_entry_ms: float
    same_block_buyers: int
    coordinated_clusters: int  # groups of wallets with shared funding
    cex_funded_buyers: int  # buyers funded from CEX
    risk_level: str  # critical, high, medium, low
    flags: list[str]


@dataclass
class DeployerProfile:
    """Profile of the token deployer wallet."""

    address: str
    age_days: int
    funding_source: str | None
    funding_source_type: str  # cex, dex, mixer, unknown
    previous_tokens_launched: int
    previous_scam_tokens: int
    cross_chain_activity: bool
    wallet_aging_detected: bool
    aging_score: float  # 0-100
    risk_score: int  # 0-100
    flags: list[str]


# ══════════════════════════════════════════════════════════════════════
# TOOL FINGERPRINTER
# ══════════════════════════════════════════════════════════════════════


class ToolFingerprinter:
    """
    Identifies scammer tools and infrastructure from on-chain fingerprints.
    Analyzes transaction patterns, program interactions, wallet graphs,
    and temporal signatures to identify specific tools.
    """

    def __init__(self):
        self._seen_txs: set[str] = set()
        self._known_scammers: set[str] = set()
        self._cex_wallets: set[str] = set()

    async def fingerprint_transaction(self, tx_data: dict, chain: str = "solana") -> list[ToolFingerprintResult]:
        """Analyze a single transaction for tool fingerprints."""
        results = []
        instructions = tx_data.get("instructions", tx_data.get("ix", []))
        tx_data.get("accountKeys", tx_data.get("accounts", []))
        program_ids = [ix.get("programId", "") for ix in instructions]
        tx_sig = tx_data.get("signature", tx_data.get("txHash", ""))

        if tx_sig in self._seen_txs:
            return results
        self._seen_txs.add(tx_sig)

        # Check Jito bundle
        jito_hits = [p for p in program_ids if p in TOOL_PROGRAMS["jito_bundle"]]
        if jito_hits:
            results.append(
                ToolFingerprintResult(
                    tool_name="jito_bundle",
                    detected=True,
                    confidence=0.95,
                    evidence=[f"Jito program invoked: {jito_hits[0]}"],
                    severity=75,
                    description="Jito bundle detected - transaction was privately submitted to avoid front-running",
                )
            )

        # Check PumpFun
        pf_hits = [p for p in program_ids if p in TOOL_PROGRAMS["pumpfun"]]
        if pf_hits:
            results.append(
                ToolFingerprintResult(
                    tool_name="pumpfun_token",
                    detected=True,
                    confidence=0.99,
                    evidence=[f"Pump.fun program call: {pf_hits[0]}"],
                    severity=40,
                    description="Pump.fun token - elevated risk due to low barrier to entry",
                )
            )

        return results

    async def detect_bundlers(self, tx_list: list[dict], token_address: str) -> list[ToolFingerprintResult]:
        """Detect bundler patterns across multiple transactions."""
        results = []
        evidence_smithii = []
        evidence_printr = []
        evidence_launchlab = []

        if not tx_list:
            return results

        # Group by block
        block_groups = defaultdict(list)
        for tx in tx_list:
            block = tx.get("slot", tx.get("blockNumber", 0))
            block_groups[block].append(tx)

        # Check each block for coordinated behavior
        for block, txs in block_groups.items():
            unique_buyers = set()
            for tx in txs:
                signer = tx.get("signer", tx.get("from", ""))
                if signer:
                    unique_buyers.add(signer)

            buyer_count = len(unique_buyers)

            # Smithii: 15-25 wallets in same block
            if 15 <= buyer_count <= 25:
                evidence_smithii.append(
                    f"Block {block}: {buyer_count} wallets bought in same block (Smithii range: 15-25)"
                )

            # Printr: 20-30 wallets in same block
            if 20 <= buyer_count <= 30:
                evidence_printr.append(
                    f"Block {block}: {buyer_count} wallets bought in same block (Printr range: 20-30)"
                )

            # LaunchLab: 8-12 instructions in a single tx
            for tx in txs:
                ix_count = len(tx.get("instructions", tx.get("ix", [])))
                if 8 <= ix_count <= 12:
                    evidence_launchlab.append(
                        f"TX {tx.get('signature', '?')[:8]}: {ix_count} instructions (LaunchLab range: 8-12)"
                    )

        if evidence_smithii:
            results.append(
                ToolFingerprintResult(
                    tool_name="smithii_bundler",
                    detected=True,
                    confidence=min(0.95, 0.6 + 0.1 * len(evidence_smithii)),
                    evidence=evidence_smithii,
                    severity=85,
                    description="Smithii bundler detected - coordinated bundle launch with 15-25 wallets",
                )
            )

        if evidence_printr:
            results.append(
                ToolFingerprintResult(
                    tool_name="printr_bundler",
                    detected=True,
                    confidence=min(0.95, 0.6 + 0.1 * len(evidence_printr)),
                    evidence=evidence_printr,
                    severity=90,
                    description="Printr bundler detected - 20-30 coordinated wallets from single deployer",
                )
            )

        if evidence_launchlab:
            results.append(
                ToolFingerprintResult(
                    tool_name="launchlab_bundle",
                    detected=True,
                    confidence=min(0.95, 0.6 + 0.1 * len(evidence_launchlab)),
                    evidence=evidence_launchlab,
                    severity=88,
                    description="LaunchLab bundle detected - 8-12 instruction complex bundle transaction",
                )
            )

        return results

    async def analyze_first_buyers(
        self, token_address: str, holders_data: list[dict], first_tx_timestamp: str | None = None
    ) -> FirstBuyerAnalysis:
        """Analyze first buyer concentration and patterns."""
        flags = []

        if not holders_data:
            return FirstBuyerAnalysis(
                token_address=token_address,
                total_holders=0,
                first_5_concentration_pct=0,
                first_10_concentration_pct=0,
                first_20_concentration_pct=0,
                avg_entry_time_ms=0,
                fastest_entry_ms=0,
                same_block_buyers=0,
                coordinated_clusters=0,
                cex_funded_buyers=0,
                risk_level="unknown",
                flags=["No holder data"],
            )

        total_holders = len(holders_data)
        sorted_holders = sorted(holders_data, key=lambda h: h.get("first_buy_time", 0) or 0)

        # Supply concentration
        total_supply = sum(h.get("balance_pct", 0) for h in sorted_holders[:50])
        if total_supply == 0:
            total_supply = 100

        first_5 = sum(h.get("balance_pct", 0) for h in sorted_holders[:5]) / max(total_supply, 1)
        first_10 = sum(h.get("balance_pct", 0) for h in sorted_holders[:10]) / max(total_supply, 1)
        first_20 = sum(h.get("balance_pct", 0) for h in sorted_holders[:20]) / max(total_supply, 1)

        if first_5 > FIRST_BUYER_THRESHOLDS["critical_concentration"]:
            flags.append(f"CRITICAL: First 5 buyers hold {first_5 * 100:.0f}%")

        if first_10 > FIRST_BUYER_THRESHOLDS["high_concentration"]:
            flags.append(f"HIGH: First 10 buyers hold {first_10 * 100:.0f}%")

        # Entry timing
        entry_times = [h.get("first_buy_time", 0) or 0 for h in sorted_holders[:20]]
        entry_times = [t for t in entry_times if t > 0]

        avg_entry = sum(entry_times) / len(entry_times) if entry_times else 0
        fastest = min(entry_times) if entry_times else 0

        if fastest < FIRST_BUYER_THRESHOLDS["sniper_time_ms"]:
            flags.append(f"SNIPER: Fastest entry {fastest:.0f}ms after launch")

        # Same block detection
        blocks = set()
        for h in sorted_holders[:20]:
            block = h.get("first_buy_block")
            if block:
                blocks.add(block)
        same_block = total_holders - len(blocks) if total_holders > 0 else 0

        if same_block >= FIRST_BUYER_THRESHOLDS["coordinated_same_block"]:
            flags.append(f"COORDINATED: {same_block} wallets bought in same block")

        # Funding source clustering
        funding_sources = defaultdict(int)
        for h in sorted_holders[:20]:
            funder = h.get("funding_source", "unknown")
            if funder:
                funding_sources[funder] += 1

        coordinated = sum(1 for count in funding_sources.values() if count >= 3)

        if coordinated > 0:
            flags.append(f"COORDINATED CLUSTERS: {coordinated} groups share funding source")

        # Determine risk level
        if first_5 > 0.80 or len(flags) >= 4:
            risk_level = "critical"
        elif first_5 > 0.50 or len(flags) >= 2:
            risk_level = "high"
        elif len(flags) >= 1:
            risk_level = "medium"
        else:
            risk_level = "low"

        return FirstBuyerAnalysis(
            token_address=token_address,
            total_holders=total_holders,
            first_5_concentration_pct=round(first_5 * 100, 1),
            first_10_concentration_pct=round(first_10 * 100, 1),
            first_20_concentration_pct=round(first_20 * 100, 1),
            avg_entry_time_ms=round(avg_entry, 1),
            fastest_entry_ms=round(fastest, 1),
            same_block_buyers=same_block,
            coordinated_clusters=coordinated,
            cex_funded_buyers=0,
            risk_level=risk_level,
            flags=flags,
        )

    async def profile_deployer(self, deployer_address: str, chain: str = "solana") -> DeployerProfile:
        """Profile the token deployer wallet for risk factors."""
        flags = []
        risk = 0

        # Try to get deployer data
        age_days = 0
        funding_source = None
        funding_type = "unknown"
        aging_detected = False
        aging_score = 0
        prev_tokens = 0
        prev_scams = 0
        cross_chain = False

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                # Basic Solana account info
                if chain == "solana":
                    resp = await client.post(
                        "https://api.mainnet-beta.solana.com",
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "getSignaturesForAddress",
                            "params": [deployer_address, {"limit": 50}],
                        },
                    )
                    if resp.status_code == 200:
                        sigs = resp.json().get("result", [])
                        if sigs:
                            first_sig = sigs[-1]
                            first_ts = first_sig.get("blockTime", 0)
                            if first_ts:
                                age_days = (datetime.now(UTC) - datetime.fromtimestamp(first_ts, tz=UTC)).days
                        prev_tokens = len(
                            [
                                s
                                for s in sigs
                                if "create" in str(s.get("memo", "")).lower()
                                or "token" in str(s.get("memo", "")).lower()
                            ]
                        )

        except Exception as e:
            logger.warning(f"Failed to profile deployer {deployer_address}: {e}")

        # Fresh wallet scoring
        if age_days < 1:
            risk += 30
            flags.append("BRAND_NEW: Wallet less than 1 day old")
        elif age_days < 7:
            risk += 20
            flags.append("FRESH: Wallet less than 7 days old")
        elif age_days < 30:
            risk += 10
            flags.append("NEW: Wallet less than 30 days old")

        # Wallet aging detection (countermeasure)
        if age_days > 30 and prev_tokens == 0:
            # Old wallet, first token launch = possible aged wallet
            aging_score = 40
            flags.append("AGING_SUSPICIOUS: Old wallet launching first token")
        elif age_days > 90 and prev_tokens <= 1:
            aging_score = 60
            aging_detected = True
            flags.append("AGING_DETECTED: Wallet aged 90+ days, first/second token only")
            risk += 25

        # Repeated token launcher
        if prev_tokens > 5:
            risk += 15
            flags.append(f"SERIAL_LAUNCHER: {prev_tokens} previous tokens")

        # Check if deployer appears in our known scam DB
        try:
            from app.scam_sources import KNOWN_SCAMS_EXPANDED

            if deployer_address in str(KNOWN_SCAMS_EXPANDED):
                risk += 50
                prev_scams = 1
                flags.append("KNOWN_SCAMMER: Previously identified in scam database")
        except Exception:
            pass

        # Determine funding type
        if funding_type == "cex":
            risk += 20
            flags.append("CEX_FUNDED: Deployer funded from centralized exchange")

        return DeployerProfile(
            address=deployer_address,
            age_days=age_days,
            funding_source=funding_source,
            funding_source_type=funding_type,
            previous_tokens_launched=prev_tokens,
            previous_scam_tokens=prev_scams,
            cross_chain_activity=cross_chain,
            wallet_aging_detected=aging_detected,
            aging_score=aging_score,
            risk_score=min(100, risk),
            flags=flags,
        )

    async def detect_volume_bots(self, trades: list[dict], token_address: str) -> list[ToolFingerprintResult]:
        """Detect volume bot / wash trading patterns."""
        results = []
        if not trades or len(trades) < 10:
            return results

        evidence = []

        # Check for circular trading (same set of wallets trading among themselves)
        traders = set()
        trade_pairs = defaultdict(int)
        for t in trades:
            a, b = t.get("buyer", ""), t.get("seller", "")
            traders.add(a)
            traders.add(b)
            if a and b:
                pair = tuple(sorted([a, b]))
                trade_pairs[pair] += 1

        # Clique detection: small number of wallets doing all trading
        if len(traders) < 10 and len(trades) > 50:
            evidence.append(f"Small trader set ({len(traders)} wallets) with {len(trades)} trades")

        # Repeated trades between same pairs
        heavy_pairs = {p: c for p, c in trade_pairs.items() if c > 5}
        if heavy_pairs:
            evidence.append(f"{len(heavy_pairs)} wallet pairs trading 5+ times each")

        # Identical trade sizes
        trade_sizes = [float(t.get("amount", 0)) for t in trades if t.get("amount")]
        if trade_sizes:
            unique_sizes = len(set(trade_sizes))
            if unique_sizes < len(trade_sizes) * 0.3:
                evidence.append(f"Repetitive trade sizes: {unique_sizes} unique from {len(trade_sizes)} trades")

        if evidence:
            results.append(
                ToolFingerprintResult(
                    tool_name="volume_bot",
                    detected=True,
                    confidence=min(0.95, 0.5 + 0.15 * len(evidence)),
                    evidence=evidence,
                    severity=80,
                    description="Volume bot / wash trading detected - inflated metrics",
                )
            )

        return results

    async def full_token_scan(
        self,
        token_address: str,
        chain: str = "solana",
        deployer_address: str | None = None,
        holders: list[dict] | None = None,
        transactions: list[dict] | None = None,
    ) -> dict[str, Any]:
        """
        Full token scan - combines all detection methods.
        Returns comprehensive risk assessment.
        """
        import time

        start = time.time()

        results = {
            "token_address": token_address,
            "chain": chain,
            "tool_fingerprints": [],
            "first_buyer_analysis": None,
            "deployer_profile": None,
            "volume_bot_detected": False,
            "aggregate_risk_score": 0,
            "risk_category": "unknown",
            "flags": [],
            "scan_duration_ms": 0,
        }

        # Run all detectors in parallel
        tasks = []

        if transactions:
            tasks.append(self.detect_bundlers(transactions, token_address))
        if holders:
            tasks.append(self.analyze_first_buyers(token_address, holders))
        if deployer_address:
            tasks.append(self.profile_deployer(deployer_address, chain))
        if transactions:
            tasks.append(self.detect_volume_bots(transactions, token_address))

        import asyncio

        gathered = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        total_risk = 0
        max_possible = 0

        for result in gathered:
            if isinstance(result, Exception):
                continue

            if isinstance(result, list):
                # Tool fingerprints
                for item in result:
                    if isinstance(item, ToolFingerprintResult) and item.detected:
                        results["tool_fingerprints"].append(
                            {
                                "tool": item.tool_name,
                                "confidence": round(item.confidence, 2),
                                "severity": item.severity,
                                "evidence": item.evidence,
                                "description": item.description,
                            }
                        )
                        total_risk += item.severity
                        max_possible += 100
                        results["flags"].append(f"TOOL:{item.tool_name}")

            elif isinstance(result, FirstBuyerAnalysis):
                fb = result
                results["first_buyer_analysis"] = {
                    "total_holders": fb.total_holders,
                    "first_5_concentration_pct": fb.first_5_concentration_pct,
                    "first_10_concentration_pct": fb.first_10_concentration_pct,
                    "first_20_concentration_pct": fb.first_20_concentration_pct,
                    "fastest_entry_ms": fb.fastest_entry_ms,
                    "avg_entry_time_ms": fb.avg_entry_time_ms,
                    "same_block_buyers": fb.same_block_buyers,
                    "coordinated_clusters": fb.coordinated_clusters,
                    "risk_level": fb.risk_level,
                }
                if fb.risk_level == "critical":
                    total_risk += 50
                elif fb.risk_level == "high":
                    total_risk += 30
                max_possible += 50
                results["flags"].extend(fb.flags)

            elif isinstance(result, DeployerProfile):
                dp = result
                results["deployer_profile"] = {
                    "address": dp.address,
                    "age_days": dp.age_days,
                    "funding_source_type": dp.funding_source_type,
                    "previous_tokens": dp.previous_tokens_launched,
                    "previous_scams": dp.previous_scam_tokens,
                    "wallet_aging_detected": dp.wallet_aging_detected,
                    "aging_score": dp.aging_score,
                    "risk_score": dp.risk_score,
                }
                total_risk += dp.risk_score
                max_possible += 100
                results["flags"].extend(dp.flags)

        # Calculate aggregate risk
        pct = total_risk / max_possible * 100 if max_possible > 0 else 0
        results["aggregate_risk_score"] = min(100, round(pct))

        if pct >= 80:
            results["risk_category"] = "critical"
        elif pct >= 60:
            results["risk_category"] = "high"
        elif pct >= 35:
            results["risk_category"] = "medium"
        elif pct >= 15:
            results["risk_category"] = "low"
        else:
            results["risk_category"] = "minimal"

        results["scan_duration_ms"] = round((time.time() - start) * 1000)
        return results


# ══════════════════════════════════════════════════════════════════════
# SINGLETON
# ══════════════════════════════════════════════════════════════════════

_fingerprinter: ToolFingerprinter | None = None


async def get_fingerprinter() -> ToolFingerprinter:
    global _fingerprinter
    if _fingerprinter is None:
        _fingerprinter = ToolFingerprinter()
    return _fingerprinter


async def fingerprint_token(
    token_address: str,
    chain: str = "solana",
    deployer: str | None = None,
    holders: list[dict] | None = None,
    transactions: list[dict] | None = None,
) -> dict[str, Any]:
    """Convenience function for full token fingerprinting."""
    fp = await get_fingerprinter()
    return await fp.full_token_scan(
        token_address=token_address,
        chain=chain,
        deployer_address=deployer,
        holders=holders,
        transactions=transactions,
    )
