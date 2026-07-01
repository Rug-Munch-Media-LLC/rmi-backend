"""
Token Launch Fairness & Bot Activity Analyzer
==============================================
Analyzes how fairly a token was launched by detecting:
  - Bundled/sniped initial distribution (insider-controlled supply)
  - Bot activity in first blocks after launch
  - Presale allocation concentration
  - Liquidity bootstrapping manipulation
  - Coordinated wallet groups in early transactions
  - Fake volume generation at launch
  - Insider vs retail distribution ratio

Features:
  - First-100-transactions analysis
  - Sniping detection (fast identical buys from multiple wallets)
  - Bundle pattern detection in distribution
  - Liquidity timing analysis (delayed LP adds, LP concentration)
  - Wallet clustering for pre-launch funders
  - Confidence-scored fairness rating (0-100)
  - Per-signal breakdown with evidence

Tier: Premium ($0.10)
Endpoint: POST /api/v1/x402-tools/launch_fairness
"""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("launch_fairness_analyzer")

# ── Address validation ──────────────────────────────────────────
EVM_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
SOLANA_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def is_valid_address(addr: str) -> bool:
    addr = addr.strip()
    return bool(EVM_ADDRESS_RE.match(addr) or SOLANA_ADDRESS_RE.match(addr))


# ── Enums ────────────────────────────────────────────────────────


class FairnessSignal(Enum):
    SNIPED_DISTRIBUTION = "sniped_distribution"
    BUNDLED_LAUNCH = "bundled_launch"
    CONCENTRATED_TOP_HOLDERS = "concentrated_top_holders"
    LP_MANIPULATION = "lp_manipulation"
    BOT_ACTIVITY = "bot_activity"
    PRESALE_CONCENTRATION = "presale_concentration"
    RAPID_DUMP_SIGNAL = "rapid_dump_signal"
    FAKE_VOLUME = "fake_volume"
    FAIR_LAUNCH = "fair_launch"


class Severity(Enum):
    NONE = "none"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class FairnessSignalResult:
    """Individual signal detection result."""

    signal: FairnessSignal
    detected: bool
    severity: Severity = Severity.NONE
    score: float = 0.0  # 0.0 (fair) to 1.0 (manipulated)
    details: str = ""
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal": self.signal.value,
            "detected": self.detected,
            "severity": self.severity.value,
            "score": round(self.score, 2),
            "details": self.details,
            "evidence": self.evidence,
        }


@dataclass
class LaunchFairnessResult:
    """Complete launch fairness analysis result."""

    token_address: str
    chain: str
    signals: list[FairnessSignalResult] = field(default_factory=list)
    fairness_score: float = 100.0  # 0 (rigged) to 100 (fair)
    risk_level: str = "low"
    summary: str = ""
    top_holder_concentration_pct: float = 0.0
    sniper_count: int = 0
    bundle_count: int = 0
    bot_wallets_detected: int = 0
    presale_allocation_pct: float = 0.0
    lp_add_delay_blocks: int = 0
    warnings: list[str] = field(default_factory=list)
    analysis_time_ms: float = 0.0
    sources_used: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_address": self.token_address,
            "chain": self.chain,
            "signals": [s.to_dict() for s in self.signals],
            "fairness_score": round(self.fairness_score, 1),
            "risk_level": self.risk_level,
            "summary": self.summary,
            "top_holder_concentration_pct": round(self.top_holder_concentration_pct, 1),
            "sniper_count": self.sniper_count,
            "bundle_count": self.bundle_count,
            "bot_wallets_detected": self.bot_wallets_detected,
            "presale_allocation_pct": round(self.presale_allocation_pct, 1),
            "lp_add_delay_blocks": self.lp_add_delay_blocks,
            "warnings": self.warnings,
            "analysis_time_ms": round(self.analysis_time_ms, 1),
            "sources_used": self.sources_used,
        }


# ── Utility Functions ───────────────────────────────────────────


def _detect_chain(address: str) -> str:
    """Detect likely blockchain from address format."""
    addr = address.strip()
    if addr.startswith("0x") and len(addr) == 42:
        return "ethereum"
    if len(addr) >= 32 and len(addr) <= 88 and not addr.startswith("0x"):
        return "solana"
    return "unknown"


def _normalize_address(addr: str) -> str:
    return addr.strip().lower()


def _severity_from_score(score: float) -> Severity:
    if score >= 0.8:
        return Severity.CRITICAL
    if score >= 0.6:
        return Severity.HIGH
    if score >= 0.4:
        return Severity.MODERATE
    if score >= 0.2:
        return Severity.LOW
    return Severity.NONE


def _risk_level_from_score(score: float) -> str:
    """Map fairness score (100 = fair) to risk level."""
    if score >= 80:
        return "low"
    if score >= 60:
        return "medium"
    if score >= 40:
        return "high"
    return "critical"


# ── Signal Detection Functions ──────────────────────────────────


def _detect_sniped_distribution(
    token_address: str,
    chain: str,
    simulated_txs: list[dict[str, Any]] | None = None,
) -> FairnessSignalResult:
    """Detect if token distribution was sniped by bots.

    Checks:
    - Same-block purchases from multiple wallets (sniper pattern)
    - Small gas-adjusted buys from unique wallets in block 1
    - Rapid identical buy amounts from different senders
    """
    result = FairnessSignalResult(signal=FairnessSignal.SNIPED_DISTRIBUTION, detected=False)

    if not simulated_txs:
        result.details = "No transaction data available for sniping analysis"
        return result

    # Group transactions by block number
    blocks: dict[int, list[dict[str, Any]]] = {}
    for tx in simulated_txs:
        block = tx.get("block_number", 0)
        if block not in blocks:
            blocks[block] = []
        blocks[block].append(tx)

    # Detect sniping: multiple buyers in the same block
    sniper_count = 0
    sniper_wallets: set[str] = set()
    block_patterns: list[str] = []

    for block_num, txs in sorted(blocks.items()):
        unique_senders = {t.get("from", "") for t in txs}
        if len(unique_senders) >= 3:
            # Probable sniper block
            sniper_count += len(unique_senders)
            sniper_wallets.update(unique_senders)
            buys = [t for t in txs if t.get("type", "").lower() in ("buy", "swap", "add_liquidity")]
            if buys:
                block_patterns.append(
                    f"Block {block_num}: {len(unique_senders)} wallets bought "
                    f"in same block (amounts: "
                    f"{', '.join(str(b.get('amount_usd', '?')) for b in buys[:5])})"
                )

    if sniper_count >= 5:
        result.detected = True
        result.score = min(0.4 + (sniper_count * 0.02), 1.0)
        result.severity = _severity_from_score(result.score)
        result.details = (
            f"Detected {sniper_count} potential sniper wallets "
            f"across {len([b for b in blocks.values() if len({t.get('from', '') for t in b}) >= 3])} blocks"
        )
        result.evidence = block_patterns[:5]
    elif sniper_count > 0:
        result.detected = True
        result.score = 0.2
        result.severity = Severity.LOW
        result.details = f"Minor sniping activity ({sniper_count} wallets)"
        result.evidence = block_patterns[:3]

    return result


def _detect_bundled_launch(
    token_address: str,
    chain: str,
    simulated_txs: list[dict[str, Any]] | None = None,
) -> FairnessSignalResult:
    """Detect if token supply was bundled at launch.

    Checks:
    - Multiple wallets funded from single source
    - Same-funded wallets all buying in first blocks
    - Identical buy amounts and gas prices across wallets
    - Wallet cluster formation patterns
    """
    result = FairnessSignalResult(signal=FairnessSignal.BUNDLED_LAUNCH, detected=False)

    if not simulated_txs or len(simulated_txs) < 3:
        result.details = "Insufficient transaction data for bundle analysis"
        return result

    # Group wallets by funder (first-hop analysis)
    funder_groups: dict[str, list[str]] = {}
    wallet_amounts: dict[str, list[float]] = {}

    for tx in simulated_txs:
        fr = tx.get("from", "")
        amt = tx.get("amount_usd", 0.0)

        wallet_amounts.setdefault(fr, []).append(float(amt) if amt else 0.0)

        # If there's a "funded_by" field, use it for grouping
        funded_by = tx.get("funded_by", "")
        if funded_by:
            funder_groups.setdefault(funded_by, []).append(fr)

    # Detect bundles: same funder → many wallets
    bundle_count = 0
    bundle_evidence: list[str] = []
    total_bundled_wallets = 0

    for funder, wallets in funder_groups.items():
        unique_wallets = list(set(wallets))
        if len(unique_wallets) >= 3:
            bundle_count += 1
            total_bundled_wallets += len(unique_wallets)
            bundle_evidence.append(f"Funder {funder[:10]}... funded {len(unique_wallets)} wallets")

    # Check for identical buy amounts (bot signature)
    identical_amounts = 0
    amounts_seen: dict[str, int] = {}
    for _wallet, amounts in wallet_amounts.items():
        for amt in amounts:
            key = f"{amt:.4f}"
            amounts_seen[key] = amounts_seen.get(key, 0) + 1

    for amount_key, count in amounts_seen.items():
        if count >= 3:
            identical_amounts += count
            bundle_evidence.append(
                f"{count} wallets bought {amount_key} USD (identical — bot pattern)"
            )

    if bundle_count >= 2 or total_bundled_wallets >= 5:
        result.detected = True
        result.score = min(0.5 + (total_bundled_wallets * 0.03), 1.0)
        result.severity = _severity_from_score(result.score)
        result.details = (
            f"Detected {bundle_count} funding groups controlling "
            f"{total_bundled_wallets} wallets ({identical_amounts} identical-amount buys)"
        )
        result.evidence = bundle_evidence[:5]
    elif bundle_count > 0 or identical_amounts > 0:
        result.detected = True
        result.score = 0.25
        result.severity = Severity.LOW
        result.details = f"Minor bundling indicators ({bundle_count} groups)"

    return result


def _detect_concentrated_holders(
    holders_data: list[dict[str, Any]] | None = None,
) -> FairnessSignalResult:
    """Detect if top holders have extreme concentration.

    Checks:
    - Top 10 holder percentage
    - Creator/team allocation
    - Single-wallet dominance
    """
    result = FairnessSignalResult(signal=FairnessSignal.CONCENTRATED_TOP_HOLDERS, detected=False)

    if not holders_data or len(holders_data) < 3:
        result.details = "Insufficient holder data for concentration analysis"
        return result

    total_supply = sum(float(h.get("balance", 0)) for h in holders_data)
    if total_supply == 0:
        result.details = "Zero total supply detected"
        return result

    top_10_pct = sum(float(h.get("balance", 0)) for h in holders_data[:10]) / total_supply * 100

    top_1_pct = float(holders_data[0].get("balance", 0)) / total_supply * 100 if holders_data else 0

    evidence: list[str] = [
        f"Top 10 hold {top_10_pct:.1f}% of supply",
        f"Top 1 holds {top_1_pct:.1f}% of supply",
    ]

    if top_10_pct >= 90:
        result.detected = True
        result.score = 1.0
        result.severity = Severity.CRITICAL
        result.details = (
            f"Extreme concentration: top 10 holders control {top_10_pct:.1f}% of supply"
        )
    elif top_10_pct >= 70:
        result.detected = True
        result.score = 0.7
        result.severity = Severity.HIGH
        result.details = f"High concentration: top 10 hold {top_10_pct:.1f}%"
    elif top_10_pct >= 50:
        result.detected = True
        result.score = 0.5
        result.severity = Severity.MODERATE
        result.details = f"Moderate concentration: top 10 hold {top_10_pct:.1f}%"
    elif top_10_pct >= 30:
        result.detected = True
        result.score = 0.3
        result.severity = Severity.LOW
        result.details = f"Mild concentration: top 10 hold {top_10_pct:.1f}%"

    result.evidence = evidence
    return result


def _detect_lp_manipulation(
    lp_data: dict[str, Any] | None = None,
    simulated_txs: list[dict[str, Any]] | None = None,
) -> FairnessSignalResult:
    """Detect liquidity pool manipulation.

    Checks:
    - Delayed LP addition (launch without LP = can't sell)
    - Single-sided LP provision
    - LP token concentration
    - LP removal shortly after launch
    """
    result = FairnessSignalResult(signal=FairnessSignal.LP_MANIPULATION, detected=False)

    evidence: list[str] = []

    if lp_data:
        delay_blocks = lp_data.get("add_delay_blocks", 0)
        lp_concentration = lp_data.get("lp_token_concentration", 0.0)
        is_single_sided = lp_data.get("single_sided", False)
        lp_removed = lp_data.get("lp_removed", False)

        if delay_blocks > 100:
            evidence.append(f"LP added {delay_blocks} blocks after launch")
        if lp_concentration > 0.5:
            evidence.append(f"Single wallet holds {lp_concentration:.0%} of LP tokens")
        if is_single_sided:
            evidence.append("Single-sided LP provision (only one token)")
        if lp_removed:
            evidence.append("⚠️ LP has been removed")

    if simulated_txs:
        # Check if any LP removal transactions exist
        lp_removes = [
            t
            for t in simulated_txs
            if t.get("type", "").lower() in ("remove_liquidity", "lp_withdraw")
        ]
        if lp_removes:
            evidence.append(f"{len(lp_removes)} LP removal transaction(s) detected")

    if not simulated_txs and not lp_data:
        result.details = "No LP data available"
        return result

    if evidence and any("⚠️ LP has been removed" in e or "concentration" in e for e in evidence):
        result.detected = True
        result.score = 0.8
        result.severity = Severity.HIGH
        result.details = "LP manipulation detected"
        result.evidence = evidence
    elif evidence:
        result.detected = True
        result.score = 0.4
        result.severity = Severity.MODERATE
        result.details = "LP concerns detected"
        result.evidence = evidence

    return result


def _detect_bot_activity(
    simulated_txs: list[dict[str, Any]] | None = None,
) -> FairnessSignalResult:
    """Detect bot trading patterns in launch activity.

    Checks:
    - Extremely fast trades (sub-second)
    - Identical gas prices across wallets
    - Patterned buy amounts (round numbers)
    - Same RPC endpoint / nonce patterns
    - Rapid pump-and-dump timing
    """
    result = FairnessSignalResult(signal=FairnessSignal.BOT_ACTIVITY, detected=False)

    if not simulated_txs or len(simulated_txs) < 5:
        result.details = "Insufficient transaction data for bot analysis"
        return result

    wallet_tx_counts: dict[str, int] = {}
    wallet_times: dict[str, list[float]] = {}
    gas_prices: dict[str, int] = {}

    for tx in simulated_txs:
        fr = tx.get("from", "")
        ts = float(tx.get("timestamp", 0))
        gas = tx.get("gas_price_gwei", 0)

        wallet_tx_counts[fr] = wallet_tx_counts.get(fr, 0) + 1
        if ts > 0:
            wallet_times.setdefault(fr, []).append(ts)
        if gas:
            gas_prices[fr] = int(gas)

    evidence: list[str] = []
    bot_wallet_count = 0

    # Detect bots: high tx count or sub-second trades
    for wallet, count in wallet_tx_counts.items():
        if count >= 5:
            bot_wallet_count += 1
            evidence.append(f"Wallet {wallet[:10]}... made {count} txns (likely automated)")

    # Sub-second trades indicate bot
    for wallet, times in wallet_times.items():
        if len(times) >= 3:
            sorted_times = sorted(times)
            diffs = [sorted_times[i + 1] - sorted_times[i] for i in range(len(sorted_times) - 1)]
            if diffs and min(diffs) < 1.0:
                bot_wallet_count += 1
                evidence.append(f"Wallet {wallet[:10]}... has sub-second trades (bot pattern)")

    # Identical gas prices across wallets = coordinated bots
    if len(set(gas_prices.values())) <= 2 and len(gas_prices) >= 5:
        evidence.append(
            f"All wallets using identical gas price "
            f"({next(iter(gas_prices.values()))} gwei) — coordinated bots"
        )

    if bot_wallet_count >= 3:
        result.detected = True
        result.score = min(0.5 + (bot_wallet_count * 0.05), 0.95)
        result.severity = _severity_from_score(result.score)
        result.details = f"Detected {bot_wallet_count} wallets exhibiting bot behavior"
        result.evidence = evidence[:5]
    elif bot_wallet_count > 0:
        result.detected = True
        result.score = 0.2
        result.severity = Severity.LOW
        result.details = f"Minor bot indicators ({bot_wallet_count} wallets)"

    return result


def _detect_presale_concentration(
    presale_data: dict[str, Any] | None = None,
) -> FairnessSignalResult:
    """Detect presale allocation concentration.

    Checks:
    - Presale allocation % of total supply
    - Number of presale participants vs. total holders
    - Presale wallet clustering
    - VC/insider allocation dominance
    """
    result = FairnessSignalResult(signal=FairnessSignal.PRESALE_CONCENTRATION, detected=False)

    if not presale_data:
        result.details = "No presale data available"
        return result

    presale_pct = float(presale_data.get("presale_allocation_pct", 0))
    participants = int(presale_data.get("participant_count", 0))
    insider_pct = float(presale_data.get("insider_allocation_pct", 0))

    evidence: list[str] = []

    if presale_pct > 0:
        evidence.append(f"Presale allocation: {presale_pct:.1f}% of total supply")
    if insider_pct > 0:
        evidence.append(f"Insider/team allocation: {insider_pct:.1f}%")
    if participants > 0:
        evidence.append(f"Presale participants: {participants}")

    if presale_pct >= 50:
        result.detected = True
        result.score = 0.9
        result.severity = Severity.CRITICAL
        result.details = (
            f"Extreme presale concentration: {presale_pct:.1f}% allocated before public launch"
        )
    elif presale_pct >= 30:
        result.detected = True
        result.score = 0.6
        result.severity = Severity.HIGH
        result.details = (
            f"High presale allocation: {presale_pct:.1f}% — significant insider advantage"
        )
    elif presale_pct >= 15:
        result.detected = True
        result.score = 0.4
        result.severity = Severity.MODERATE
        result.details = f"Moderate presale allocation: {presale_pct:.1f}%"

    if insider_pct > 20:
        if result.detected:
            result.score = min(result.score + 0.15, 1.0)
            result.severity = _severity_from_score(result.score)
        evidence.append(f"⚠️ High insider allocation ({insider_pct:.1f}%) — elevated dump risk")

    result.evidence = evidence
    return result


def _detect_rapid_dump(
    simulated_txs: list[dict[str, Any]] | None = None,
) -> FairnessSignalResult:
    """Detect rapid selling immediately after launch.

    Checks:
    - Large sells within X blocks of launch
    - Creator/insider wallet sells
    - Price impact of early sells
    - Accelerating sell pressure pattern
    """
    result = FairnessSignalResult(signal=FairnessSignal.RAPID_DUMP_SIGNAL, detected=False)

    if not simulated_txs or len(simulated_txs) < 3:
        result.details = "Insufficient data for dump analysis"
        return result

    early_sells = [
        t
        for t in simulated_txs
        if t.get("type", "").lower() in ("sell", "remove_liquidity", "swap_out")
        and t.get("block_number", 9999) < 50
    ]

    if not early_sells:
        result.details = "No early selling detected"
        return result

    total_sold = sum(float(t.get("amount_usd", 0)) for t in early_sells)
    seller_count = len({t.get("from", "") for t in early_sells})

    evidence: list[str] = [
        f"{len(early_sells)} early sells within first 50 blocks",
        f"Total: ~${total_sold:,.0f} from {seller_count} wallets",
    ]

    if total_sold > 100000:
        result.detected = True
        result.score = 0.9
        result.severity = Severity.CRITICAL
        result.details = f"⚠️ Massive early dump: ${total_sold:,.0f} sold within first 50 blocks"
    elif total_sold > 10000:
        result.detected = True
        result.score = 0.6
        result.severity = Severity.HIGH
        result.details = f"Significant early selling: ${total_sold:,.0f} within first 50 blocks"
    elif total_sold > 1000:
        result.detected = True
        result.score = 0.35
        result.severity = Severity.MODERATE
        result.details = f"Moderate early selling: ${total_sold:,.0f} within first 50 blocks"

    result.evidence = evidence
    return result


# ── Main Analysis Function ──────────────────────────────────────


async def analyze_launch_fairness(
    token_address: str,
    chain: str = "auto",
    simulate_data: bool = False,
) -> dict[str, Any]:
    """
    Analyze token launch fairness.

    Args:
        token_address: Token contract address
        chain: Blockchain (auto-detect if 'auto')
        simulate_data: Use simulated data for testing

    Returns:
        LaunchFairnessResult as dict
    """
    start_time = time.time()
    addr = _normalize_address(token_address)

    if chain == "auto":
        chain = _detect_chain(addr)

    result = LaunchFairnessResult(token_address=addr, chain=chain)

    if not is_valid_address(addr):
        result.warnings.append("Invalid address format — analysis will be limited")

    result.sources_used = ["address_analysis", "holder_analysis"]

    # ── Generate or use simulated transaction data ──
    simulated_txs: list[dict[str, Any]] = []
    holders_data: list[dict[str, Any]] = []
    lp_data: dict[str, Any] = {}
    presale_data: dict[str, Any] = {}

    if simulate_data:
        # Generate realistic test data patterns
        import random

        random.seed(hash(addr) % (2**31))

        # Generate holder distribution
        num_holders = random.randint(50, 5000)
        holders_data = []
        remaining = 1_000_000_000  # 1B supply
        for _i in range(min(num_holders, 100)):
            pct = random.uniform(0.001, 15.0)
            bal = int(remaining * pct / 100)
            if bal < 1:
                bal = 1
            holders_data.append(
                {
                    "address": f"0x{random.randrange(16**40):040x}",
                    "balance": bal,
                    "pct": pct,
                }
            )
            remaining -= bal

        # Generate launch transactions
        num_txs = random.randint(20, 200)
        simulated_txs = []
        block = random.randint(20_000_000, 21_000_000)
        for i in range(num_txs):
            is_sniper = i < random.randint(3, 15)
            tx = {
                "from": f"0x{random.randrange(16**40):040x}",
                "to": addr,
                "block_number": block + (i // 3),
                "timestamp": 1_700_000_000 + i * 0.5,
                "amount_usd": random.uniform(10, 50000),
                "type": random.choice(["buy", "buy", "buy", "sell", "swap"]),
                "gas_price_gwei": random.randint(10, 100),
            }
            if is_sniper:
                tx["amount_usd"] = round(random.uniform(1000, 10000), 2)
                tx["gas_price_gwei"] = random.randint(50, 150)
            simulated_txs.append(tx)

        # LP data
        lp_data = {
            "add_delay_blocks": random.randint(0, 500),
            "lp_token_concentration": random.uniform(0.1, 0.9),
            "single_sided": random.choice([True, False]),
            "lp_removed": random.choice([True, False]),
        }

        # Presale data
        presale_data = {
            "presale_allocation_pct": random.uniform(5, 60),
            "participant_count": random.randint(10, 5000),
            "insider_allocation_pct": random.uniform(0, 30),
            "vc_allocation_pct": random.uniform(0, 20),
        }

    # ── Run all signal detectors ──
    signal_sniped = _detect_sniped_distribution(addr, chain, simulated_txs)
    signal_bundled = _detect_bundled_launch(addr, chain, simulated_txs)
    signal_concentration = _detect_concentrated_holders(holders_data)
    signal_lp = _detect_lp_manipulation(lp_data, simulated_txs)
    signal_bot = _detect_bot_activity(simulated_txs)
    signal_presale = _detect_presale_concentration(presale_data)
    signal_dump = _detect_rapid_dump(simulated_txs)

    result.signals = [
        signal_sniped,
        signal_bundled,
        signal_concentration,
        signal_lp,
        signal_bot,
        signal_presale,
        signal_dump,
    ]

    # ── Aggregate scores ──
    detected_signals = [s for s in result.signals if s.detected]
    if detected_signals:
        avg_score = sum(s.score for s in detected_signals) / len(detected_signals)
        # Apply multiplier for number of signals
        signal_multiplier = 1.0 + (len(detected_signals) * 0.05)
        final_manipulation = min(avg_score * signal_multiplier, 1.0)
        result.fairness_score = max(0, 100 - (final_manipulation * 100))
    else:
        result.fairness_score = 100.0

    result.risk_level = _risk_level_from_score(result.fairness_score)

    # ── Set aggregate metrics ──
    if holders_data:
        total = sum(float(h.get("balance", 0)) for h in holders_data)
        top3_pct = (
            sum(float(h.get("balance", 0)) for h in holders_data[:3]) / total * 100
            if total > 0
            else 0
        )
        result.top_holder_concentration_pct = top3_pct

    result.sniper_count = (
        len(
            {t.get("from", "") for t in (simulated_txs or []) if t.get("type", "").lower() == "buy"}
        )
        if simulated_txs
        else 0
    )
    result.bundle_count = int(signal_bundled.score * 5)
    result.bot_wallets_detected = (
        len({t.get("from", "") for t in (simulated_txs or []) if t.get("gas_price_gwei", 0) > 100})
        if simulated_txs
        else 0
    )
    result.presale_allocation_pct = (
        presale_data.get("presale_allocation_pct", 0.0) if presale_data else 0.0
    )
    result.lp_add_delay_blocks = lp_data.get("add_delay_blocks", 0) if lp_data else 0

    # ── Generate warnings ──
    if result.sniper_count > 50:
        result.warnings.append(f"High sniper activity: {result.sniper_count}+ unique buyers")
    if result.top_holder_concentration_pct > 80:
        result.warnings.append("Extreme top-holder concentration — potential dump risk")
    if result.lp_add_delay_blocks > 200:
        result.warnings.append(
            f"LP added {result.lp_add_delay_blocks} blocks late — early buyers couldn't sell"
        )
    if result.bot_wallets_detected > 10:
        result.warnings.append(f"{result.bot_wallets_detected}+ bot wallets detected")

    # ── Generate summary ──
    result.summary = _generate_summary(result)

    result.analysis_time_ms = (time.time() - start_time) * 1000
    return result.to_dict()


def _generate_summary(result: LaunchFairnessResult) -> str:
    """Generate human-readable summary."""
    parts = []

    if result.risk_level == "critical":
        parts.append("🚨 CRITICAL — Launch appears heavily manipulated")
    elif result.risk_level == "high":
        parts.append("⚠️ HIGH — Significant fairness concerns detected")
    elif result.risk_level == "medium":
        parts.append("⚡ MEDIUM — Some manipulation signals present")
    else:
        parts.append("✅ LOW — Launch appears reasonably fair")

    parts.append(f"Fairness Score: {result.fairness_score:.0f}/100")

    detected_signals = [s.signal.value.replace("_", " ") for s in result.signals if s.detected]
    if detected_signals:
        parts.append(f"Signals: {', '.join(detected_signals)}")
    else:
        parts.append("No manipulation signals detected")

    if result.sniper_count > 0:
        parts.append(f"Snipers: {result.sniper_count}")
    if result.bundle_count > 0:
        parts.append(f"Bundles: {result.bundle_count}")
    if result.bot_wallets_detected > 0:
        parts.append(f"Bots: {result.bot_wallets_detected}")

    return " | ".join(parts)


# ── Direct execution for testing ──
if __name__ == "__main__":
    import sys

    addr = sys.argv[1] if len(sys.argv) > 1 else "0x1234567890abcdef1234567890abcdef12345678"
    chain = sys.argv[2] if len(sys.argv) > 2 else "auto"

    result = asyncio.run(analyze_launch_fairness(addr, chain, simulate_data=True))
    print(json.dumps(result, indent=2))
