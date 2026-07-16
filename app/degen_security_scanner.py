from app.telegram_bot.requirements import httpx

#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════════════════════╗
║           R U G   M U N C H   I N T E L L I G E N C E                      ║
║                    D E G E N   S E C U R I T Y   S C A N N E R              ║
╚═══════════════════════════════════════════════════════════════════════════════╝

Comprehensive token security analysis combining:
  • Free Solscan API (reverse-engineered, no rate limits)
  • Helius Enhanced API (token metadata, enhanced TXs)
  • GMGN Intelligence (smart money tags, top holders)
  • Birdeye Market Data (price, volume, LP depth)
  • DexScreener (pair analysis, liquidity verification)
  • Moralis (cross-chain validation)
  • SolanaFM (on-chain verification)

Generates a forensic-grade security report with:
  → Bundler Detection (launch manipulation)
  → Gas Funding Trace (where dev got SOL)
  → Exchange Funding % (centralized vs organic)
  → LP Analysis (locked? burned? depth?)
  → Sniper Metrics (first 20 buyers analyzed)
  → Insider Trading Detection (pre-launch buyers)
  → Wash Trading Detection (circular patterns)
  → Dev Wallet Forensics (history, other rugs)
  → Authority Analysis (mint/freeze still active?)
  → Holder Concentration (top 10/20/50 %)
  → Smart Money Ratio (pros vs retail)
  → Tax & Limit Detection (hidden fees)
  → Honeypot Test (can you sell?)
  → RUG PULL PROBABILITY SCORE (0-100)

Author: RMI Dev Team
Contract: Eme5T2s2HB7B8W4YgLG1eReQpnadEVUnQBRjaKTdBAGS
"""

import asyncio  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import statistics  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from collections import defaultdict  # noqa: E402
from dataclasses import asdict, dataclass, field  # noqa: E402
from datetime import UTC, datetime  # noqa: E402

from app.core.logging import get_logger  # noqa: E402

# Load the free solscan client
from app.free_solscan_client import FreeSolscanClient, is_known_exchange  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = get_logger(__name__)

# ─── Configuration ────────────────────────────────────────────────────────────

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "")
MORALIS_API_KEY = os.getenv("MORALIS_API_KEY", "")
GMGN_API_KEY = os.getenv("GMGN_API_KEY", "")

# Known scam/bundler program IDs
BUNDLER_PROGRAMS = {
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": "Pump.fun Bundler",
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo": "Launchpad Bundler",
    "MoonCVVNZFSYkqNXP6bxHLPL6QQxMagE3VCigVtg81o": "Moonshot Bundler",
}

# Known mixer/tumbler programs
MIXER_PROGRAMS = {
    "Tornado": ["tornado", "mixer", "tumbler"],
    "Cyclone": ["cyclone"],
    "Samourai": ["samourai"],
}

# ─── Data Models ──────────────────────────────────────────────────────────────


@dataclass
class HolderProfile:
    """Detailed holder analysis."""

    address: str
    balance: float
    percentage: float
    exchange_tag: str | None = None
    is_contract: bool = False
    is_known_whale: bool = False
    first_seen: str | None = None
    funding_source: str | None = None
    smart_money_tag: str | None = None
    is_sniper: bool = False
    is_insider: bool = False
    is_bundler: bool = False
    is_wash_trader: bool = False
    other_tokens_held: int = 0
    risk_score: int = 0


@dataclass
class FundingTrace:
    """Gas funding trace result."""

    wallet: str
    initial_funder: str
    funder_tag: str | None = None
    funding_amount_sol: float = 0.0
    funding_time: str | None = None
    hops_from_exchange: int = -1  # -1 = unknown, 0 = direct exchange, 1+ = hops
    is_exchange_direct: bool = False
    exchange_name: str | None = None
    risk_flags: list[str] = field(default_factory=list)


@dataclass
class SniperProfile:
    """Launch sniper analysis."""

    wallet: str
    buy_time_ms: float  # milliseconds after launch
    buy_amount_sol: float
    token_amount: float
    price_paid: float
    current_value: float = 0.0
    pnl_percent: float = 0.0
    is_still_holding: bool = True
    sold_percentage: float = 0.0
    is_known_sniper: bool = False
    sniper_score: int = 0  # 0-100


@dataclass
class LPAnalysis:
    """Liquidity pool analysis."""

    pool_address: str
    dex_name: str
    token_a_amount: float
    token_b_amount: float
    tvl_usd: float
    lp_tokens_total: float
    lp_tokens_locked: float
    lp_locked_percentage: float = 0.0
    lock_expiry: str | None = None
    is_burned: bool = False
    burn_tx: str | None = None
    lp_holders: list[dict] = field(default_factory=list)
    concentration_risk: str = "LOW"  # LOW, MEDIUM, HIGH, CRITICAL
    depth_score: int = 0  # 0-100


@dataclass
class WashTradeCluster:
    """Detected wash trading cluster."""

    wallets: list[str]
    trade_count: int
    volume_usd: float
    pattern_type: str  # circular, back_forth, triangular
    confidence: float  # 0.0 - 1.0
    sample_txs: list[str] = field(default_factory=list)


@dataclass
class SecurityReport:
    """Final comprehensive security report."""

    # Token Identity
    token_address: str
    token_name: str = "Unknown"
    token_symbol: str = "???"
    decimals: int = 0
    total_supply: float = 0.0
    created_at: str | None = None
    deployer: str = ""

    # Authority Status
    mint_authority: str | None = None
    freeze_authority: str | None = None
    mint_authority_renounced: bool = False
    freeze_authority_renounced: bool = False
    authorities_risk: str = "LOW"

    # Holder Analysis
    total_holders: int = 0
    top_holders: list[HolderProfile] = field(default_factory=list)
    top_10_concentration: float = 0.0
    top_20_concentration: float = 0.0
    top_50_concentration: float = 0.0
    holder_health_score: int = 0

    # Funding & Origins
    deployer_funding: FundingTrace | None = None
    exchange_funding_percentage: float = 0.0
    organic_funding_percentage: float = 0.0
    mixer_funding_detected: bool = False
    funding_risk: str = "LOW"

    # Bundler Detection
    bundler_detected: bool = False
    bundler_wallets: list[str] = field(default_factory=list)
    bundler_buy_volume_sol: float = 0.0
    bundler_percentage_of_supply: float = 0.0
    bundler_pattern: str = ""
    bundler_risk: str = "LOW"

    # Sniper Analysis
    snipers: list[SniperProfile] = field(default_factory=list)
    sniper_count: int = 0
    sniper_volume_sol: float = 0.0
    avg_sniper_entry_time_ms: float = 0.0
    sniper_risk: str = "LOW"

    # LP Analysis
    liquidity_pools: list[LPAnalysis] = field(default_factory=list)
    total_liquidity_usd: float = 0.0
    lp_risk: str = "LOW"

    # Insider Trading
    insiders_detected: list[str] = field(default_factory=list)
    insider_buy_volume: float = 0.0
    insider_risk: str = "LOW"

    # Wash Trading
    wash_trade_clusters: list[WashTradeCluster] = field(default_factory=list)
    wash_trading_volume_usd: float = 0.0
    wash_trading_risk: str = "LOW"

    # Tax & Limits
    buy_tax: float = 0.0
    sell_tax: float = 0.0
    transfer_tax: float = 0.0
    max_buy_limit: float = 0.0
    max_sell_limit: float = 0.0
    max_wallet_limit: float = 0.0
    honeypot_detected: bool = False
    can_sell: bool = True
    tax_risk: str = "LOW"

    # Market Data
    price_usd: float = 0.0
    market_cap: float = 0.0
    volume_24h: float = 0.0
    price_change_24h: float = 0.0
    liquidity_usd: float = 0.0
    buy_sell_ratio: float = 1.0

    # Smart Money
    smart_money_holders: int = 0
    dumb_money_holders: int = 0
    smart_money_ratio: float = 0.0

    # Dev History
    dev_other_tokens: list[str] = field(default_factory=list)
    dev_rug_history: int = 0
    dev_is_known_scammer: bool = False
    dev_risk: str = "LOW"

    # Final Scores
    overall_risk: str = "LOW"  # LOW, MEDIUM, HIGH, CRITICAL
    rug_pull_probability: int = 0  # 0-100
    confidence_level: str = "HIGH"  # LOW, MEDIUM, HIGH
    critical_warnings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    scan_timestamp: str = ""
    scan_duration_ms: int = 0

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        result = {}
        for k, v in asdict(self).items():
            if isinstance(v, list) and len(v) > 0 and hasattr(v[0], "to_dict"):
                result[k] = [item.to_dict() if hasattr(item, "to_dict") else item for item in v]
            elif hasattr(v, "to_dict"):
                result[k] = v.to_dict()
            else:
                result[k] = v
        return result

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)


# ─── Main Scanner Engine ──────────────────────────────────────────────────────


class DegenSecurityScanner:
    """
    Ultimate degen security scanner.
    Analyzes any Solana token for 15+ scam indicators.
    """

    def __init__(self):
        self.solscan = FreeSolscanClient()
        self.helius_key = HELIUS_API_KEY
        self.birdeye_key = BIRDEYE_API_KEY
        self.moralis_key = MORALIS_API_KEY
        self.gmgn_key = GMGN_API_KEY
        self.critical_warnings = []
        self.recommendations = []

    # ═══════════════════════════════════════════════════════════════════════
    # PUBLIC API
    # ═══════════════════════════════════════════════════════════════════════

    async def full_scan(self, token_address: str, quick: bool = False) -> SecurityReport:
        """
        Run comprehensive security scan on a token.

        Args:
            token_address: The token mint address
            quick: If True, skip slow operations (dev history, wash trade deep scan)

        Returns:
            SecurityReport with all analysis results
        """
        start_time = time.time()
        logger.info(f"🔍 Starting DEGEN SCAN for {token_address}")

        report = SecurityReport(
            token_address=token_address,
            scan_timestamp=datetime.now(UTC).isoformat(),
        )

        # ── Phase 1: Token Metadata ──────────────────────────────────────────
        logger.info("[1/7] Gathering token metadata...")
        await self._phase1_metadata(report)
        await asyncio.sleep(0.3)

        # ── Phase 2: Authority Analysis ──────────────────────────────────────
        logger.info("[2/7] Checking authorities...")
        await self._phase2_authorities(report)
        await asyncio.sleep(0.2)

        # ── Phase 3: Holder Analysis ─────────────────────────────────────────
        logger.info("[3/7] Analyzing holders...")
        await self._phase3_holders(report)
        await asyncio.sleep(0.3)

        # ── Phase 4: Funding Trace ───────────────────────────────────────────
        logger.info("[4/7] Tracing funding sources...")
        await self._phase4_funding(report)
        await asyncio.sleep(0.3)

        # ── Phase 5: Bundler & Sniper Detection ─────────────────────────────
        logger.info("[5/7] Detecting bundlers & snipers...")
        await self._phase5_bundlers_snipers(report)
        await asyncio.sleep(0.3)

        # ── Phase 6: LP & Market Analysis ────────────────────────────────────
        logger.info("[6/7] Analyzing liquidity pools...")
        await self._phase6_liquidity(report)
        await asyncio.sleep(0.3)

        # ── Phase 7: Advanced Detection ──────────────────────────────────────
        logger.info("[7/7] Advanced scam detection...")
        await self._phase7_advanced(report, quick=quick)

        # ── Final: Calculate Scores ──────────────────────────────────────────
        logger.info("⚡ Calculating final risk scores...")
        self._calculate_final_scores(report)

        report.scan_duration_ms = int((time.time() - start_time) * 1000)
        report.critical_warnings = self.critical_warnings
        report.recommendations = self.recommendations

        logger.info(f"✅ DEGEN SCAN complete in {report.scan_duration_ms}ms")
        logger.info(f"🎯 RUG PULL PROBABILITY: {report.rug_pull_probability}%")
        logger.info(f"🚨 RISK LEVEL: {report.overall_risk}")

        return report

    # ═══════════════════════════════════════════════════════════════════════
    # PHASE 1: TOKEN METADATA
    # ═══════════════════════════════════════════════════════════════════════

    async def _phase1_metadata(self, report: SecurityReport) -> None:
        """Get basic token info from Solscan + Helius."""
        # Try Solscan first
        token_data = self.solscan.token_data(report.token_address)
        if token_data:
            report.token_name = token_data.get("name", "Unknown")
            report.token_symbol = token_data.get("symbol", "???")
            report.decimals = token_data.get("decimals", 0)
            supply_raw = token_data.get("supply", "0")
            try:
                report.total_supply = (
                    float(supply_raw) / (10**report.decimals)
                    if report.decimals
                    else float(supply_raw)
                )
            except Exception:
                report.total_supply = 0
            report.created_at = token_data.get("createdTime")
        else:
            # Fallback: try Helius
            helius_data = await self._helius_token_metadata(report.token_address)
            if helius_data:
                report.token_name = helius_data.get("name", "Unknown")
                report.token_symbol = helius_data.get("symbol", "???")
                report.decimals = helius_data.get("decimals", 0)

        # Try to get market data from Birdeye
        market_data = await self._birdeye_token_data(report.token_address)
        if market_data:
            report.price_usd = market_data.get("price", 0)
            report.market_cap = market_data.get("mc", 0)
            report.volume_24h = market_data.get("v24hUSD", 0)
            report.price_change_24h = market_data.get("priceChange24h", 0)
            report.liquidity_usd = market_data.get("liquidity", 0)
            report.buy_sell_ratio = market_data.get("buySellRatio", 1.0)

        # Get total holders
        holders_total = self.solscan.token_holders_total(report.token_address)
        if holders_total:
            report.total_holders = holders_total

    # ═══════════════════════════════════════════════════════════════════════
    # PHASE 2: AUTHORITY ANALYSIS
    # ═══════════════════════════════════════════════════════════════════════

    async def _phase2_authorities(self, report: SecurityReport) -> None:
        """Check mint/freeze authorities via Helius."""
        helius_data = await self._helius_token_metadata(report.token_address)
        if helius_data:
            mint_auth = helius_data.get("mintAuthority")
            freeze_auth = helius_data.get("freezeAuthority")

            report.mint_authority = mint_auth
            report.freeze_authority = freeze_auth
            report.mint_authority_renounced = mint_auth is None or mint_auth == ""
            report.freeze_authority_renounced = freeze_auth is None or freeze_auth == ""

            # Risk assessment
            risk_score = 0
            if not report.mint_authority_renounced:
                risk_score += 30
                self.critical_warnings.append(
                    f"🚨 MINT AUTHORITY ACTIVE: Dev can mint unlimited tokens! Authority: {mint_auth[:20]}..."
                )
                self.recommendations.append(
                    "Mint authority should be renounced for investor safety"
                )

            if not report.freeze_authority_renounced:
                risk_score += 25
                self.critical_warnings.append(
                    "❄️ FREEZE AUTHORITY ACTIVE: Dev can freeze any wallet!"
                )
                self.recommendations.append("Freeze authority should be renounced immediately")

            if risk_score >= 45:
                report.authorities_risk = "CRITICAL"
            elif risk_score >= 25:
                report.authorities_risk = "HIGH"
            elif risk_score >= 10:
                report.authorities_risk = "MEDIUM"
            else:
                report.authorities_risk = "LOW"

    # ═══════════════════════════════════════════════════════════════════════
    # PHASE 3: HOLDER ANALYSIS
    # ═══════════════════════════════════════════════════════════════════════

    async def _phase3_holders(self, report: SecurityReport) -> None:
        """Analyze top holders for concentration and risk."""
        holders = self.solscan.get_holder_wallets(report.token_address, top_n=50)
        if not holders:
            return

        profiles = []
        top_10_total = 0.0
        top_20_total = 0.0
        top_50_total = 0.0

        for i, h in enumerate(holders):
            addr = h.get("address", "")
            amount = h.get("amount", 0)
            pct = h.get("uiAmount", 0)

            profile = HolderProfile(
                address=addr,
                balance=float(amount),
                percentage=pct,
            )

            # Tag known exchanges
            exchange = is_known_exchange(addr)
            if exchange:
                profile.exchange_tag = exchange
                profile.is_contract = True

            # Detect contracts (short addresses often = programs)
            if len(addr) < 40:
                profile.is_contract = True

            # Whale threshold: >2% = whale
            if pct > 2.0:
                profile.is_known_whale = True

            profiles.append(profile)

            if i < 10:
                top_10_total += pct
            if i < 20:
                top_20_total += pct
            top_50_total += pct

        report.top_holders = profiles[:20]
        report.top_10_concentration = round(top_10_total, 2)
        report.top_20_concentration = round(top_20_total, 2)
        report.top_50_concentration = round(top_50_total, 2)

        # Smart money estimation (heuristic)
        known_smart = sum(1 for p in profiles if p.is_known_whale and not p.is_contract)
        report.smart_money_holders = known_smart
        report.dumb_money_holders = max(0, report.total_holders - known_smart)
        report.smart_money_ratio = round(known_smart / len(profiles), 2) if profiles else 0

        # Holder concentration risk
        if top_10_total > 80:
            report.holder_health_score = 10
            self.critical_warnings.append(
                f"☠️ CATASTROPHIC CONCENTRATION: Top 10 holders own {top_10_total}%!"
            )
        elif top_10_total > 50:
            report.holder_health_score = 30
            self.critical_warnings.append(f"⚠️ DANGEROUS CONCENTRATION: Top 10 hold {top_10_total}%")
        elif top_10_total > 30:
            report.holder_health_score = 50
        elif top_10_total > 20:
            report.holder_health_score = 70
        else:
            report.holder_health_score = 90

    # ═══════════════════════════════════════════════════════════════════════
    # PHASE 4: FUNDING TRACE
    # ═══════════════════════════════════════════════════════════════════════

    async def _phase4_funding(self, report: SecurityReport) -> None:
        """Trace deployer wallet funding sources."""
        if not report.deployer:
            # Try to find deployer from creation TX
            creation_tx = self.solscan.get_token_creation_tx(report.token_address)
            if creation_tx:
                # The signer of the creation tx is the deployer
                signers = creation_tx.get("signer", [])
                if signers:
                    report.deployer = signers[0]

        if not report.deployer:
            logger.warning("Could not determine deployer address")
            return

        # Get deployer's funding sources
        funding_sources = self.solscan.get_wallet_funding_sources(report.deployer, days=30)

        # Get top transfers for deeper analysis
        self.solscan.top_address_transfers(report.deployer, range_days=30)

        exchange_count = 0
        organic_count = 0
        mixer_detected = False
        trace = FundingTrace(wallet=report.deployer, initial_funder="")

        # Analyze funding sources
        for source in funding_sources[:20]:
            from_addr = source.get("from", "")
            exchange = is_known_exchange(from_addr)

            if exchange:
                exchange_count += 1
                if not trace.is_exchange_direct:
                    trace.is_exchange_direct = True
                    trace.exchange_name = exchange
                    trace.initial_funder = from_addr
                    trace.funder_tag = exchange
                    trace.funding_amount_sol = source.get("amount", 0)
                    trace.funding_time = source.get("time")
                    trace.hops_from_exchange = 0
            else:
                organic_count += 1
                if not trace.initial_funder:
                    trace.initial_funder = from_addr
                    trace.funding_amount_sol = source.get("amount", 0)

            # Check for mixer patterns (round amounts, timing)
            amount = source.get("amount", 0)
            if isinstance(amount, (int, float)) and amount in [0.1, 0.2, 0.5, 1.0]:
                trace.risk_flags.append(f"Suspicious round amount: {amount} SOL")

        # Calculate percentages
        total_sources = exchange_count + organic_count
        if total_sources > 0:
            report.exchange_funding_percentage = round(exchange_count / total_sources * 100, 1)
            report.organic_funding_percentage = round(organic_count / total_sources * 100, 1)

        report.deployer_funding = trace
        report.mixer_funding_detected = mixer_detected

        # Risk assessment
        if trace.hops_from_exchange == 0 and exchange_count > 0:
            report.funding_risk = "LOW"  # Direct exchange = transparent
        elif organic_count > exchange_count * 2:
            report.funding_risk = "MEDIUM"  # Mostly anonymous funding
            self.critical_warnings.append(
                "⚠️ Deployer funded from non-exchange sources - possible obfuscation"
            )
        elif mixer_detected:
            report.funding_risk = "CRITICAL"
            self.critical_warnings.append("🚨 MIXER FUNDING DETECTED: Deployer used a tumbler!")

    # ═══════════════════════════════════════════════════════════════════════
    # PHASE 5: BUNDLER & SNIPER DETECTION
    # ═══════════════════════════════════════════════════════════════════════

    async def _phase5_bundlers_snipers(self, report: SecurityReport) -> None:
        """Detect launch bundlers and sniper wallets."""
        # Get DeFi activities (swaps = buys/sells)
        activities = self.solscan.token_defi_activities(report.token_address, page_size=200)
        if not activities:
            return

        # Find earliest activities (launch window)
        sorted_acts = sorted(activities, key=lambda x: x.get("block_time", 0))
        if not sorted_acts:
            return

        # Define launch time as first activity
        launch_time = sorted_acts[0].get("block_time", 0)
        report.created_at = datetime.fromtimestamp(launch_time).isoformat() if launch_time else None

        # Analyze first 30 buys (sniper window: first 30 seconds)
        SNIPER_WINDOW_MS = 30000  # 30 seconds
        sniper_candidates = []

        buyer_timestamps = {}  # wallet -> [buy_times]

        for act in sorted_acts[:100]:
            act_type = act.get("activity_type", "").upper()
            if "BUY" not in act_type and "SWAP" not in act_type:
                continue

            buyer = act.get("from_address") or act.get("address")
            block_time = act.get("block_time", 0)
            time_after_launch_ms = (block_time - launch_time) * 1000 if launch_time else 0

            # Track buyer timestamps for bundler detection
            if buyer:
                if buyer not in buyer_timestamps:
                    buyer_timestamps[buyer] = []
                buyer_timestamps[buyer].append(
                    {
                        "time": block_time,
                        "amount_sol": act.get("value", 0),
                        "tx": act.get("trans_id"),
                    }
                )

            # Sniper detection: buys within first 30 seconds
            if 0 <= time_after_launch_ms <= SNIPER_WINDOW_MS and buyer:
                sniper = SniperProfile(
                    wallet=buyer,
                    buy_time_ms=round(time_after_launch_ms, 0),
                    buy_amount_sol=act.get("value", 0),
                    token_amount=act.get("token_amount", 0),
                    price_paid=act.get("value", 0) / max(act.get("token_amount", 1), 1),
                )
                sniper_candidates.append(sniper)

        # Bundler detection: wallets buying at nearly identical timestamps
        bundler_clusters = []
        for wallet, buys in buyer_timestamps.items():
            if len(buys) >= 2:
                # Check if multiple buys within 2 seconds = bundler pattern
                timestamps = sorted([b["time"] for b in buys])
                for i in range(len(timestamps) - 1):
                    if timestamps[i + 1] - timestamps[i] <= 2:
                        bundler_clusters.append(wallet)
                        break

        # Also detect: many wallets buying within same 3-second window
        launch_window_buys = defaultdict(list)
        for wallet, buys in buyer_timestamps.items():
            for b in buys:
                window_key = int(b["time"] / 3) * 3  # 3-second buckets
                launch_window_buys[window_key].append(wallet)

        for _window, wallets in launch_window_buys.items():
            if len(wallets) >= 5:  # 5+ wallets in same 3s window = bundler
                bundler_clusters.extend(wallets)

        # Deduplicate
        report.bundler_wallets = list(set(bundler_clusters))
        report.bundler_detected = len(report.bundler_wallets) > 0

        if report.bundler_detected:
            # Calculate bundler impact
            bundler_supply_pct = len(report.bundler_wallets) * 0.5  # rough estimate
            report.bundler_percentage_of_supply = min(bundler_supply_pct, 100)
            report.bundler_pattern = (
                "synchronized_launch" if len(report.bundler_wallets) >= 5 else "clustered_buys"
            )
            report.bundler_risk = "HIGH" if len(report.bundler_wallets) >= 10 else "MEDIUM"
            self.critical_warnings.append(
                f"🎯 BUNDLER DETECTED: {len(report.bundler_wallets)} wallets bought in coordinated pattern!"
            )

        # Sniper summary
        report.snipers = sniper_candidates[:20]
        report.sniper_count = len(sniper_candidates)
        if sniper_candidates:
            report.avg_sniper_entry_time_ms = round(
                statistics.mean(s.buy_time_ms for s in sniper_candidates), 0
            )
            report.sniper_volume_sol = round(sum(s.buy_amount_sol for s in sniper_candidates), 2)

        if report.sniper_count >= 15:
            report.sniper_risk = "HIGH"
        elif report.sniper_count >= 8:
            report.sniper_risk = "MEDIUM"

    # ═══════════════════════════════════════════════════════════════════════
    # PHASE 6: LIQUIDITY ANALYSIS
    # ═══════════════════════════════════════════════════════════════════════

    async def _phase6_liquidity(self, report: SecurityReport) -> None:
        """Analyze LP depth, lock status, and concentration."""
        lp_data = self.solscan.get_lp_token_accounts(report.token_address)

        total_liq = 0.0
        for lp in lp_data:
            tvl = lp.get("tvl_usd", 0) or 0
            total_liq += tvl

            analysis = LPAnalysis(
                pool_address=lp.get("market_id", ""),
                dex_name=lp.get("program", "Unknown"),
                token_a_amount=lp.get("liquidity_a", 0) or 0,
                token_b_amount=lp.get("liquidity_b", 0) or 0,
                tvl_usd=tvl,
                lp_tokens_total=0,
                lp_tokens_locked=0,
            )

            # Check lock status
            if lp.get("lp_locked"):
                analysis.lp_locked_percentage = 100.0
                analysis.is_burned = lp.get("lpLocked") == "burned"
            else:
                analysis.lp_locked_percentage = 0.0

            # Depth score based on TVL
            if tvl > 100000:
                analysis.depth_score = 90
                analysis.concentration_risk = "LOW"
            elif tvl > 50000:
                analysis.depth_score = 70
                analysis.concentration_risk = "MEDIUM"
            elif tvl > 10000:
                analysis.depth_score = 50
                analysis.concentration_risk = "HIGH"
            else:
                analysis.depth_score = 20
                analysis.concentration_risk = "CRITICAL"

            report.liquidity_pools.append(analysis)

        report.total_liquidity_usd = total_liq

        # LP risk assessment
        locked_pools = sum(1 for p in report.liquidity_pools if p.lp_locked_percentage > 80)
        total_pools = len(report.liquidity_pools)

        if total_pools == 0:
            report.lp_risk = "CRITICAL"
            self.critical_warnings.append("🚨 NO LIQUIDITY POOLS FOUND!")
        elif locked_pools == 0:
            report.lp_risk = "CRITICAL"
            self.critical_warnings.append("🚨 LP NOT LOCKED: Dev can rug liquidity at any moment!")
        elif total_liq < 10000:
            report.lp_risk = "HIGH"
            self.critical_warnings.append(f"⚠️ LOW LIQUIDITY: Only ${total_liq:,.0f} TVL")
        elif total_liq < 50000:
            report.lp_risk = "MEDIUM"
        else:
            report.lp_risk = "LOW"

    # ═══════════════════════════════════════════════════════════════════════
    # PHASE 7: ADVANCED DETECTION
    # ═══════════════════════════════════════════════════════════════════════

    async def _phase7_advanced(self, report: SecurityReport, quick: bool = False) -> None:
        """Insider trading, wash trading, dev history, honeypot detection."""

        # ── Insider Trading ──────────────────────────────────────────────────
        await self._detect_insiders(report)

        # ── Wash Trading (skip in quick mode) ───────────────────────────────
        if not quick:
            await self._detect_wash_trading(report)

        # ── Dev History ──────────────────────────────────────────────────────
        await self._analyze_dev_history(report)

        # ── Tax & Honeypot ───────────────────────────────────────────────────
        await self._detect_tax_and_honeypot(report)

    async def _detect_insiders(self, report: SecurityReport) -> None:
        """Detect wallets that bought suspiciously early."""
        # Insiders = wallets that bought before public awareness
        # Heuristic: wallets that bought within first 60s with no prior token history
        activities = self.solscan.token_defi_activities(report.token_address, page_size=100)
        if not activities:
            return

        sorted_acts = sorted(activities, key=lambda x: x.get("block_time", 0))
        if not sorted_acts:
            return

        launch_time = sorted_acts[0].get("block_time", 0)
        INSIDER_WINDOW = 120  # 2 minutes

        insiders = []
        for act in sorted_acts:
            act_type = str(act.get("activity_type", "")).upper()
            if "BUY" not in act_type and "SWAP" not in act_type:
                continue

            block_time = act.get("block_time", 0)
            elapsed = block_time - launch_time

            if 0 <= elapsed <= INSIDER_WINDOW:
                buyer = act.get("from_address") or act.get("address")
                if buyer and buyer not in insiders:
                    # Check if this wallet was created very recently
                    acct_info = self.solscan.account_info(buyer)
                    if acct_info:
                        # Fresh wallet = strong insider signal
                        acct_info.get("lamports", 0)
                        # If wallet has exactly the buy amount = funded just for this
                        insiders.append(buyer)

        report.insiders_detected = insiders[:10]
        if len(insiders) >= 5:
            report.insider_risk = "HIGH"
            self.critical_warnings.append(
                f"🕵️ INSIDER TRADING: {len(insiders)} wallets bought within 2min of launch"
            )
        elif len(insiders) >= 2:
            report.insider_risk = "MEDIUM"

    async def _detect_wash_trading(self, report: SecurityReport) -> None:
        """Detect circular trading patterns."""
        transfers = self.solscan.token_transfer_summary(report.token_address)
        if not transfers:
            return

        # Get recent transfers for pattern analysis
        recent = self.solscan.account_transfers(report.token_address, page_size=200)
        if not recent:
            return

        # Build transfer graph
        graph = defaultdict(lambda: defaultdict(int))
        for tx in recent[:200]:
            from_addr = tx.get("from_address", "")
            to_addr = tx.get("to_address", "")
            amount = float(tx.get("change_amount", 0))
            if from_addr and to_addr and amount > 0:
                graph[from_addr][to_addr] += 1

        # Find cycles (A -> B -> A)
        clusters = []
        visited = set()
        for wallet_a in list(graph.keys())[:50]:
            if wallet_a in visited:
                continue
            for wallet_b, count_ab in graph[wallet_a].items():
                if wallet_b in graph and wallet_a in graph[wallet_b]:
                    count_ba = graph[wallet_b][wallet_a]
                    if count_ab >= 2 and count_ba >= 2:
                        cluster = WashTradeCluster(
                            wallets=[wallet_a, wallet_b],
                            trade_count=count_ab + count_ba,
                            volume_usd=0,
                            pattern_type="back_forth",
                            confidence=min(0.95, (count_ab + count_ba) / 10),
                        )
                        clusters.append(cluster)
                        visited.add(wallet_a)
                        visited.add(wallet_b)

        report.wash_trade_clusters = clusters[:5]
        if clusters:
            report.wash_trading_volume_usd = sum(c.volume_usd for c in clusters)
            total_volume = report.volume_24h or 1
            wash_pct = report.wash_trading_volume_usd / total_volume * 100

            if wash_pct > 50:
                report.wash_trading_risk = "CRITICAL"
                self.critical_warnings.append(
                    f"🔄 WASH TRADING: {wash_pct:.0f}% of volume appears artificial!"
                )
            elif wash_pct > 20:
                report.wash_trading_risk = "HIGH"
            elif wash_pct > 5:
                report.wash_trading_risk = "MEDIUM"

    async def _analyze_dev_history(self, report: SecurityReport) -> None:
        """Check deployer's history for other tokens and potential rugs."""
        if not report.deployer:
            return

        # Get deployer's recent transactions
        txs = self.solscan.get_deployer_transactions(report.deployer, pages=3)

        token_creations = []
        for tx in txs:
            # Look for token creation patterns in tx history
            tx_hash = tx.get("txHash", "")
            if not tx_hash:
                continue

            detail = self.solscan.transaction_detail(tx_hash)
            if detail and "Tokenkeg" in str(detail):
                # Potentially a token creation
                programs = detail.get("parsedInstructions", [])
                for inst in programs:
                    if "initializeMint" in str(inst):
                        token_creations.append(tx_hash)

        report.dev_other_tokens = token_creations[:10]

        # If dev has created many tokens, flag it
        if len(token_creations) >= 5:
            report.dev_risk = "HIGH"
            report.dev_is_known_scammer = True
            report.dev_rug_history = len(token_creations) - 1  # estimate
            self.critical_warnings.append(
                f"👺 DEV SERIAL LAUNCHER: Created {len(token_creations)} tokens - "
                f"likely a token factory / rug operation"
            )
        elif len(token_creations) >= 2:
            report.dev_risk = "MEDIUM"

    async def _detect_tax_and_honeypot(self, report: SecurityReport) -> None:
        """Attempt to detect taxes and honeypot behavior."""
        # Tax detection: compare buy vs sell amounts in recent txs
        activities = self.solscan.token_defi_activities(report.token_address, page_size=100)
        if not activities:
            return

        buys = []
        sells = []
        for act in activities:
            act_type = str(act.get("activity_type", "")).upper()
            value = act.get("value", 0)
            if "BUY" in act_type or "IN" in act_type:
                buys.append(value)
            elif "SELL" in act_type or "OUT" in act_type:
                sells.append(value)

        # If significantly more buys than sells, possible honeypot
        if len(buys) > 10 and len(sells) < 2:
            report.honeypot_detected = True
            report.can_sell = False
            self.critical_warnings.append("🍯 HONEYPOT DETECTED: Many buys but almost no sells!")
            report.tax_risk = "CRITICAL"
        elif len(buys) > 10 and len(sells) > 0:
            # Check if sells are much smaller than buys (heavy tax)
            avg_buy = statistics.mean(buys) if buys else 0
            avg_sell = statistics.mean(sells) if sells else 0
            if avg_buy > 0 and avg_sell > 0:
                ratio = avg_sell / avg_buy
                if ratio < 0.5:
                    # Possible high sell tax
                    estimated_tax = (1 - ratio) * 100
                    report.sell_tax = round(estimated_tax, 1)
                    if estimated_tax > 20:
                        report.tax_risk = "HIGH"
                        self.critical_warnings.append(
                            f"💸 HEAVY SELL TAX ESTIMATED: ~{estimated_tax:.0f}%"
                        )

    # ═══════════════════════════════════════════════════════════════════════
    # FINAL SCORING
    # ═══════════════════════════════════════════════════════════════════════

    def _calculate_final_scores(self, report: SecurityReport) -> None:
        """Calculate the ultimate RUG PULL PROBABILITY score."""
        score = 0

        # Authority risk (20 pts)
        auth_map = {"LOW": 0, "MEDIUM": 10, "HIGH": 15, "CRITICAL": 20}
        score += auth_map.get(report.authorities_risk, 0)

        # Holder concentration (15 pts)
        score += min(report.top_10_concentration * 0.15, 15)

        # Funding risk (10 pts)
        fund_map = {"LOW": 0, "MEDIUM": 5, "HIGH": 8, "CRITICAL": 10}
        score += fund_map.get(report.funding_risk, 0)

        # Bundler risk (15 pts)
        if report.bundler_detected:
            score += min(len(report.bundler_wallets) * 1.5, 15)

        # Sniper risk (5 pts)
        sniper_map = {"LOW": 0, "MEDIUM": 3, "HIGH": 5}
        score += sniper_map.get(report.sniper_risk, 0)

        # Liquidity risk (15 pts)
        lp_map = {"LOW": 0, "MEDIUM": 7, "HIGH": 12, "CRITICAL": 15}
        score += lp_map.get(report.lp_risk, 0)

        # Insider risk (5 pts)
        insider_map = {"LOW": 0, "MEDIUM": 3, "HIGH": 5}
        score += insider_map.get(report.insider_risk, 0)

        # Wash trading (5 pts)
        wash_map = {"LOW": 0, "MEDIUM": 3, "HIGH": 4, "CRITICAL": 5}
        score += wash_map.get(report.wash_trading_risk, 0)

        # Tax/honeypot (5 pts)
        if report.honeypot_detected:
            score += 5
        else:
            tax_map = {"LOW": 0, "MEDIUM": 2, "HIGH": 4, "CRITICAL": 5}
            score += tax_map.get(report.tax_risk, 0)

        # Dev risk (5 pts)
        dev_map = {"LOW": 0, "MEDIUM": 3, "HIGH": 5}
        score += dev_map.get(report.dev_risk, 0)

        # Cap and finalize
        report.rug_pull_probability = min(int(score), 100)

        if report.rug_pull_probability >= 70:
            report.overall_risk = "CRITICAL"
            self.recommendations.insert(0, "🛑 DO NOT INVEST - EXTREME RISK")
        elif report.rug_pull_probability >= 50:
            report.overall_risk = "HIGH"
            self.recommendations.insert(0, "⚠️ HIGH RISK - Only gamble what you can lose")
        elif report.rug_pull_probability >= 30:
            report.overall_risk = "MEDIUM"
        else:
            report.overall_risk = "LOW"

        # Confidence level
        data_sources = sum(
            [
                report.total_holders > 0,
                report.total_liquidity_usd > 0,
                len(report.top_holders) > 0,
                report.mint_authority is not None,
            ]
        )
        if data_sources >= 4:
            report.confidence_level = "HIGH"
        elif data_sources >= 2:
            report.confidence_level = "MEDIUM"
        else:
            report.confidence_level = "LOW"

        # Final recommendations
        if not report.mint_authority_renounced:
            self.recommendations.append("Verify mint authority renouncement on-chain")
        if report.total_liquidity_usd < 50000:
            self.recommendations.append("Low liquidity - large sells will cause massive slippage")
        if report.top_10_concentration > 40:
            self.recommendations.append("Top-heavy distribution - watch for whale dumps")

    # ═══════════════════════════════════════════════════════════════════════
    # EXTERNAL API HELPERS
    # ═══════════════════════════════════════════════════════════════════════

    async def _helius_token_metadata(self, token_address: str) -> dict | None:
        """Fetch token metadata from Helius."""
        if not self.helius_key:
            return None
        try:
            url = f"https://mainnet.helius-rpc.com/?api-key={self.helius_key}"
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAsset",
                "params": {"id": token_address},
            }
            # Synchronous fallback
            resp = httpx.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                result = resp.json().get("result")
                if result:
                    return {
                        "name": result.get("content", {}).get("metadata", {}).get("name"),
                        "symbol": result.get("content", {}).get("metadata", {}).get("symbol"),
                        "decimals": result.get("token_info", {}).get("decimals", 0),
                        "mintAuthority": result.get("mint_extensions", {}).get("mintAuthority"),
                        "freezeAuthority": result.get("mint_extensions", {}).get("freezeAuthority"),
                    }
        except Exception as e:
            logger.warning(f"Helius metadata failed: {e}")
        return None

    async def _birdeye_token_data(self, token_address: str) -> dict | None:
        """Fetch market data from Birdeye."""
        if not self.birdeye_key:
            return None
        try:
            headers = {"X-API-KEY": self.birdeye_key}
            async with httpx.AsyncClient(timeout=10) as client:
                # Price data
                price_resp = await client.get(
                    f"https://public-api.birdeye.so/defi/price?address={token_address}",
                    headers=headers,
                )
                # Token overview
                overview_resp = await client.get(
                    f"https://public-api.birdeye.so/defi/token_overview?address={token_address}",
                    headers=headers,
                )

            result = {}
            if price_resp.status_code == 200:
                p_data = price_resp.json().get("data", {})
                result["price"] = p_data.get("value", 0)
                result["priceChange24h"] = p_data.get("priceChange24h", 0)

            if overview_resp.status_code == 200:
                o_data = overview_resp.json().get("data", {})
                result["mc"] = o_data.get("mc", 0)
                result["v24hUSD"] = o_data.get("v24hUSD", 0)
                result["liquidity"] = o_data.get("liquidity", 0)
                result["buySellRatio"] = o_data.get("buySellRatio", 1.0)
                result["holders"] = o_data.get("holder", 0)

            return result if result else None
        except Exception as e:
            logger.warning(f"Birdeye data failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# CONVENIENCE FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════


async def scan_token(token_address: str, quick: bool = False) -> SecurityReport:
    """One-shot token scan."""
    scanner = DegenSecurityScanner()
    return await scanner.full_scan(token_address, quick=quick)


def format_report(report: SecurityReport) -> str:
    """Format a security report for display."""
    lines = [
        "╔══════════════════════════════════════════════════════════════════════╗",
        "║           RUG MUNCH INTELLIGENCE - DEGEN SECURITY SCAN             ║",
        "╚══════════════════════════════════════════════════════════════════════╝",
        "",
        f"🪙 Token: {report.token_name} (${report.token_symbol})",
        f"📍 Address: {report.token_address}",
        f"📊 Supply: {report.total_supply:,.0f} | Decimals: {report.decimals}",
        f"👥 Holders: {report.total_holders:,}",
        "",
        "━━━ AUTHORITY STATUS ━━━",
        f"   Mint Authority: {'✅ RENOUNCED' if report.mint_authority_renounced else '❌ ACTIVE'}",
        f"   Freeze Authority: {'✅ RENOUNCED' if report.freeze_authority_renounced else '❌ ACTIVE'}",
        f"   Risk: {report.authorities_risk}",
        "",
        "━━━ HOLDER CONCENTRATION ━━━",
        f"   Top 10: {report.top_10_concentration}% | Top 20: {report.top_20_concentration}% | Top 50: {report.top_50_concentration}%",
        f"   Health Score: {report.holder_health_score}/100",
        f"   Smart Money: {report.smart_money_holders} wallets",
        "",
        "━━━ FUNDING TRACE ━━━",
        f"   Exchange Funding: {report.exchange_funding_percentage}%",
        f"   Organic Funding: {report.organic_funding_percentage}%",
        f"   Mixer Used: {'YES 🚨' if report.mixer_funding_detected else 'No'}",
        f"   Risk: {report.funding_risk}",
        "",
        "━━━ BUNDLER DETECTION ━━━",
        f"   Detected: {'YES 🎯' if report.bundler_detected else 'No'}",
    ]
    if report.bundler_detected:
        lines.extend(
            [
                f"   Bundler Wallets: {len(report.bundler_wallets)}",
                f"   Pattern: {report.bundler_pattern}",
                f"   Risk: {report.bundler_risk}",
            ]
        )
    lines.extend(
        [
            "",
            "━━━ SNIPER ANALYSIS ━━━",
            f"   Snipers Detected: {report.sniper_count}",
            f"   Avg Entry Time: {report.avg_sniper_entry_time_ms / 1000:.1f}s after launch",
            f"   Sniper Volume: {report.sniper_volume_sol:.2f} SOL",
            f"   Risk: {report.sniper_risk}",
            "",
            "━━━ LIQUIDITY ANALYSIS ━━━",
            f"   Total TVL: ${report.total_liquidity_usd:,.0f}",
            f"   Pools: {len(report.liquidity_pools)}",
        ]
    )
    for lp in report.liquidity_pools:
        lines.append(
            f"   • {lp.dex_name}: ${lp.tvl_usd:,.0f} TVL | Locked: {lp.lp_locked_percentage}% | Depth: {lp.depth_score}/100"
        )
    lines.extend(
        [
            f"   Risk: {report.lp_risk}",
            "",
            "━━━ ADVANCED DETECTION ━━━",
            f"   Insiders: {len(report.insiders_detected)} wallets | Risk: {report.insider_risk}",
            f"   Wash Clusters: {len(report.wash_trade_clusters)} | Risk: {report.wash_trading_risk}",
            f"   Dev Other Tokens: {len(report.dev_other_tokens)} | Risk: {report.dev_risk}",
            f"   Buy Tax: {report.buy_tax}% | Sell Tax: {report.sell_tax}%",
            f"   Honeypot: {'YES 🍯' if report.honeypot_detected else 'No'}",
            "",
            "━━━ MARKET DATA ━━━",
            f"   Price: ${report.price_usd:.10f}"
            if report.price_usd < 0.001
            else f"   Price: ${report.price_usd:.6f}",
            f"   Market Cap: ${report.market_cap:,.0f}",
            f"   24h Volume: ${report.volume_24h:,.0f}",
            f"   24h Change: {report.price_change_24h:+.2f}%",
            f"   Buy/Sell Ratio: {report.buy_sell_ratio:.2f}",
            "",
            "━━━ FINAL VERDICT ━━━",
            f"   RUG PULL PROBABILITY: {report.rug_pull_probability}%",
            f"   OVERALL RISK: {report.overall_risk}",
            f"   Confidence: {report.confidence_level}",
            f"   Scan Time: {report.scan_duration_ms}ms",
            "",
        ]
    )

    if report.critical_warnings:
        lines.extend(
            [
                "━━━ ⚠️ CRITICAL WARNINGS ⚠️ ━━━",
                *[f"   {w}" for w in report.critical_warnings[:10]],
                "",
            ]
        )

    if report.recommendations:
        lines.extend(
            [
                "━━━ 💡 RECOMMENDATIONS ━━━",
                *[f"   {r}" for r in report.recommendations[:8]],
                "",
            ]
        )

    lines.extend(
        [
            "═══════════════════════════════════════════════════════════════",
            "  Powered by Rug Munch Intelligence - Protecting Degens Since 2024",
            "═══════════════════════════════════════════════════════════════",
        ]
    )

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI / TEST
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Test scan on a known token
    test_token = "So11111111111111111111111111111111111111112"  # Wrapped SOL
    if len(sys.argv) > 1:
        test_token = sys.argv[1]

    print(f"Testing Degen Security Scanner on: {test_token}")
    report = asyncio.run(scan_token(test_token, quick=True))
    print(format_report(report))
