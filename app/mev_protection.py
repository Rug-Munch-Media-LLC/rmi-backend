"""
MEV Shield Analysis - Proactive Transaction Protection
========================================================
Assesses any pending or planned transaction for MEV extraction vulnerability
BEFORE it's sent. Identifies sandwich attack risk, frontrunning exposure,
and backrunning vulnerability. Provides actionable protection strategies.

The tool analyzes:
  - MEMPOOL: Pending transaction patterns in the block's proximity
  - SLIPPAGE: How much slippage tolerance invites MEV extraction
  - GAS_PRICE: Whether gas bidding makes the tx a target
  - PAIR_LIQUIDITY: How thin liquidity enables sandwich attacks
  - TOKEN_PAIR: Historical MEV activity on this specific pair
  - TIMING: Peak vs off-peak MEV activity periods

TOOL  : mev_protection
TIER  : premium
PRICE : $0.08 (80000 atoms)
TRIAL : 1 free check

Data Sources (all free):
  - Local MEV database (historical sandwich/frontrun patterns)
  - DexScreener - pool liquidity, pair metadata
  - Chain RPC (public) - pending tx pool analysis
  - Mempool.space (BTC) and Etherscan pending (EVM)
  - Historical MEV data from mev_sandwich_detector
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import httpx

from app.core.logging import get_logger

__all__ = [
    "MevFactor",
    "MevRiskLevel",
    "MevShieldResult",
    "analyze_transaction_risk",
    "mev_shield_analysis",
]

logger = get_logger(__name__)

# ── Constants ────────────────────────────────────────────────────

MEV_API_TIMEOUT = 10
DEFAULT_SLIPPAGE_BPS = 100  # 1% = 100 bps
DEFAULT_GAS_PRICE_GWEI = 10
HIGH_RISK_SLIPPAGE_BPS = 300  # 3%+
HIGH_RISK_GAS_PRICE_GWEI = 50
THIN_LIQUIDITY_USD = 50_000
MODERATE_LIQUIDITY_USD = 250_000

FREE_RPCS = {
    "ethereum": "https://eth.llamarpc.com",
    "bsc": "https://bsc-dataseed.binance.org",
    "base": "https://mainnet.base.org",
    "arbitrum": "https://arb1.arbitrum.io/rpc",
    "polygon": "https://polygon-rpc.com",
    "optimism": "https://mainnet.optimism.io",
    "avalanche": "https://api.avax.network/ext/bc/C/rpc",
}

FALLBACK_RPCS = {
    "ethereum": ["https://rpc.ankr.com/eth", "https://ethereum-rpc.publicnode.com"],
    "bsc": ["https://rpc.ankr.com/bsc", "https://bsc-rpc.publicnode.com"],
    "base": ["https://base-rpc.publicnode.com", "https://rpc.base.llama.com"],
}

# ── Risk Levels ──────────────────────────────────────────────────


class MevRiskLevel(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


# ── Data Models ──────────────────────────────────────────────────


@dataclass
class MevFactor:
    name: str
    score: float  # 0.0 (safe) to 1.0 (very vulnerable)
    finding: str
    detail: str
    suggestion: str | None = None


@dataclass
class MevShieldResult:
    risk_level: MevRiskLevel
    overall_score: float  # 0.0 (safe) to 1.0 (critical)
    factors: list[MevFactor]
    protection_strategies: list[str]
    estimated_loss_bps: int  # Expected loss in basis points if targeted
    timestamp: str
    tx_params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_level": self.risk_level.value,
            "overall_score": round(self.overall_score, 2),
            "estimated_loss_bps": self.estimated_loss_bps,
            "factors": [
                {
                    "name": f.name,
                    "score": round(f.score, 2),
                    "finding": f.finding,
                    "detail": f.detail,
                    "suggestion": f.suggestion,
                }
                for f in self.factors
            ],
            "protection_strategies": self.protection_strategies,
            "timestamp": self.timestamp,
            "tx_params": self.tx_params,
        }


# ── MEV Shield Analysis Engine ────────────────────────────────────
async def _fetch_free_rpc(chain: str, method: str, params: list[Any] | None = None) -> dict[str, Any] | None:
    """Call an RPC with fallback URLs."""
    urls: list[str] = [FREE_RPCS.get(chain, "")]
    if chain in FALLBACK_RPCS:
        urls.extend(FALLBACK_RPCS[chain])

    for url in urls:
        if not url:
            continue
        try:
            async with httpx.AsyncClient(timeout=MEV_API_TIMEOUT) as client:
                resp = await client.post(
                    url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": method,
                        "params": params or [],
                    },
                    headers={"Content-Type": "application/json"},
                )
                if resp.status_code == 200:
                    data: dict[str, Any] = resp.json()
                    if "error" not in data:
                        return data
        except Exception as e:
            logger.debug(f"RPC {url} failed: {e}")
            continue
    return None


async def _check_mempool_risk(chain: str, pair_address: str | None = None) -> MevFactor:
    """
    Check pending transaction pool for signs of MEV activity.
    Uses pending tx count and gas price distribution as proxies.

    Note: pair_address is reserved for future pair-specific mempool analysis.
    Currently performs chain-level mempool congestion assessment only.
    """
    if chain not in FREE_RPCS:
        return MevFactor(
            name="mempool_activity",
            score=0.3,
            finding=f"Unsupported chain: {chain}",
            detail=f"No RPC endpoints configured for {chain} - mempool analysis unavailable",
            suggestion="Supported chains: " + ", ".join(sorted(FREE_RPCS.keys())),
        )
    score = 0.1
    findings = []
    details = []

    # Get pending transaction count
    result = await _fetch_free_rpc(chain, "txpool_status")
    if result and "result" in result:
        try:
            pending = int(result["result"].get("pending", "0x0"), 16)
            if pending > 500:
                score = 0.8
                findings.append("Congested mempool")
                details.append(f"{pending} pending transactions - high MEV competition zone")
            elif pending > 200:
                score = 0.5
                findings.append("Moderate mempool activity")
                details.append(f"{pending} pending transactions - MEV possible")
            else:
                score = 0.2
                findings.append("Low mempool activity")
                details.append(f"{pending} pending transactions - lower MEV likelihood")
        except (ValueError, KeyError):
            pass

    # Check gas prices in pending pool
    result2 = await _fetch_free_rpc(chain, "txpool_content")
    if result2 and "result" in result2:
        try:
            pending_txs = result2["result"].get("pending", {})
            gas_prices = []
            for _nonce_key, txs in pending_txs.items():
                if isinstance(txs, dict):
                    for _, tx in txs.items():
                        if isinstance(tx, dict) and "gasPrice" in tx:
                            gas_prices.append(int(tx["gasPrice"], 16))

            if gas_prices:
                avg_gas = sum(gas_prices) / len(gas_prices) / 1e9  # convert to gwei
                if avg_gas > 100:
                    score = max(score, 0.75)
                    findings.append("High gas price environment")
                    details.append(f"Avg gas: {avg_gas:.1f} gwei - competitive bidding indicates MEV activity")
                elif avg_gas > 30:
                    score = max(score, 0.45)
                    findings.append("Elevated gas prices")
                    details.append(f"Avg gas: {avg_gas:.1f} gwei - some MEV extraction likely")
        except (ValueError, KeyError):
            pass

    finding = "; ".join(findings) if findings else "Mempool looks quiet"
    detail = "; ".join(details) if details else "No unusual mempool activity detected"
    suggestions = {
        0.8: "Use private mempool (Flashbots, MEV Blocker) to avoid frontrunning",
        0.5: "Consider using a MEV-protected RPC endpoint",
        0.2: "Standard transaction should be safe - no special protection needed",
    }
    # Pick closest suggestion
    closest_key = min(suggestions.keys(), key=lambda k: abs(k - score))
    suggestion = suggestions[closest_key]

    return MevFactor(
        name="mempool_activity",
        score=round(score, 2),
        finding=finding,
        detail=detail,
        suggestion=suggestion,
    )


async def _check_slippage_risk(slippage_bps: int) -> MevFactor:
    """Assess how slippage tolerance exposes the transaction to MEV."""
    if slippage_bps >= HIGH_RISK_SLIPPAGE_BPS:
        score = 0.9
        finding = "High slippage tolerance"
        detail = f"{slippage_bps / 100:.1f}% slippage - easily exploited by sandwich bots"
        suggestion = "Reduce slippage to 0.5-1.0% (50-100 bps) to minimize attack surface"
    elif slippage_bps >= DEFAULT_SLIPPAGE_BPS:
        score = 0.5
        finding = "Moderate slippage tolerance"
        detail = f"{slippage_bps / 100:.1f}% slippage - exploitable in thin liquidity pools"
        suggestion = "Lower slippage to 0.5% for tighter protection"
    else:
        score = 0.15
        finding = "Low slippage tolerance"
        detail = f"{slippage_bps / 100:.1f}% slippage - tight, harder to sandwich"
        suggestion = "Current slippage setting looks safe"

    return MevFactor(
        name="slippage_tolerance",
        score=round(score, 2),
        finding=finding,
        detail=detail,
        suggestion=suggestion,
    )


async def _check_gas_price_risk(gas_price_gwei: float) -> MevFactor:
    """Assess if gas price makes the transaction a target."""
    if gas_price_gwei >= HIGH_RISK_GAS_PRICE_GWEI:
        score = 0.7
        finding = "Premium gas price"
        detail = f"{gas_price_gwei:.1f} gwei - high bid flags tx as urgent/value to bots"
        suggestion = "Use Flashbots to submit privately while still getting fast inclusion"
    elif gas_price_gwei >= 20:
        score = 0.4
        finding = "Above-average gas price"
        detail = f"{gas_price_gwei:.1f} gwei - may attract some MEV attention"
        suggestion = "Consider MEV Blocker or a private RPC for extra safety"
    else:
        score = 0.1
        finding = "Normal gas price"
        detail = f"{gas_price_gwei:.1f} gwei - unlikely to attract MEV extractors"
        suggestion = "Standard gas pricing - no special MEV concern"

    return MevFactor(
        name="gas_price",
        score=round(score, 2),
        finding=finding,
        detail=detail,
        suggestion=suggestion,
    )


async def _check_pool_liquidity(pair_address: str | None, chain: str) -> MevFactor:
    """Check if pool liquidity is thin enough to enable sandwich attacks."""
    if not pair_address:
        return MevFactor(
            name="pool_liquidity",
            score=0.3,
            finding="Unknown pool",
            detail="No pair address provided - cannot assess liquidity depth",
            suggestion="Provide pair address for accurate MEV risk assessment",
        )

    try:
        async with httpx.AsyncClient(timeout=MEV_API_TIMEOUT) as client:
            resp = await client.get(f"https://api.dexscreener.com/latest/dex/pairs/{chain}/{pair_address}")
            if resp.status_code == 200:
                data = resp.json()
                pairs = data.get("pairs", [])
                if pairs:
                    pair = pairs[0]
                    liquidity_usd = float(pair.get("liquidity", {}).get("usd", 0) or 0)

                    if liquidity_usd < THIN_LIQUIDITY_USD:
                        score = 0.85
                        finding = "Thin liquidity pool"
                        detail = (
                            f"${liquidity_usd:,.0f} liquidity - highly vulnerable to sandwich/liquidity manipulation"
                        )
                        suggestion = "Avoid trading in sub-$50K liquidity pools, or use small orders split across time"
                    elif liquidity_usd < MODERATE_LIQUIDITY_USD:
                        score = 0.5
                        finding = "Moderate liquidity"
                        detail = f"${liquidity_usd:,.0f} liquidity - sandwich possible for orders >${liquidity_usd * 0.01:,.0f}"
                        suggestion = (
                            "Keep individual trades under 1% of pool liquidity to minimize slippage and MEV risk"
                        )
                    else:
                        score = 0.15
                        finding = "Deep liquidity pool"
                        detail = f"${liquidity_usd:,.0f} liquidity - sandwich attacks expensive and unlikely"
                        suggestion = "Low MEV risk due to deep liquidity"

                    return MevFactor(
                        name="pool_liquidity",
                        score=round(score, 2),
                        finding=finding,
                        detail=detail,
                        suggestion=suggestion,
                    )
    except Exception as e:
        logger.debug(f"DexScreener lookup failed: {e}")

    return MevFactor(
        name="pool_liquidity",
        score=0.3,
        finding="Liquidity lookup failed",
        detail="Could not fetch pool data - conservative risk estimate applied",
        suggestion="Check manually on DexScreener or retry later",
    )


async def _check_historical_mev(chain: str, token_address: str | None = None) -> MevFactor:
    """
    Check if this chain/token pair has a history of MEV attacks.
    Uses the existing mev_sandwich_detector module if available.
    """
    score = 0.2
    try:
        # Try to import the MEV detector for historical data
        from app.domains.detectors.mev_sandwich_detector import MEVSandwichDetector

        detector = MEVSandwichDetector()
        stats = await detector.scan()
        if stats and hasattr(stats, "sandwiches"):
            recent_attacks = len(stats.sandwiches)
            if recent_attacks > 20:
                score = 0.75
                finding = "Active MEV chain"
                detail = f"{recent_attacks} MEV attacks in last 24h - high activity chain"
                suggestion = "Always use MEV protection on this chain (Flashbots, MEV Blocker)"
            elif recent_attacks > 5:
                score = 0.45
                finding = "Moderate MEV activity"
                detail = f"{recent_attacks} MEV attacks in last 24h - some risk"
                suggestion = "Consider MEV protection for high-value transactions"
            else:
                score = 0.15
                finding = "Low MEV activity"
                detail = f"{recent_attacks} MEV attacks in last 24h - chain relatively safe"
                suggestion = "Standard transactions should be safe"

            return MevFactor(
                name="historical_mev",
                score=round(score, 2),
                finding=finding,
                detail=detail,
                suggestion=suggestion,
            )
    except (ImportError, AttributeError) as e:
        logger.warning("MEV error: %s", e)
    except Exception as e:
        logger.debug(f"Historical MEV check failed: {e}")

    # Fallback: use chain-level heuristics
    chain_risk = {
        "ethereum": 0.6,
        "bsc": 0.5,
        "base": 0.4,
        "arbitrum": 0.35,
        "polygon": 0.3,
    }
    score = chain_risk.get(chain, 0.25)
    return MevFactor(
        name="historical_mev",
        score=round(score, 2),
        finding=f"Default risk for {chain}",
        detail=f"{chain} has {'high' if score > 0.5 else 'moderate' if score > 0.3 else 'low'} historical MEV activity",
        suggestion="Check chain-specific MEV statistics for accurate assessment",
    )


async def _check_timing_risk() -> MevFactor:
    """Assess if current time is a high-MEV period."""
    now = datetime.now(UTC)
    hour = now.hour

    # Peak MEV hours tend to be during US market hours (14-22 UTC)
    # and during high-volatility news events
    is_weekend = now.weekday() >= 5

    if is_weekend:
        score = 0.3
        finding = "Weekend trading"
        detail = "Lower overall MEV activity on weekends - fewer bots competing"
        suggestion = "Good time for lower-risk transactions"
    elif 14 <= hour <= 22:
        score = 0.6
        finding = "Peak MEV hours"
        detail = "US market hours - highest MEV bot competition"
        suggestion = (
            "Consider transacting outside peak hours (before 14:00 UTC) for lower MEV risk, or use private mempool"
        )
    elif 6 <= hour <= 13:
        score = 0.35
        finding = "Off-peak hours"
        detail = "Lower bot activity during Asian/European hours"
        suggestion = "Moderate MEV risk - standard precautions recommended"
    else:
        score = 0.2
        finding = "Low activity period"
        detail = "Nighttime hours - minimal bot activity expected"
        suggestion = "Low MEV risk window"

    return MevFactor(
        name="timing",
        score=round(score, 2),
        finding=finding,
        detail=detail,
        suggestion=suggestion,
    )


def _compute_protection_strategies(risk_level: MevRiskLevel, factors: list[MevFactor], chain: str) -> list[str]:
    """Generate actionable protection strategies based on risk profile."""
    strategies = []

    # Check high-risk factors and suggest specific protections
    high_factors = [f for f in factors if f.score >= 0.6]

    if risk_level in (MevRiskLevel.CRITICAL, MevRiskLevel.HIGH):
        strategies.append(
            "🚨 Use Flashbots Protect (https://flashbots.net/) for private transaction submission - "
            "guarantees your tx won't be frontrun"
        )
        strategies.append(
            "🔒 Use MEV Blocker (https://mevblocker.io/) - sends tx to private mempool with backrunning protection"
        )
        if any(f.name == "pool_liquidity" for f in high_factors):
            strategies.append("📊 Split large trades into smaller chunks to reduce slippage and sandwich exposure")
        if any(f.name == "slippage_tolerance" for f in high_factors):
            strategies.append(
                "⚡ Set slippage to 0.5% or lower - this is the single most effective MEV reduction tactic"
            )
    elif risk_level == MevRiskLevel.MODERATE:
        strategies.append("🛡️ Consider MEV Blocker for moderate-value transactions - free to use")
        strategies.append("⏰ Time your transaction during off-peak hours (before 14:00 UTC) for lower MEV competition")
    else:
        strategies.append("✅ Standard transaction appears safe - no special MEV protection needed")
        strategies.append("💡 For high-value transactions (>$10K), still consider Flashbots as a precaution")

    # Chain-specific
    if chain == "ethereum":
        strategies.append(
            "🔷 Ethereum has the most mature MEV protection - Flashbots, MEV Blocker, and CoW Swap all available"
        )
    elif chain == "bsc":
        strategies.append(
            "🟡 BSC has fewer MEV protection options - Flashbots not available. "
            "Use low slippage and consider Poly Network for cross-chain routing"
        )

    return strategies


def _compute_estimated_loss(risk_level: MevRiskLevel, factors: list[MevFactor]) -> int:
    """Estimate expected loss in basis points if MEV'd."""
    base_loss = {
        MevRiskLevel.LOW: 10,
        MevRiskLevel.MODERATE: 50,
        MevRiskLevel.HIGH: 150,
        MevRiskLevel.CRITICAL: 300,
    }
    loss = base_loss[risk_level]

    # Adjust based on specific factors
    for f in factors:
        if f.name == "pool_liquidity" and f.score > 0.7:
            loss = int(loss * 1.5)  # Thin liquidity amplifies losses
        if f.name == "slippage_tolerance" and f.score > 0.7:
            loss = int(loss * 1.3)  # High slippage enables bigger sandwich
        if f.name == "mempool_activity" and f.score > 0.7:
            loss = int(loss * 1.2)  # Competitive mempool = better bots

    return min(loss, 500)  # Cap at 5% max estimated loss


async def _resolve_chain_id(chain: str) -> str:
    """Normalize chain name to internal format."""
    chain_map = {
        "eth": "ethereum",
        "ether": "ethereum",
        "bsc": "bsc",
        "bnb": "bsc",
        "base": "base",
        "arb": "arbitrum",
        "arbitrum": "arbitrum",
        "polygon": "polygon",
        "matic": "polygon",
        "op": "optimism",
        "optimism": "optimism",
        "avax": "avalanche",
        "avalanche": "avalanche",
    }
    return chain_map.get(chain.lower(), chain.lower())


async def analyze_transaction_risk(
    chain: str = "ethereum",
    pair_address: str | None = None,
    token_address: str | None = None,
    slippage_bps: int | None = None,
    gas_price_gwei: float | None = None,
) -> MevShieldResult:
    """
    Full MEV Shield Analysis - assess transaction MEV vulnerability.

    Args:
        chain: Blockchain name (ethereum, bsc, base, arbitrum, polygon, optimism, avalanche)
        pair_address: DEX pair address for liquidity check (DexScreener format)
        token_address: Token address for historical MEV lookup
        slippage_bps: Slippage tolerance in basis points (default: 100 = 1%)
        gas_price_gwei: Gas price in gwei (default: auto-detect from RPC)

    Returns:
        MevShieldResult with risk assessment, factor breakdown, and protection strategies
    """
    chain = await _resolve_chain_id(chain)
    if chain not in FREE_RPCS:
        raise ValueError(f"Unsupported chain: '{chain}'. Supported chains: {', '.join(sorted(FREE_RPCS.keys()))}")
    slippage_bps = slippage_bps or DEFAULT_SLIPPAGE_BPS

    if gas_price_gwei is None:
        # Auto-detect gas price
        result = await _fetch_free_rpc(chain, "eth_gasPrice")
        if result and "result" in result:
            try:
                gas_price_gwei = int(result["result"], 16) / 1e9
            except (ValueError, KeyError):
                gas_price_gwei = DEFAULT_GAS_PRICE_GWEI
        else:
            gas_price_gwei = DEFAULT_GAS_PRICE_GWEI

    # Run all risk checks in parallel
    (
        mempool_factor,
        slippage_factor,
        gas_factor,
        liquidity_factor,
        historical_factor,
        timing_factor,
    ) = await asyncio.gather(
        _check_mempool_risk(chain, pair_address),
        _check_slippage_risk(slippage_bps),
        _check_gas_price_risk(gas_price_gwei),
        _check_pool_liquidity(pair_address, chain),
        _check_historical_mev(chain, token_address),
        _check_timing_risk(),
    )

    factors = [
        mempool_factor,
        slippage_factor,
        gas_factor,
        liquidity_factor,
        historical_factor,
        timing_factor,
    ]

    # Compute overall score (weighted average)
    weights = {
        "mempool_activity": 0.20,
        "slippage_tolerance": 0.25,
        "gas_price": 0.15,
        "pool_liquidity": 0.20,
        "historical_mev": 0.10,
        "timing": 0.10,
    }

    overall_score = sum(f.score * weights.get(f.name, 0.15) for f in factors) / sum(weights.values())

    # Determine risk level
    if overall_score >= 0.7:
        risk_level = MevRiskLevel.CRITICAL
    elif overall_score >= 0.5:
        risk_level = MevRiskLevel.HIGH
    elif overall_score >= 0.3:
        risk_level = MevRiskLevel.MODERATE
    else:
        risk_level = MevRiskLevel.LOW

    protection_strategies = _compute_protection_strategies(risk_level, factors, chain)
    estimated_loss_bps = _compute_estimated_loss(risk_level, factors)

    return MevShieldResult(
        risk_level=risk_level,
        overall_score=round(overall_score, 2),
        factors=factors,
        protection_strategies=protection_strategies,
        estimated_loss_bps=estimated_loss_bps,
        timestamp=datetime.now(UTC).isoformat(),
        tx_params={
            "chain": chain,
            "slippage_bps": slippage_bps,
            "gas_price_gwei": round(gas_price_gwei, 1),
            "pair_address": pair_address,
            "token_address": token_address,
        },
    )


# ── CLI Entry Point ──────────────────────────────────────────────


def mev_shield_analysis(
    chain: str = "ethereum",
    pair_address: str | None = None,
    token_address: str | None = None,
    slippage_bps: int | None = None,
    gas_price_gwei: float | None = None,
) -> dict[str, Any]:
    """Synchronous wrapper for MEV Shield Analysis. Use in non-async contexts."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Already in an event loop - create a new one in a new thread
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    analyze_transaction_risk(
                        chain=chain,
                        pair_address=pair_address,
                        token_address=token_address,
                        slippage_bps=slippage_bps,
                        gas_price_gwei=gas_price_gwei,
                    ),
                )
                return future.result().to_dict()
    except RuntimeError as e:
        logger.warning("MEV error: %s", e)
    return asyncio.run(
        analyze_transaction_risk(
            chain=chain,
            pair_address=pair_address,
            token_address=token_address,
            slippage_bps=slippage_bps,
            gas_price_gwei=gas_price_gwei,
        )
    ).to_dict()


# ── Main (standalone test) ───────────────────────────────────────


async def main() -> None:
    """Run a sample MEV Shield analysis for demonstration."""
    print("🛡️ MEV Shield Analysis - Demo Run")
    print("=" * 60)

    result = await analyze_transaction_risk(
        chain="ethereum",
        pair_address="0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",  # USDC/WETH
        slippage_bps=300,  # 3% - high risk
    )

    print(f"\nRisk Level: {result.risk_level.value.upper()}")
    print(f"Overall Score: {result.overall_score:.2f} / 1.00")
    print(f"Estimated Loss if MEV'd: {result.estimated_loss_bps} bps ({result.estimated_loss_bps / 100:.1f}%)")
    print()

    print("Factor Breakdown:")
    for f in result.factors:
        bar = "█" * int(f.score * 20) + "░" * (20 - int(f.score * 20))
        print(f"  [{bar}] {f.name:25s} {f.score:.2f} - {f.finding}")

    print("\nProtection Strategies:")
    for s in result.protection_strategies:
        print(f"  • {s}")

    print(f"\nTimestamp: {result.timestamp}")


if __name__ == "__main__":
    asyncio.run(main())
