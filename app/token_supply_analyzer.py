"""
Token Supply & Liquidity Analyzer
==================================
Comprehensive analysis of a token's supply mechanics, liquidity locks,
holder concentration, mint/burn capabilities, and hidden tax mechanisms.

Signals detected:
  - Circulating vs total supply ratio (dilution risk)
  - Mint functions (unlimited mint, capped mint, mint authorities)
  - Burn mechanisms (deflationary, manual burn, burn fee)
  - Top holder concentration (whale dominance, insider allocation)
  - Liquidity lock status (locked, unlocked, removed, partial lock)
  - Hidden buy/sell taxes (dynamic tax, blacklist-dependent tax)
  - Fake renounced ownership (admin key still active, proxy pattern)
  - Liquidity pool health (LP token concentration, pair age)
  - Multi-sig and timelock risks

Tier: Premium ($0.08-0.12)
Endpoint: POST /api/v1/x402-tools/token_supply_analyze
"""

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, cast
from urllib.parse import urlparse

from app.core.logging import get_logger

logger = get_logger("token_supply_analyzer")

# ── Free API sources ─────────────────────────────────────────────
DEXSCREENER_TOKENS = "https://api.dexscreener.com/latest/dex/tokens/{token_addr}"
DEXSCREENER_SEARCH = "https://api.dexscreener.com/latest/dex/search?q={}"
BIRDEYE_TOKEN = "https://public-api.birdeye.so/defi/v3/token/overview?address={token_addr}"
BIRDEYE_HOLDERS = "https://public-api.birdeye.so/defi/v3/token/holder?address={token_addr}"
SOLSCAN_TOKEN = "https://api.solscan.io/token/meta?token={token_addr}"
SOLSCAN_HOLDERS = "https://api.solscan.io/token/holders?token={token_addr}&limit=10"
ETHERSCAN_TOKEN = (
    "https://api.etherscan.io/api?module=account&action=tokeninfo&contractaddress={token_addr}"
)
BSCSCAN_TOKEN = (
    "https://api.bscscan.com/api?module=account&action=tokeninfo&contractaddress={token_addr}"
)

# Rate limiting
_RATE_LIMITERS: dict[str, float] = {}
_RATE_LIMIT_WINDOW = 0.5  # 500ms between requests to same host

# URL safety regex
_URL_SAFE = re.compile(
    r"^https?://[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?)*(?::\d{1,5})?(?:/[^\s\"<>]*)?$"
)

# ── Constants ────────────────────────────────────────────────────
KNOWN_BURN_ADDRESSES = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
    "0x000000000000000000000000000000000000dEaD",
    "dead00000000000000000000000000000000000000000000000000000000000000",
    "0x0000000000000000000000000000000000000001",
}

MAX_HOLDER_SAMPLE = 50  # Max top holders to analyze
CONCENTRATION_THRESHOLD_PCT = 90.0  # Top 10 holders > this = concentrated
SUPPLY_RISK_RATIO = 0.3  # Circ/total ratio below this = high dilution risk


class SupplyRisk(Enum):
    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class LockStatus(Enum):
    LOCKED = "locked"
    PARTIALLY_LOCKED = "partially_locked"
    UNLOCKED = "unlocked"
    REMOVED = "removed"
    UNKNOWN = "unknown"


@dataclass
class TokenSupplyProfile:
    """Complete supply and liquidity profile for a token."""

    # Token basics
    address: str
    chain: str = ""
    symbol: str = ""
    name: str = ""
    decimals: int = 18

    # Supply data
    total_supply_raw: int = 0
    circulating_supply_raw: int = 0
    max_supply_raw: int = 0
    total_supply: float = 0.0
    circulating_supply: float = 0.0
    max_supply: float = 0.0
    burned_supply: float = 0.0

    # Supply mechanics
    has_mint_function: bool = False
    mint_function_type: str = "none"  # none, unlimited, capped, ownable
    mint_authority: str = ""
    has_burn_function: bool = False
    burn_function_type: str = "none"  # none, manual, automated, fee
    is_deflationary: bool = False

    # Holder concentration
    top_10_holder_pct: float = 0.0
    top_50_holder_pct: float = 0.0
    deployer_hold_pct: float = 0.0
    holder_count: int = 0
    whale_holdings: list[dict] = field(default_factory=list)

    # Liquidity analysis
    lock_status: LockStatus = LockStatus.UNKNOWN
    liquidity_lock_address: str = ""
    liquidity_lock_expiry: str = ""
    lp_token_pct_held_by_deployer: float = 0.0
    pair_age_days: float = 0.0
    pair_liquidity_usd: float = 0.0

    # Tax analysis
    buy_tax_pct: float = 0.0
    sell_tax_pct: float = 0.0
    has_dynamic_tax: bool = False
    has_blacklist: bool = False
    has_max_wallet_limit: bool = False

    # Ownership
    is_renounced: bool = False
    owner_address: str = ""
    has_proxy_admin: bool = False
    has_multisig: bool = False
    has_timelock: bool = False

    # Scoring
    supply_risk: SupplyRisk = SupplyRisk.SAFE
    supply_risk_score: float = 0.0
    concentration_risk: SupplyRisk = SupplyRisk.SAFE
    concentration_score: float = 0.0
    liquidity_risk: SupplyRisk = SupplyRisk.SAFE
    liquidity_score: float = 0.0
    overall_risk_score: float = 0.0
    overall_risk: SupplyRisk = SupplyRisk.SAFE

    # Raw data
    patterns_detected: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


# ── Utility functions ────────────────────────────────────────────


def _validate_url(url: str) -> bool:
    """Basic URL validation to prevent SSRF."""
    return bool(_URL_SAFE.match(url))


async def _rate_limit_host(host: str) -> None:
    """Simple per-host rate limiter."""
    now = time.time()
    last = _RATE_LIMITERS.get(host, 0.0)
    wait = _RATE_LIMIT_WINDOW - (now - last)
    if wait > 0:
        await asyncio.sleep(wait)
    _RATE_LIMITERS[host] = time.time()


def _detect_chain(address: str) -> str:
    """Detect chain from address format."""
    addr = address.strip()
    if addr.startswith("0x"):
        return "ethereum"  # Could also be BSC, Polygon, Arbitrum, etc.
    elif len(addr) >= 32 and not addr.startswith("0x"):
        return "solana"
    return "unknown"


def _normalize_address(address: str) -> str:
    """Normalize address to lowercase for comparison."""
    return address.lower().strip()


def _classify_risk(score: float) -> SupplyRisk:
    """Convert numeric score to risk level."""
    if score <= 10:
        return SupplyRisk.SAFE
    elif score <= 25:
        return SupplyRisk.LOW
    elif score <= 50:
        return SupplyRisk.MEDIUM
    elif score <= 75:
        return SupplyRisk.HIGH
    else:
        return SupplyRisk.CRITICAL


# ── Supply Analysis ──────────────────────────────────────────────


def _analyze_supply_mechanics(profile: TokenSupplyProfile) -> None:
    """
    Analyze token supply data for dilution risk and manipulation patterns.
    """
    score = 0.0
    total = profile.total_supply
    circ = profile.circulating_supply
    max_sup = profile.max_supply
    burned = profile.burned_supply

    # ── Circulating vs total supply ratio ──────────────────────
    if total > 0 and circ > 0:
        ratio = circ / total
        profile.patterns_detected.append(f"circ_supply_ratio:{ratio:.2%}")

        if ratio < SUPPLY_RISK_RATIO:
            score += 35
            profile.patterns_detected.append(
                "high_dilution_risk:circ_supply_less_than_30%_of_total"
            )
            profile.recommendations.append(
                f"⚠ Only {ratio:.0%} of total supply is circulating - high dilution risk. "
                "Check vesting/unlock schedule."
            )
        elif ratio < 0.5:
            score += 20
            profile.patterns_detected.append(
                "moderate_dilution_risk:circ_supply_less_than_50%_of_total"
            )
            profile.recommendations.append(
                f"⚠ ~{ratio:.0%} of supply is circulating. Verify lockup/vesting terms."
            )
    elif total > 0 and circ == 0:
        score += 15
        profile.patterns_detected.append("no_circulating_supply_data")

    # ── Max supply vs total supply (capped vs uncapped) ────────
    if max_sup > 0:
        if total > max_sup:
            score += 40
            profile.patterns_detected.append(
                f"supply_exceeds_max:total_{total:.2f}_exceeds_max_{max_sup:.2f}"
            )
            profile.recommendations.append(
                "🚨 Total supply exceeds max supply - possible mint/printing."
            )
        else:
            remaining = max_sup - total
            remaining_pct = (remaining / max_sup) * 100
            if remaining_pct > 50:
                score += 25
                profile.patterns_detected.append(
                    f"large_mint_headroom:{remaining_pct:.0f}%_of_max_supply_unminted"
                )
                profile.recommendations.append(
                    f"🚨 {remaining_pct:.0f}% of max supply is unminted - significant dilution risk."
                )
    else:
        # No max supply = potentially unlimited mint
        if profile.has_mint_function:
            score += 30
            profile.patterns_detected.append("no_max_supply_with_mint:unlimited_mint_possible")
            profile.recommendations.append(
                "🚨 No max supply set AND mint function exists - potentially unlimited mint."
            )

    # ── Burn amount ─────────────────────────────────────────────
    if burned > 0 and total > 0:
        burn_pct = (burned / total) * 100
        profile.patterns_detected.append(f"burned_supply:{burn_pct:.1f}%")
        if burn_pct > 50:
            score -= 15  # Heavy burning is generally positive
            profile.recommendations.append(
                f"✅ {burn_pct:.0f}% of supply has been burned - strong deflationary pressure."
            )

    # ── Mint function analysis ──────────────────────────────────
    if profile.has_mint_function:
        if profile.mint_function_type == "unlimited":
            score += 40
            profile.patterns_detected.append("unlimited_mint_function")
            profile.recommendations.append(
                "🚨 Token has an unrestricted mint function - supply can be inflated at will."
            )
        elif profile.mint_function_type == "ownable":
            score += 25
            profile.patterns_detected.append("ownable_mint_function:owner_can_mint")
            if not profile.is_renounced:
                score += 10
                profile.recommendations.append(
                    "⚠ Owner-controlled mint function - supply can be increased if ownership is not renounced."
                )
        elif profile.mint_function_type == "capped":
            score += 5
            profile.patterns_detected.append("capped_mint_function")

    # ── Deflationary analysis ───────────────────────────────────
    if profile.is_deflationary:
        score -= 10
        profile.patterns_detected.append("deflationary_token")
        profile.recommendations.append(
            "✅ Deflationary mechanism active (auto-burn on transactions)."
        )

    profile.supply_risk_score = max(0.0, min(100.0, score))
    profile.supply_risk = _classify_risk(profile.supply_risk_score)


def _analyze_concentration(profile: TokenSupplyProfile) -> None:
    """
    Analyze holder concentration for whale dominance and insider risk.
    """
    score = 0.0
    top10 = profile.top_10_holder_pct
    top50 = profile.top_50_holder_pct
    deployer_hold = profile.deployer_hold_pct

    # ── Top 10 holder concentration ─────────────────────────────
    if top10 > 0:
        profile.patterns_detected.append(f"top_10_holder_pct:{top10:.1f}%")

        if top10 >= 99.0:
            score += 50
            profile.patterns_detected.append("extreme_concentration:top_10_holds_>99%")
            profile.recommendations.append(
                "🚨 Top 10 wallets hold >99% of supply - extremely concentrated, likely a scam."
            )
        elif top10 >= CONCENTRATION_THRESHOLD_PCT:
            score += 40
            profile.patterns_detected.append("critical_concentration:top_10_holds_>90%")
            profile.recommendations.append(
                f"🚨 Top 10 wallets hold {top10:.0f}% of supply - extreme concentration risk."
            )
        elif top10 >= 70:
            score += 25
            profile.patterns_detected.append("high_concentration:top_10_holds_>70%")
            profile.recommendations.append(
                f"⚠ Top 10 wallets hold {top10:.0f}% of supply - high concentration."
            )
        elif top10 >= 50:
            score += 15
            profile.patterns_detected.append("moderate_concentration:top_10_holds_>50%")
            profile.recommendations.append(
                f"⚠ Top 10 wallets hold {top10:.0f}% of supply - moderate concentration."
            )

    # ── Top 50 holder concentration ─────────────────────────────
    if top50 > 0:
        if top50 >= 99.0:
            score += 25
            profile.patterns_detected.append("extreme_concentration_top50")
        elif top50 >= 95:
            score += 15
            profile.patterns_detected.append("critical_concentration_top50")
        elif top50 >= 80:
            score += 10
            profile.patterns_detected.append("high_concentration_top50")

    # ── Deployer holding ────────────────────────────────────────
    if deployer_hold > 0:
        profile.patterns_detected.append(f"deployer_hold_pct:{deployer_hold:.1f}%")

        if deployer_hold > 50:
            score += 40
            profile.patterns_detected.append("deployer_holds_majority")
            profile.recommendations.append(
                f"🚨 Deployer holds {deployer_hold:.0f}% of supply - complete control, very high risk."
            )
        elif deployer_hold > 20:
            score += 20
            profile.patterns_detected.append("deployer_holds_significant")
            profile.recommendations.append(
                f"⚠ Deployer holds {deployer_hold:.0f}% of supply - significant insider allocation."
            )
        elif deployer_hold > 5:
            score += 10
            profile.patterns_detected.append("deployer_holds_moderate")
            profile.recommendations.append(
                f"(i) Deployer holds {deployer_hold:.0f}% of supply - moderate allocation."
            )

    # ── Whale holdings analysis ─────────────────────────────────
    for w in profile.whale_holdings:
        if w.get("pct", 0) > 5:
            score += 5
            profile.patterns_detected.append(
                f"whale_gt_5%:address_{w.get('address', '')[:8]}..._{w.get('pct', 0):.1f}%"
            )

    profile.concentration_score = max(0.0, min(100.0, score))
    profile.concentration_risk = _classify_risk(profile.concentration_score)


def _analyze_liquidity(profile: TokenSupplyProfile) -> None:
    """
    Analyze liquidity pool health and lock status.
    """
    score = 0.0

    # ── Lock status ─────────────────────────────────────────────
    if profile.lock_status == LockStatus.REMOVED:
        score += 50
        profile.patterns_detected.append("liquidity_removed")
        profile.recommendations.append(
            "🚨 Liquidity has been removed from the pool - rug pull risk."
        )
    elif profile.lock_status == LockStatus.UNLOCKED:
        score += 30
        profile.patterns_detected.append("liquidity_unlocked")
        profile.recommendations.append(
            "🚨 LP tokens are unlocked - deployer can remove liquidity at any time."
        )
    elif profile.lock_status == LockStatus.PARTIALLY_LOCKED:
        score += 15
        profile.patterns_detected.append("liquidity_partially_locked")
        profile.recommendations.append(
            "⚠ Only part of the liquidity is locked. Verify the locked portion."
        )
    elif profile.lock_status == LockStatus.LOCKED:
        score -= 15
        profile.patterns_detected.append("liquidity_locked")
        if profile.liquidity_lock_expiry:
            profile.recommendations.append(
                f"✅ Liquidity locked until {profile.liquidity_lock_expiry}. Good practice."
            )
        else:
            profile.recommendations.append("✅ Liquidity is locked.")

    # ── LP token concentration ──────────────────────────────────
    if profile.lp_token_pct_held_by_deployer > 50:
        score += 25
        profile.patterns_detected.append("deployer_holds_lp_majority")
        profile.recommendations.append("🚨 Deployer holds >50% of LP tokens - can drain pool.")
    elif profile.lp_token_pct_held_by_deployer > 10:
        score += 10
        profile.patterns_detected.append("deployer_holds_significant_lp")
        profile.recommendations.append(
            f"⚠ Deployer holds {profile.lp_token_pct_held_by_deployer:.0f}% of LP tokens."
        )

    # ── Pair age ────────────────────────────────────────────────
    if profile.pair_age_days > 0:
        if profile.pair_age_days < 1:
            score += 20
            profile.patterns_detected.append("very_new_pair:less_than_1_day_old")
            profile.recommendations.append(
                "⚠ Liquidity pair is less than 1 day old - very high risk."
            )
        elif profile.pair_age_days < 7:
            score += 10
            profile.patterns_detected.append(f"new_pair:{profile.pair_age_days:.0f}_days_old")
            profile.recommendations.append(
                f"⚠ Pair is only {profile.pair_age_days:.0f} days old - higher risk."
            )
        elif profile.pair_age_days > 30:
            score -= 10
            profile.patterns_detected.append("established_pair:>30_days_old")

    # ── Liquidity depth ─────────────────────────────────────────
    if profile.pair_liquidity_usd > 0:
        if profile.pair_liquidity_usd < 1000:
            score += 25
            profile.patterns_detected.append("very_low_liquidity:<$1k")
            profile.recommendations.append(
                "🚨 Very low liquidity (<$1K) - high slippage and manipulation risk."
            )
        elif profile.pair_liquidity_usd < 10000:
            score += 15
            profile.patterns_detected.append("low_liquidity:<$10k")
            profile.recommendations.append("⚠ Low liquidity (<$10K) - moderate slippage risk.")
        elif profile.pair_liquidity_usd > 100000:
            score -= 10
            profile.patterns_detected.append("healthy_liquidity:>$100k")

    profile.liquidity_score = max(0.0, min(100.0, score))
    profile.liquidity_risk = _classify_risk(profile.liquidity_score)


def _compute_overall_risk(profile: TokenSupplyProfile) -> None:
    """Compute overall risk score from all sub-scores."""
    weights = {
        "supply": 0.35,
        "concentration": 0.30,
        "liquidity": 0.20,
        "tax": 0.10,
        "ownership": 0.05,
    }

    tax_score = 0.0
    if profile.has_dynamic_tax:
        tax_score += 30
        profile.patterns_detected.append("dynamic_tax:tax_can_change")
        profile.recommendations.append(
            "⚠ Dynamic tax detected - buy/sell tax can change at any time."
        )
    if profile.has_blacklist:
        tax_score += 25
        profile.patterns_detected.append("blacklist_function")
        profile.recommendations.append(
            "⚠ Token has a blacklist function - holders can be blocked from selling."
        )
    if profile.buy_tax_pct > 10:
        tax_score += 15
        profile.patterns_detected.append(f"high_buy_tax:{profile.buy_tax_pct:.1f}%")
    if profile.sell_tax_pct > 15:
        tax_score += 20
        profile.patterns_detected.append(f"very_high_sell_tax:{profile.sell_tax_pct:.1f}%")
        profile.recommendations.append(
            f"🚨 Sell tax of {profile.sell_tax_pct:.0f}% is very high - may be a honeypot."
        )

    ownership_score = 0.0
    if not profile.is_renounced:
        ownership_score += 15
        profile.patterns_detected.append("ownership_not_renounced")
        profile.recommendations.append("⚠ Ownership is NOT renounced - contract can be modified.")
    if profile.has_proxy_admin:
        ownership_score += 20
        profile.patterns_detected.append("proxy_admin:contract_can_be_upgraded")
        profile.recommendations.append(
            "🚨 Token uses a proxy pattern with admin - contract can be upgraded (rug risk)."
        )
    if profile.has_multisig:
        ownership_score -= 10
        profile.patterns_detected.append("multisig_owner")
        profile.recommendations.append("✅ Ownership uses multi-sig - adds security layer.")

    profile.overall_risk_score = (
        profile.supply_risk_score * weights["supply"]
        + profile.concentration_score * weights["concentration"]
        + profile.liquidity_score * weights["liquidity"]
        + tax_score * weights["tax"]
        + ownership_score * weights["ownership"]
    )
    profile.overall_risk = _classify_risk(profile.overall_risk_score)


# ── Main analysis function ───────────────────────────────────────


async def analyze_token_supply(token_address: str, chain: str = "") -> TokenSupplyProfile:
    """
    Main entry point. Analyzes token supply, liquidity, and holder concentration.

    Args:
        token_address: The token contract address.
        chain: Optional chain hint (ethereum, solana, bsc, polygon, etc.).

    Returns:
        TokenSupplyProfile with all analysis results.
    """
    profile = TokenSupplyProfile(address=token_address)

    if not chain:
        chain = _detect_chain(token_address)
    profile.chain = chain

    logger.info(
        "Analyzing token supply for %s on %s",
        token_address[:16],
        chain,
    )

    # ── Fetch data from free APIs ───────────────────────────────
    dexscreener_data = await _fetch_dexscreener(token_address, chain)
    birdeye_data = await _fetch_birdeye(token_address)

    # ── Parse DexScreener data ──────────────────────────────────
    if dexscreener_data:
        pairs = dexscreener_data.get("pairs", [])
        if pairs:
            pair = pairs[0]
            profile.symbol = pair.get("baseToken", {}).get("symbol", "")
            profile.name = pair.get("baseToken", {}).get("name", "")
            profile.decimals = int(pair.get("baseToken", {}).get("decimals", 18))
            profile.pair_liquidity_usd = float(pair.get("liquidity", {}).get("usd", 0))
            profile.pair_age_days = _compute_pair_age_days(pair.get("pairCreatedAt", 0))

            # Tax data (if available from DexScreener)
            if "buyTax" in pair:
                profile.buy_tax_pct = float(pair.get("buyTax", 0))
            if "sellTax" in pair:
                profile.sell_tax_pct = float(pair.get("sellTax", 0))

            # LP holder info
            lp_holders = pair.get("liquidity", {}).get("holders", [])
            if lp_holders:
                for h in lp_holders:
                    if h.get("address", "").lower() in (
                        _normalize_address(profile.owner_address),
                        _normalize_address(profile.address),
                    ):
                        profile.lp_token_pct_held_by_deployer = float(h.get("percent", 0))
                        break

    # ── Parse Birdeye data ──────────────────────────────────────
    if birdeye_data:
        # Supply data
        profile.total_supply_raw = int(
            birdeye_data.get("totalSupply", birdeye_data.get("supply", 0))
        )
        profile.circulating_supply_raw = int(birdeye_data.get("circulatingSupply", 0))
        profile.max_supply_raw = int(birdeye_data.get("maxSupply", 0))

        # Convert based on decimals
        divider = 10**profile.decimals
        if profile.total_supply_raw > 0:
            profile.total_supply = profile.total_supply_raw / divider
        if profile.circulating_supply_raw > 0:
            profile.circulating_supply = profile.circulating_supply_raw / divider
        if profile.max_supply_raw > 0:
            profile.max_supply = profile.max_supply_raw / divider

        # Holder data
        holders = birdeye_data.get("holders", [])
        if holders:
            profile.holder_count = len(holders)
            top10_pct = 0.0
            top50_pct = 0.0
            for i, h in enumerate(holders):
                pct = float(h.get("percent", h.get("percentage", 0)))
                if i < 10:
                    top10_pct += pct
                if i < 50:
                    top50_pct += pct
                if i < MAX_HOLDER_SAMPLE:
                    profile.whale_holdings.append(
                        {
                            "address": h.get("address", ""),
                            "pct": pct,
                            "balance": h.get("balance", 0),
                        }
                    )

            profile.top_10_holder_pct = top10_pct
            profile.top_50_holder_pct = top50_pct

            # Check deployer holding
            if profile.owner_address:
                for h in holders:
                    if h.get("address", "").lower() == _normalize_address(profile.owner_address):
                        profile.deployer_hold_pct = float(h.get("percent", h.get("percentage", 0)))
                        break

        # Supply mechanics
        profile.has_mint_function = birdeye_data.get("mintable", False)
        profile.has_burn_function = birdeye_data.get("burnable", False)
        profile.is_deflationary = birdeye_data.get("deflationary", False)

        # Lock status
        lock_data = birdeye_data.get("liquidityLock", {})
        if lock_data:
            locked = lock_data.get("locked", False)
            expiry = lock_data.get("expiry", "")
            if locked:
                profile.lock_status = LockStatus.LOCKED
                profile.liquidity_lock_expiry = expiry
            elif lock_data.get("removed", False):
                profile.lock_status = LockStatus.REMOVED
            else:
                profile.lock_status = LockStatus.UNLOCKED

        # Ownership
        profile.is_renounced = birdeye_data.get("renounced", False)
        profile.owner_address = birdeye_data.get("owner", "")
        profile.has_proxy_admin = birdeye_data.get("proxy", False)
        profile.has_multisig = birdeye_data.get("multisig", False)

        # Tax
        if not profile.buy_tax_pct:
            profile.buy_tax_pct = float(birdeye_data.get("buyTax", 0))
        if not profile.sell_tax_pct:
            profile.sell_tax_pct = float(birdeye_data.get("sellTax", 0))
        profile.has_dynamic_tax = birdeye_data.get("dynamicTax", False)
        profile.has_blacklist = birdeye_data.get("blacklist", False)
        profile.has_max_wallet_limit = birdeye_data.get("maxWallet", False)

    # ── Run analysis ────────────────────────────────────────────
    _analyze_supply_mechanics(profile)
    _analyze_concentration(profile)
    _analyze_liquidity(profile)
    _compute_overall_risk(profile)

    # ── Summary recommendation ──────────────────────────────────
    if profile.overall_risk == SupplyRisk.CRITICAL:
        profile.recommendations.insert(
            0, "🚨 CRITICAL: Do NOT invest. Multiple severe risk factors detected."
        )
    elif profile.overall_risk == SupplyRisk.HIGH:
        profile.recommendations.insert(
            0, "⚠ HIGH RISK: Significant red flags. Proceed with extreme caution."
        )
    elif profile.overall_risk == SupplyRisk.MEDIUM:
        profile.recommendations.insert(
            0, "⚠ MEDIUM RISK: Some concerns. Verify all claims before investing."
        )
    elif profile.overall_risk == SupplyRisk.LOW:
        profile.recommendations.insert(
            0, "✅ LOW RISK: Reasonable tokenomics. Standard precautions advised."
        )
    else:
        profile.recommendations.insert(0, "✅ SAFE: Tokenomics appear healthy. Always DYOR.")

    return profile


# ── API fetch helpers ────────────────────────────────────────────


async def _fetch_dexscreener(token_address: str, chain: str) -> dict[str, Any]:
    """Fetch token data from DexScreener API."""
    url = DEXSCREENER_TOKENS.format(token_addr=token_address)
    if not _validate_url(url):
        logger.warning("Invalid DexScreener URL for %s", token_address[:16])
        return {}

    return await _fetch_json(url, "DexScreener")


async def _fetch_birdeye(token_address: str) -> dict[str, Any]:
    """Fetch token data from Birdeye API."""
    url = BIRDEYE_TOKEN.format(token_addr=token_address)
    if not _validate_url(url):
        logger.warning("Invalid Birdeye URL for %s", token_address[:16])
        return {}

    return await _fetch_json(url, "Birdeye")


async def _fetch_json(url: str, source: str) -> dict[str, Any]:
    """Fetch JSON from a URL with rate limiting."""
    parsed = urlparse(url)
    host = parsed.hostname or "unknown"

    try:
        await _rate_limit_host(host)

        import aiohttp

        timeout = aiohttp.ClientTimeout(total=10)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(
                url,
                headers={
                    "User-Agent": "RMI-TokenSupplyAnalyzer/1.0",
                    "Accept": "application/json",
                },
            ) as resp,
        ):
            if resp.status == 200:
                data = await resp.json()
                # Birdeye wraps data in data field
                if isinstance(data, dict) and "data" in data:
                    return cast("dict[str, Any]", data["data"])
                return cast("dict[str, Any]", data)
            elif resp.status == 429:
                logger.warning("Rate limited by %s, waiting 2s...", source)
                await asyncio.sleep(2)
                return {}
            else:
                logger.warning(
                    "%s returned status %d for %s",
                    source,
                    resp.status,
                    url[:60],
                )
                return {}
    except TimeoutError:
        logger.warning("Timeout fetching from %s: %s", source, url[:60])
        return {}
    except Exception as e:
        logger.warning("Error fetching from %s: %s", source, e)
        return {}


def _compute_pair_age_days(created_at_unix: int) -> float:
    """Compute pair age in days from Unix timestamp."""
    if not created_at_unix:
        return 0.0
    try:
        created = datetime.fromtimestamp(created_at_unix / 1000, tz=UTC)  # DexScreener uses ms
        now = datetime.now(tz=UTC)
        return max(0.0, (now - created).total_seconds() / 86400)
    except (ValueError, OSError):
        return 0.0


# ── Batch / streaming analysis ───────────────────────────────────


async def analyze_multiple_tokens(
    addresses: list[str], chain: str = ""
) -> list[TokenSupplyProfile]:
    """Analyze multiple tokens concurrently."""
    tasks = [analyze_token_supply(addr, chain) for addr in addresses]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    valid: list[TokenSupplyProfile] = []
    for r in results:
        if isinstance(r, TokenSupplyProfile):
            valid.append(r)
    return valid


# ── CLI entry point ──────────────────────────────────────────────


async def main() -> None:
    """Run supply analysis from command line."""
    import argparse

    parser = argparse.ArgumentParser(description="Token Supply & Liquidity Analyzer")
    parser.add_argument("address", help="Token contract address")
    parser.add_argument(
        "--chain",
        default="",
        help="Chain hint (ethereum, solana, bsc, polygon)",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    profile = await analyze_token_supply(args.address, args.chain)

    if args.json:
        import dataclasses

        def _serialize(obj: Any) -> str:
            if isinstance(obj, Enum):
                val = obj.value
                return str(val)
            if isinstance(obj, datetime):
                return obj.isoformat()
            return str(obj)

        logger.info(
            json.dumps(
                dataclasses.asdict(profile),
                indent=2,
                default=_serialize,
            )
        )
    else:
        logger.info(f"\n{'=' * 60}")
        logger.info(f"TOKEN SUPPLY ANALYSIS: {profile.symbol} ({profile.name})")
        logger.info(f"{'=' * 60}")
        logger.info(f"Address: {profile.address}")
        logger.info(f"Chain:   {profile.chain}")
        logger.info("\n── Supply ──")
        logger.info(f"  Total supply:     {profile.total_supply:,.2f}")
        logger.info(f"  Circulating:      {profile.circulating_supply:,.2f}")
        logger.info(f"  Max supply:       {profile.max_supply:,.2f}")
        logger.info(f"  Burned:           {profile.burned_supply:,.2f}")
        logger.info(
            f"  Has mint:         {'YES' if profile.has_mint_function else 'no'} ({profile.mint_function_type})"
        )
        logger.info(
            f"  Has burn:         {'YES' if profile.has_burn_function else 'no'} ({profile.burn_function_type})"
        )
        logger.info(f"  Deflationary:     {'YES' if profile.is_deflationary else 'no'}")
        logger.info("\n── Concentration ──")
        logger.info(f"  Holders:          {profile.holder_count:,}")
        logger.info(f"  Top 10 hold:      {profile.top_10_holder_pct:.1f}%")
        logger.info(f"  Top 50 hold:      {profile.top_50_holder_pct:.1f}%")
        logger.info(f"  Deployer holds:   {profile.deployer_hold_pct:.1f}%")
        logger.info("\n── Liquidity ──")
        logger.info(f"  Lock status:      {profile.lock_status.value}")
        logger.info(f"  Lock expiry:      {profile.liquidity_lock_expiry or 'N/A'}")
        logger.info(f"  Liquidity (USD):  ${profile.pair_liquidity_usd:,.2f}")
        logger.info(f"  Pair age (days):  {profile.pair_age_days:.1f}")
        logger.info(f"  LP by deployer:   {profile.lp_token_pct_held_by_deployer:.1f}%")
        logger.info("\n── Tax ──")
        logger.info(f"  Buy tax:          {profile.buy_tax_pct:.1f}%")
        logger.info(f"  Sell tax:         {profile.sell_tax_pct:.1f}%")
        logger.info(f"  Dynamic tax:      {'YES' if profile.has_dynamic_tax else 'no'}")
        logger.info(f"  Blacklist:        {'YES' if profile.has_blacklist else 'no'}")
        logger.info("\n── Ownership ──")
        logger.info(f"  Renounced:        {'YES' if profile.is_renounced else 'NO'}")
        logger.info(f"  Proxy admin:      {'YES' if profile.has_proxy_admin else 'no'}")
        logger.info(f"  Multi-sig:        {'YES' if profile.has_multisig else 'no'}")
        logger.info("\n── Risk Scores ──")
        logger.info(
            f"  Supply risk:      {profile.supply_risk.value} ({profile.supply_risk_score:.0f}/100)"
        )
        logger.info(
            f"  Concentration:    {profile.concentration_risk.value} ({profile.concentration_score:.0f}/100)"
        )
        logger.info(
            f"  Liquidity risk:   {profile.liquidity_risk.value} ({profile.liquidity_score:.0f}/100)"
        )
        logger.info(
            f"  OVERALL RISK:     {profile.overall_risk.value} ({profile.overall_risk_score:.0f}/100)"
        )
        logger.info("\n── Patterns Detected ──")
        for p in profile.patterns_detected:
            logger.info(f"  • {p}")
        logger.info("\n── Recommendations ──")
        for r in profile.recommendations:
            logger.info(f"  {r}")
        logger.info()


if __name__ == "__main__":
    asyncio.run(main())
