"""
SENTINEL — Unified Pipeline Orchestrator
==========================================
Runs all 22 scanner modules in parallel with graceful degradation.
If any module fails, the others still return results.

Modules:
  1. HolderAnalyzer            → HHI concentration, fake diversification
  2. BundleDetector            → Bundle/sniper detection, funding chain analysis
  3. ExchangeFunderDetector    → CEX-funded wallet detection
  4. LiquidityVerifier         → Lock verification, fake locker detection
  5. DevReputationEngine        → Serial rugg detection, cross-chain portability
  6. WashTradingDetector        → Circular transfer detection, cross-DEX loops
  7. MetadataFingerprinter       → HTML structure hashing, description similarity
  8. SentimentAnalyzer          → Sentiment scoring, bot campaign detection
  9. PumpFunAnalyzer            → Bonding curve, bot detection (Solana only)
  10. HoneypotDetector          → Buy/sell simulation, transfer tax analysis
  11. ContractAuthorityScanner  → Mint/freeze/update authority, proxy detection
  12. MEVDetector               → Sandwich attacks, Jito bundles, known bot tracking
  13. FlashLoanDetector         → Flash loan attack detection, borrow-then-dump
  14. PumpDumpDetector          → Pump-and-dump lifecycle, coordinated shill, volume spikes
  15. OracleManipulationDetector → Oracle source/depth, price manipulation vulnerability
  16. GovernanceAttackDetector  → Governance concentration, timelock/quorum risk
  17. ProxyDetector             → Proxy resolution, implementation fingerprinting, upgrade risk
  18. StaticAnalyzer            → Slither static analysis, Forta alerts, SmartCheck
  19. DecompilerAnalyzer        → Heimdall decompilation, whatsABI selector extraction, rug patterns
  20. AddressLabeler            → Multi-source address labeling, scam/exchange/MEV identification
  21. GuiltAssociationAnalyzer  → Mixer/fixedfloat interaction detection, guilt score
"""

import asyncio
import logging
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from typing import Any

import httpx

from .address_labeler import AddressLabeler
from .block_zero_sniper import BlockZeroSniperAnalyzer
from .bundle_detector import BundleDetector, BundleReport
from .bytecode_similarity import BytecodeSimilarityAnalyzer
from .contract_authority import ContractAuthorityScanner
from .contract_diff import ContractDiffAnalyzer
from .decompiler_analyzer import DecompilerAnalyzer
from .dev_reputation import DevReputationEngine, DevReputationReport
from .exchange_funder import ExchangeFunderDetector, ExchangeFundReport
from .flash_loan_detector import FlashLoanDetector
from .fund_flow_visualizer import FundFlowVisualizer
from .gas_trace import GasTraceAnalyzer
from .governance_attack import GovernanceAttackDetector
from .guilt_association import (
    GuiltAssociationAnalyzer,
    GuiltAssociationReport,
)
from .holder_analyzer import HHIReport, HolderAnalyzer
from .honeypot_detector import HoneypotDetector
from .liquidity_verifier import LiquidityVerifier, LockReport
from .metadata_fingerprint import FingerprintReport, MetadataFingerprinter
from .mev_detector import MEVDetector
from .oracle_manipulation import OracleManipulationDetector
from .proxy_detector import ProxyDetector
from .pump_dump_detector import PumpDumpDetector
from .pumpfun_analyzer import PumpFunAnalyzer, PumpFunReport
from .sentiment_analyzer import SentimentAnalyzer, SentimentReport
from .sleep_cycle_scanner import SleepCycleAnalyzer
from .social_velocity import SocialVelocityAnalyzer
from .static_analyzer import StaticAnalyzer
from .wash_trading import WashTradingDetector, WashTradingReport

# Wallet Memory Bank integration
try:
    from app.wallet_memory.engine import get_wallet_engine

    _WALLET_MEMORY_AVAILABLE = True
except ImportError:
    _WALLET_MEMORY_AVAILABLE = False

logger = logging.getLogger("sentinel_pipeline")


# ─── Configuration dataclass ─────────────────────────────────────────────


@dataclass
class SentinelConfig:
    """Configuration for SENTINEL pipeline — API keys and shared clients."""

    # API keys
    helius_api_key: str = ""
    birdeye_api_key: str = ""

    # Shared httpx clients (optional — created if not provided)
    dexscreener_client: httpx.AsyncClient | None = None

    def get_dexscreener_client(self) -> httpx.AsyncClient:
        """Return a shared DexScreener httpx client (creates one if needed)."""
        if self.dexscreener_client is None:
            self.dexscreener_client = httpx.AsyncClient(timeout=15.0)
        return self.dexscreener_client


# ─── Dataclass-to-dict helper ───────────────────────────────────────


def dataclass_to_dict(obj: Any) -> Any:
    """Recursively convert dataclass instances to dicts.

    Handles:
      - Nested dataclasses
      - Optional fields (None stays None)
      - Lists/tuples of dataclasses
      - Primitive types pass through unchanged
    """
    if obj is None:
        return None

    # Dataclass instance — recurse into fields
    if hasattr(obj, "__dataclass_fields__"):
        return {f.name: dataclass_to_dict(getattr(obj, f.name)) for f in fields(obj)}

    # List / tuple — recurse per element
    if isinstance(obj, (list, tuple)):
        return [dataclass_to_dict(item) for item in obj]

    # Dict — recurse per value
    if isinstance(obj, dict):
        return {k: dataclass_to_dict(v) for k, v in obj.items()}

    # Primitive (str, int, float, bool) — return as-is
    return obj


# ─── Sentinel unified report ─────────────────────────────────────────


@dataclass
class SentinelReport:
    """Aggregated SENTINEL deep-scan report across all modules."""

    token_address: str
    chain: str
    scanned_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    # Per-module results (None if skipped/failed)
    holder_analysis: dict[str, Any] | None = None
    bundle_detection: dict[str, Any] | None = None
    exchange_funding: dict[str, Any] | None = None
    liquidity_verification: dict[str, Any] | None = None
    dev_reputation: dict[str, Any] | None = None
    wash_trading: dict[str, Any] | None = None
    metadata_fingerprint: dict[str, Any] | None = None
    sentiment: dict[str, Any] | None = None
    pumpfun_analysis: dict[str, Any] | None = None
    honeypot_detection: dict[str, Any] | None = None
    contract_authority: dict[str, Any] | None = None
    mev_detection: dict[str, Any] | None = None
    flash_loan_detection: dict[str, Any] | None = None
    pump_dump_detection: dict[str, Any] | None = None
    oracle_manipulation: dict[str, Any] | None = None
    governance_attack: dict[str, Any] | None = None
    proxy_detection: dict[str, Any] | None = None

    # Tier 3 scanners
    static_analysis: dict[str, Any] | None = None
    decompiler_analysis: dict[str, Any] | None = None
    address_labels: dict[str, Any] | None = None

    # Tier 3.5 — Wallet Memory Bank intelligence
    wallet_intel: dict[str, Any] | None = None

    # Tier 4 — Visualization
    fund_flow: dict[str, Any] | None = None
    contract_diff: dict[str, Any] | None = None

    # Guilt by Association
    guilt_association: dict[str, Any] | None = None

    # Sleep Cycle Analysis
    sleep_cycle: dict[str, Any] | None = None

    # Social Velocity
    social_velocity: dict[str, Any] | None = None

    # Bytecode Similarity
    bytecode_similarity: dict[str, Any] | None = None

    # Block Zero Sniper
    block_zero_sniper: dict[str, Any] | None = None

    # Three Hop Gas Trace
    gas_trace: dict[str, Any] | None = None

    # Module errors (module_name → error message)
    errors: dict[str, str] = field(default_factory=dict)

    # Which modules actually ran
    modules_run: list[str] = field(default_factory=list)

    # Composite risk score 0-100 (higher = more risky)
    composite_risk_score: float = 0.0
    risk_level: str = "unknown"  # low / moderate / high / critical

    # Aggregated red flags across all modules
    red_flags: list[str] = field(default_factory=list)

    # Per-module risk scores (module_name → 0-100)
    module_risk_scores: dict[str, float] = field(default_factory=dict)


# ─── Module instances (lazily initialized with config) ───────────────

# Default config for backward compatibility
_default_config: SentinelConfig = SentinelConfig()

# Module singletons — initialized lazily via _ensure_modules()
_holder_analyzer: HolderAnalyzer | None = None
_bundle_detector: BundleDetector | None = None
_exchange_funder: ExchangeFunderDetector | None = None
_liquidity_verifier: LiquidityVerifier | None = None
_dev_reputation: DevReputationEngine | None = None
_wash_trading: WashTradingDetector | None = None
_metadata_fingerprinter: MetadataFingerprinter | None = None
_sentiment_analyzer: SentimentAnalyzer | None = None
_pumpfun_analyzer: PumpFunAnalyzer | None = None
_honeypot_detector: HoneypotDetector | None = None
_contract_authority: ContractAuthorityScanner | None = None
_mev_detector: MEVDetector | None = None
_flash_loan_detector: FlashLoanDetector | None = None
_pump_dump_detector: PumpDumpDetector | None = None
_oracle_manipulation: OracleManipulationDetector | None = None
_governance_attack: GovernanceAttackDetector | None = None
_proxy_detector: ProxyDetector | None = None

# Tier 3 singletons
_static_analyzer: StaticAnalyzer | None = None
_decompiler_analyzer: DecompilerAnalyzer | None = None
_address_labeler: AddressLabeler | None = None

# Tier 4 singletons
_fund_flow_visualizer: FundFlowVisualizer | None = None
_contract_diff: ContractDiffAnalyzer | None = None

# Guilt by Association
_guilt_association: GuiltAssociationAnalyzer | None = None

# New — Sleep Cycle + Social Velocity + Bytecode + Block Zero + Gas Trace
_sleep_cycle: SleepCycleAnalyzer | None = None
_social_velocity: SocialVelocityAnalyzer | None = None
_bytecode_similarity: BytecodeSimilarityAnalyzer | None = None
_block_zero_sniper: BlockZeroSniperAnalyzer | None = None
_gas_trace: GasTraceAnalyzer | None = None


def _ensure_modules(config: SentinelConfig | None = None) -> None:
    """Initialize or reinitialize module singletons with the given config.

    If config is None, uses the default config (env vars for keys).
    Modules that accept API keys or shared clients are (re)created with
    the correct parameters.
    """
    global _holder_analyzer, _bundle_detector, _exchange_funder
    global _liquidity_verifier, _dev_reputation, _wash_trading
    global _metadata_fingerprinter, _sentiment_analyzer, _pumpfun_analyzer
    global _honeypot_detector, _contract_authority, _mev_detector
    global _flash_loan_detector, _pump_dump_detector, _oracle_manipulation
    global _governance_attack, _proxy_detector
    global _static_analyzer, _decompiler_analyzer, _address_labeler
    global _fund_flow_visualizer, _contract_diff
    global _guilt_association
    global _sleep_cycle, _social_velocity, _bytecode_similarity
    global _block_zero_sniper, _gas_trace

    cfg = config or _default_config
    dex_client = cfg.get_dexscreener_client()

    # Modules that need shared clients or API keys
    _metadata_fingerprinter = MetadataFingerprinter(
        dexscreener_client=dex_client,
        birdeye_api_key=cfg.birdeye_api_key,
    )
    _sentiment_analyzer = SentimentAnalyzer(
        dexscreener_client=dex_client,
        birdeye_api_key=cfg.birdeye_api_key,
    )
    _pumpfun_analyzer = PumpFunAnalyzer(
        helius_api_key=cfg.helius_api_key,
        dexscreener_client=dex_client,
    )

    # Modules that don't need external API clients (simple init)
    if _holder_analyzer is None:
        _holder_analyzer = HolderAnalyzer()
    if _bundle_detector is None:
        _bundle_detector = BundleDetector()
    if _exchange_funder is None:
        _exchange_funder = ExchangeFunderDetector()
    if _liquidity_verifier is None:
        _liquidity_verifier = LiquidityVerifier()
    if _dev_reputation is None:
        _dev_reputation = DevReputationEngine()
    if _wash_trading is None:
        _wash_trading = WashTradingDetector()
    if _honeypot_detector is None:
        _honeypot_detector = HoneypotDetector()
    if _contract_authority is None:
        _contract_authority = ContractAuthorityScanner()
    if _mev_detector is None:
        _mev_detector = MEVDetector()
    if _flash_loan_detector is None:
        _flash_loan_detector = FlashLoanDetector()
    if _pump_dump_detector is None:
        _pump_dump_detector = PumpDumpDetector()
    if _oracle_manipulation is None:
        _oracle_manipulation = OracleManipulationDetector()
    if _governance_attack is None:
        _governance_attack = GovernanceAttackDetector()
    if _proxy_detector is None:
        _proxy_detector = ProxyDetector()

    # Tier 3 modules
    if _static_analyzer is None:
        _static_analyzer = StaticAnalyzer()
    if _decompiler_analyzer is None:
        _decompiler_analyzer = DecompilerAnalyzer()
    if _address_labeler is None:
        _address_labeler = AddressLabeler()

    # Tier 4 modules
    if _fund_flow_visualizer is None:
        _fund_flow_visualizer = FundFlowVisualizer()
    if _contract_diff is None:
        _contract_diff = ContractDiffAnalyzer()

    # Guilt by Association
    if _guilt_association is None:
        _guilt_association = GuiltAssociationAnalyzer()

    # New modules
    if _sleep_cycle is None:
        _sleep_cycle = SleepCycleAnalyzer()
    if _social_velocity is None:
        _social_velocity = SocialVelocityAnalyzer()
    if _bytecode_similarity is None:
        _bytecode_similarity = BytecodeSimilarityAnalyzer()
    if _block_zero_sniper is None:
        _block_zero_sniper = BlockZeroSniperAnalyzer()
    if _gas_trace is None:
        _gas_trace = GasTraceAnalyzer()


# ─── Risk score extraction helpers ────────────────────────────────────


def _extract_risk_hhi(report: HHIReport) -> float:
    """Map HHI value to 0-100 risk score."""
    hhi = report.hhi
    if hhi < 1500:
        return min(hhi / 1500 * 30, 30)  # 0-30 range
    elif hhi < 3000:
        return 30 + (hhi - 1500) / 1500 * 30  # 30-60
    elif hhi < 5000:
        return 60 + (hhi - 3000) / 2000 * 20  # 60-80
    else:
        return min(80 + (hhi - 5000) / 5000 * 20, 100)  # 80-100


def _extract_risk_bundle(report: BundleReport) -> float:
    """Bundle report risk score — use bundle_risk_score if available, else heuristic."""
    if hasattr(report, "risk_score"):
        return float(report.risk_score)
    bundle_count = len(report.bundles) if hasattr(report, "bundles") and report.bundles else 0
    return min(bundle_count * 15, 100)


def _extract_risk_exchange_fund(report: ExchangeFundReport) -> float:
    """Exchange funding risk — higher CEX-funded ratio = more suspicious."""
    if hasattr(report, "cex_funded_percentage"):
        return min(float(report.cex_funded_percentage), 100)
    if hasattr(report, "risk_score"):
        return float(report.risk_score)
    return 0.0


def _extract_risk_lock(report: LockReport) -> float:
    """Lock risk — unlocked/no lock = 80, locked = low risk."""
    if hasattr(report, "risk_score"):
        return float(report.risk_score)
    if hasattr(report, "is_locked") and not report.is_locked:
        return 80.0
    return 10.0


def _extract_risk_dev(report: DevReputationReport) -> float:
    """Dev reputation risk — serial ruggers = high risk."""
    if hasattr(report, "risk_score"):
        return float(report.risk_score)
    if hasattr(report, "rug_count"):
        return min(int(report.rug_count) * 25, 100)
    return 0.0


def _extract_risk_wash_trading(report: WashTradingReport) -> float:
    """Wash trading risk score."""
    if hasattr(report, "risk_score"):
        return float(report.risk_score)
    if hasattr(report, "wash_trading_probability"):
        return float(report.wash_trading_probability) * 100
    return 0.0


def _extract_risk_fingerprint(report: FingerprintReport) -> float:
    """Metadata fingerprint risk — high similarity = higher risk."""
    if hasattr(report, "risk_score"):
        return float(report.risk_score)
    if hasattr(report, "max_similarity"):
        return float(report.max_similarity) * 100
    return 0.0


def _extract_risk_sentiment(report: SentimentReport) -> float:
    """Sentiment risk — extreme hype = risky."""
    if hasattr(report, "risk_score"):
        return float(report.risk_score)
    return 30.0  # neutral default


def _extract_risk_pumpfun(report: PumpFunReport) -> float:
    """PumpFun risk — low graduation probability or high bot ratio = risky."""
    if hasattr(report, "risk_score"):
        return float(report.risk_score)
    if hasattr(report, "graduation_probability"):
        return (1.0 - float(report.graduation_probability)) * 100
    return 30.0


def _extract_risk_guilt_association(report: GuiltAssociationReport) -> float:
    """Guilt association risk — use guilt_score directly."""
    if hasattr(report, "guilt_score"):
        return float(report.guilt_score)
    if hasattr(report, "risk_score"):
        return float(report.risk_score)
    return 0.0


def _extract_risk_sleep_cycle(report) -> float:
    """Sleep cycle risk — use sleep_score directly."""
    if hasattr(report, "sleep_score"):
        return float(report.sleep_score)
    return 0.0


def _extract_risk_social_velocity(report) -> float:
    """Social velocity risk — use velocity_score directly."""
    if hasattr(report, "velocity_score"):
        return float(report.velocity_score)
    return 0.0


def _extract_risk_bytecode_similarity(report) -> float:
    """Bytecode similarity risk — exact match = 100."""
    if hasattr(report, "exact_match") and report.exact_match:
        return 100.0
    if hasattr(report, "similarity_score"):
        return float(report.similarity_score)
    return 0.0


def _extract_risk_block_zero(report) -> float:
    """Block zero sniper risk — farm confidence * 100."""
    if hasattr(report, "farm_confidence"):
        return float(report.farm_confidence) * 100
    return 0.0


def _extract_risk_gas_trace(report) -> float:
    """Gas trace — dead end = 100, cex funded = 0."""
    if hasattr(report, "risk_score"):
        return float(report.risk_score)
    if hasattr(report, "dead_end") and report.dead_end:
        return 100.0
    if hasattr(report, "cex_funded") and report.cex_funded:
        return 10.0
    return 30.0


def _compute_composite(scores: dict[str, float]) -> tuple:
    """Weighted composite risk score → (score, level).

    Weights reflect each module's importance for rug-pull detection.
    """
    weights = {
        "holder_analysis": 0.20,
        "bundle_detection": 0.20,
        "exchange_funding": 0.10,
        "liquidity_verification": 0.15,
        "dev_reputation": 0.15,
        "wash_trading": 0.08,
        "metadata_fingerprint": 0.04,
        "sentiment": 0.04,
        "pumpfun_analysis": 0.04,
        "guilt_association": 0.06,
        "sleep_cycle": 0.04,
        "social_velocity": 0.03,
        "bytecode_similarity": 0.08,
        "block_zero_sniper": 0.08,
        "gas_trace": 0.06,
    }
    total = 0.0
    weight_used = 0.0
    for module, score in scores.items():
        w = weights.get(module, 0.05)
        total += score * w
        weight_used += w

    # Normalise if not all modules ran
    composite = total / weight_used if weight_used > 0 else 0.0

    if composite < 25:
        level = "low"
    elif composite < 50:
        level = "moderate"
    elif composite < 75:
        level = "high"
    else:
        level = "critical"

    return round(composite, 1), level


# ─── Red-flag collection ──────────────────────────────────────────────


def _collect_red_flags(report_dict: dict | None, module_name: str) -> list[str]:
    """Pull top red-flag strings from each module's dict result."""
    flags: list[str] = []
    if not report_dict:
        return flags

    # Common patterns: "red_flags", "warnings", "flags"
    for key in ("red_flags", "warnings", "flags", "risks"):
        items = report_dict.get(key, [])
        if isinstance(items, (list, tuple)):
            for item in items[:5]:
                text = str(item) if not isinstance(item, str) else item
                flags.append(f"[{module_name}] {text}")

    # If risk_level is high/critical, promote
    rl = report_dict.get("risk_level", "")
    if rl in ("high", "critical"):
        flags.append(f"[{module_name}] risk_level={rl}")
    return flags


# ─── Main pipeline ────────────────────────────────────────────────────


async def run_sentinel_scan(
    token_address: str,
    chain: str = "solana",
    dev_address: str | None = None,
    config: SentinelConfig | None = None,
) -> SentinelReport:
    """Run all 21 SENTINEL modules concurrently with graceful degradation.

    Args:
        token_address: Token contract address to scan.
        chain: Blockchain (solana, base, ethereum, etc.).
        dev_address: Optional deployer/creator wallet for dev reputation.
        config: Optional SentinelConfig with API keys and shared clients.
                If None, uses environment variables for keys.

    Returns:
        SentinelReport with per-module results, composite risk score, and flags.
    """
    # Ensure modules are initialized with correct config
    _ensure_modules(config)

    report = SentinelReport(token_address=token_address, chain=chain)

    # ── Build coroutine list ──────────────────────────────────────
    coros = {}

    coros["holder_analysis"] = _holder_analyzer.analyze(token_address, chain)
    coros["bundle_detection"] = _bundle_detector.detect_bundles(token_address, chain)
    coros["exchange_funding"] = _exchange_funder.analyze(token_address, chain)

    async def _liquidity():
        return await _liquidity_verifier.verify_lock(token_address, chain)

    coros["liquidity_verification"] = _liquidity()

    if dev_address:

        async def _dev():
            return await _dev_reputation.analyze(dev_address, chains=[chain])

        coros["dev_reputation"] = _dev()
    else:
        # Skip dev reputation if no address provided
        report.errors["dev_reputation"] = "skipped: no dev_address provided"
        coros["dev_reputation"] = None

    coros["wash_trading"] = _wash_trading.analyze(token_address, chain)
    coros["metadata_fingerprint"] = _metadata_fingerprinter.analyze_token_metadata(token_address, chain)
    coros["sentiment"] = _sentiment_analyzer.analyze(token_address, chain)
    coros["honeypot_detection"] = _honeypot_detector.analyze(token_address, chain)
    coros["contract_authority"] = _contract_authority.analyze(token_address, chain)
    coros["mev_detection"] = _mev_detector.analyze(token_address, chain)

    # Tier 2 scanners
    coros["flash_loan_detection"] = _flash_loan_detector.analyze(token_address, chain)
    coros["pump_dump_detection"] = _pump_dump_detector.analyze(token_address, chain)
    coros["oracle_manipulation"] = _oracle_manipulation.analyze(token_address, chain)
    coros["governance_attack"] = _governance_attack.analyze(token_address, chain)
    coros["proxy_detection"] = _proxy_detector.analyze(token_address, chain)

    # Tier 3 scanners
    coros["static_analysis"] = _static_analyzer.analyze(token_address, chain)
    coros["decompiler_analysis"] = _decompiler_analyzer.analyze(token_address, chain)
    coros["address_labels"] = _address_labeler.analyze(token_address, chain)

    # ── Wallet Memory Bank (replaces scattered wallet lookups) ───
    if _WALLET_MEMORY_AVAILABLE and dev_address:

        async def _wallet_intel():
            engine = get_wallet_engine()
            return await engine.get_deployer_intelligence(dev_address, chain)

        coros["wallet_intel"] = _wallet_intel()
    else:
        coros["wallet_intel"] = None

    # Tier 4 — Visualization
    coros["fund_flow"] = _fund_flow_visualizer.analyze(token_address, chain)
    coros["contract_diff"] = _contract_diff.analyze(token_address, chain)

    # Guilt by Association (requires deployer address)
    if dev_address:

        async def _guilt():
            return await _guilt_association.analyze(dev_address, chain)

        coros["guilt_association"] = _guilt()
    else:
        report.errors["guilt_association"] = "skipped: no dev_address provided"
        coros["guilt_association"] = None

    # Sleep Cycle (requires deployer deployment timestamps)
    if dev_address:

        async def _sleep():
            # This module needs deployment timestamps, pass empty for now
            return await _sleep_cycle.analyze([])

        coros["sleep_cycle"] = _sleep()
    else:
        coros["sleep_cycle"] = None

    # Social Velocity (runs on social mention data, pass empty)
    async def _social():
        return await _social_velocity.analyze([])

    coros["social_velocity"] = _social()

    # Bytecode Similarity (needs on-chain bytecode)
    async def _bytecode():
        return await _bytecode_similarity.analyze(token_address, chain)

    coros["bytecode_similarity"] = _bytecode()

    # Block Zero Sniper
    async def _blockzero():
        return await _block_zero_sniper.analyze(token_address, chain)

    coros["block_zero_sniper"] = _blockzero()

    # Gas Trace (requires deployer address)
    if dev_address:

        async def _gas():
            return await _gas_trace.analyze(dev_address, chain)

        coros["gas_trace"] = _gas()
    else:
        coros["gas_trace"] = None

    # PumpFun is Solana-only
    if chain.lower() == "solana":
        coros["pumpfun_analysis"] = _pumpfun_analyzer.analyze(token_address)
    else:
        report.errors["pumpfun_analysis"] = f"skipped: PumpFun analyzer is Solana-only (chain={chain})"
        coros["pumpfun_analysis"] = None

    # ── Run all modules concurrently with graceful degradation ────
    keys = [k for k, v in coros.items() if v is not None]
    tasks = {k: asyncio.create_task(coros[k]) for k in keys}

    results: dict[str, Any] = {}
    for k, task in tasks.items():
        try:
            results[k] = await task
        except Exception as exc:
            logger.warning(f"SENTINEL module {k} failed for {token_address}: {exc}")
            results[k] = None
            report.errors[k] = str(exc)[:200]

    # ── Convert dataclass results to dicts ───────────────────────
    risk_scores: dict[str, float] = {}

    extractors = {
        "holder_analysis": _extract_risk_hhi,
        "bundle_detection": _extract_risk_bundle,
        "exchange_funding": _extract_risk_exchange_fund,
        "liquidity_verification": _extract_risk_lock,
        "dev_reputation": _extract_risk_dev,
        "wash_trading": _extract_risk_wash_trading,
        "metadata_fingerprint": _extract_risk_fingerprint,
        "sentiment": _extract_risk_sentiment,
        "pumpfun_analysis": _extract_risk_pumpfun,
        "honeypot_detection": lambda r: float(getattr(r, "risk_score", 0)),
        "contract_authority": lambda r: float(getattr(r, "risk_score", 0)),
        "mev_detection": lambda r: float(getattr(r, "mev_risk_score", 0)),
        "flash_loan_detection": lambda r: float(getattr(r, "risk_score", 0)),
        "pump_dump_detection": lambda r: float(getattr(r, "risk_score", 0)),
        "oracle_manipulation": lambda r: float(getattr(r, "risk_score", 0)),
        "governance_attack": lambda r: float(getattr(r, "risk_score", 0)),
        "proxy_detection": lambda r: float(getattr(r, "risk_score", 0)),
        "static_analysis": lambda r: float(getattr(r, "risk_score", 0)),
        "decompiler_analysis": lambda r: float(getattr(r, "risk_score", 0)),
        "address_labels": lambda r: float(getattr(r, "risk_score", 0)),
        "wallet_intel": lambda r: float(r.get("risk_score", 0)) if isinstance(r, dict) else 0.0,
        "fund_flow": lambda r: float(getattr(r, "risk_score", 0)),
        "contract_diff": lambda r: float(getattr(r, "risk_score", 0)),
        "guilt_association": _extract_risk_guilt_association,
        "sleep_cycle": _extract_risk_sleep_cycle,
        "social_velocity": _extract_risk_social_velocity,
        "bytecode_similarity": _extract_risk_bytecode_similarity,
        "block_zero_sniper": _extract_risk_block_zero,
        "gas_trace": _extract_risk_gas_trace,
    }

    # Module name → field name on SentinelReport
    field_map = {
        "holder_analysis": "holder_analysis",
        "bundle_detection": "bundle_detection",
        "exchange_funding": "exchange_funding",
        "liquidity_verification": "liquidity_verification",
        "dev_reputation": "dev_reputation",
        "wash_trading": "wash_trading",
        "metadata_fingerprint": "metadata_fingerprint",
        "sentiment": "sentiment",
        "pumpfun_analysis": "pumpfun_analysis",
        "honeypot_detection": "honeypot_detection",
        "contract_authority": "contract_authority",
        "mev_detection": "mev_detection",
        "flash_loan_detection": "flash_loan_detection",
        "pump_dump_detection": "pump_dump_detection",
        "oracle_manipulation": "oracle_manipulation",
        "governance_attack": "governance_attack",
        "proxy_detection": "proxy_detection",
        "static_analysis": "static_analysis",
        "decompiler_analysis": "decompiler_analysis",
        "address_labels": "address_labels",
        "wallet_intel": "wallet_intel",
        "fund_flow": "fund_flow",
        "contract_diff": "contract_diff",
        "guilt_association": "guilt_association",
        "sleep_cycle": "sleep_cycle",
        "social_velocity": "social_velocity",
        "bytecode_similarity": "bytecode_similarity",
        "block_zero_sniper": "block_zero_sniper",
        "gas_trace": "gas_trace",
    }

    for module_name, raw_result in results.items():
        dict_result = dataclass_to_dict(raw_result) if raw_result is not None else None
        setattr(report, field_map[module_name], dict_result)
        report.modules_run.append(module_name)

        # Extract risk score
        if raw_result is not None and module_name in extractors:
            try:
                risk_scores[module_name] = extractors[module_name](raw_result)
            except Exception as e:
                logger.debug(f"Risk score extraction failed for {module_name}: {e}")

        # Collect red flags
        flags = _collect_red_flags(dict_result, module_name)
        report.red_flags.extend(flags)

    # ── Compute composite risk ───────────────────────────────────
    report.module_risk_scores = risk_scores
    composite, level = _compute_composite(risk_scores)
    report.composite_risk_score = composite
    report.risk_level = level

    # ── Data flywheel: feed results back to Wallet Memory Bank ───
    if _WALLET_MEMORY_AVAILABLE and dev_address and composite > 0:
        try:
            engine = get_wallet_engine()
            await engine.flag_deployer(
                address=dev_address,
                chain=chain,
                reason="sentinel_scan",
                token=token_address,
                score=composite,
            )
        except Exception as e:
            logger.debug(f"Wallet Memory Bank feedback failed: {e}")

    return report


# ─── Standalone single-module runners (for individual endpoint use) ──


async def run_holder_analysis(token_address: str, chain: str, config: SentinelConfig | None = None) -> dict[str, Any]:
    """Run HolderAnalyzer only, return dict."""
    _ensure_modules(config)
    result = await _holder_analyzer.analyze(token_address, chain)
    return dataclass_to_dict(result)


async def run_bundle_detection(token_address: str, chain: str, config: SentinelConfig | None = None) -> dict[str, Any]:
    """Run BundleDetector only, return dict."""
    _ensure_modules(config)
    result = await _bundle_detector.detect_bundles(token_address, chain)
    return dataclass_to_dict(result)


async def run_exchange_funding(token_address: str, chain: str, config: SentinelConfig | None = None) -> dict[str, Any]:
    """Run ExchangeFunderDetector only, return dict."""
    _ensure_modules(config)
    result = await _exchange_funder.analyze(token_address, chain)
    return dataclass_to_dict(result)


async def run_liquidity_verification(
    token_address: str, chain: str, config: SentinelConfig | None = None
) -> dict[str, Any]:
    """Run LiquidityVerifier only, return dict."""
    _ensure_modules(config)
    result = await _liquidity_verifier.verify_lock(token_address, chain)
    return dataclass_to_dict(result)


async def run_dev_reputation(
    dev_wallet: str, chains: list[str] | None = None, config: SentinelConfig | None = None
) -> dict[str, Any]:
    """Run DevReputationEngine only, return dict."""
    _ensure_modules(config)
    result = await _dev_reputation.analyze(dev_wallet, chains)
    return dataclass_to_dict(result)


async def run_wash_trading(token_address: str, chain: str, config: SentinelConfig | None = None) -> dict[str, Any]:
    """Run WashTradingDetector only, return dict."""
    _ensure_modules(config)
    result = await _wash_trading.analyze(token_address, chain)
    return dataclass_to_dict(result)


async def run_metadata_fingerprint(
    token_address: str, chain: str, config: SentinelConfig | None = None
) -> dict[str, Any]:
    """Run MetadataFingerprinter only, return dict."""
    _ensure_modules(config)
    result = await _metadata_fingerprinter.analyze_token_metadata(token_address, chain)
    return dataclass_to_dict(result)


async def run_sentiment(token_address: str, chain: str, config: SentinelConfig | None = None) -> dict[str, Any]:
    """Run SentimentAnalyzer only, return dict."""
    _ensure_modules(config)
    result = await _sentiment_analyzer.analyze(token_address, chain)
    return dataclass_to_dict(result)


async def run_pumpfun_analysis(token_address: str, config: SentinelConfig | None = None) -> dict[str, Any]:
    """Run PumpFunAnalyzer only (Solana only), return dict."""
    _ensure_modules(config)
    result = await _pumpfun_analyzer.analyze(token_address)
    return dataclass_to_dict(result)


async def run_honeypot_detection(
    token_address: str, chain: str, config: SentinelConfig | None = None
) -> dict[str, Any]:
    """Run HoneypotDetector only, return dict."""
    _ensure_modules(config)
    result = await _honeypot_detector.analyze(token_address, chain)
    return dataclass_to_dict(result)


async def run_contract_authority(
    token_address: str, chain: str, config: SentinelConfig | None = None
) -> dict[str, Any]:
    """Run ContractAuthorityScanner only, return dict."""
    _ensure_modules(config)
    result = await _contract_authority.analyze(token_address, chain)
    return dataclass_to_dict(result)


async def run_mev_detection(token_address: str, chain: str, config: SentinelConfig | None = None) -> dict[str, Any]:
    """Run MEVDetector only, return dict."""
    _ensure_modules(config)
    result = await _mev_detector.analyze(token_address, chain)
    return dataclass_to_dict(result)


async def run_flash_loan_detection(
    token_address: str, chain: str, config: SentinelConfig | None = None
) -> dict[str, Any]:
    """Run FlashLoanDetector only, return dict."""
    _ensure_modules(config)
    result = await _flash_loan_detector.analyze(token_address, chain)
    return dataclass_to_dict(result)


async def run_pump_dump_detection(
    token_address: str, chain: str, config: SentinelConfig | None = None
) -> dict[str, Any]:
    """Run PumpDumpDetector only, return dict."""
    _ensure_modules(config)
    result = await _pump_dump_detector.analyze(token_address, chain)
    return dataclass_to_dict(result)


async def run_oracle_manipulation(
    token_address: str, chain: str, config: SentinelConfig | None = None
) -> dict[str, Any]:
    """Run OracleManipulationDetector only, return dict."""
    _ensure_modules(config)
    result = await _oracle_manipulation.analyze(token_address, chain)
    return dataclass_to_dict(result)


async def run_governance_attack(token_address: str, chain: str, config: SentinelConfig | None = None) -> dict[str, Any]:
    """Run GovernanceAttackDetector only, return dict."""
    _ensure_modules(config)
    result = await _governance_attack.analyze(token_address, chain)
    return dataclass_to_dict(result)


async def run_proxy_detection(token_address: str, chain: str, config: SentinelConfig | None = None) -> dict[str, Any]:
    """Run ProxyDetector only, return dict."""
    _ensure_modules(config)
    result = await _proxy_detector.analyze(token_address, chain)
    return dataclass_to_dict(result)


async def run_static_analysis(token_address: str, chain: str, config: SentinelConfig | None = None) -> dict[str, Any]:
    """Run StaticAnalyzer only, return dict."""
    _ensure_modules(config)
    result = await _static_analyzer.analyze(token_address, chain)
    return dataclass_to_dict(result)


async def run_decompiler_analysis(
    token_address: str, chain: str, config: SentinelConfig | None = None
) -> dict[str, Any]:
    """Run DecompilerAnalyzer only, return dict."""
    _ensure_modules(config)
    result = await _decompiler_analyzer.analyze(token_address, chain)
    return dataclass_to_dict(result)


async def run_address_labels(token_address: str, chain: str, config: SentinelConfig | None = None) -> dict[str, Any]:
    """Run AddressLabeler only, return dict."""
    _ensure_modules(config)
    result = await _address_labeler.analyze(token_address, chain)
    return dataclass_to_dict(result)


async def run_fund_flow(token_address: str, chain: str, config: SentinelConfig | None = None) -> dict[str, Any]:
    """Run FundFlowVisualizer only, return dict."""
    _ensure_modules(config)
    result = await _fund_flow_visualizer.analyze(token_address, chain)
    return dataclass_to_dict(result)


async def run_contract_diff(token_address: str, chain: str, config: SentinelConfig | None = None) -> dict[str, Any]:
    """Run ContractDiffAnalyzer only, return dict."""
    _ensure_modules(config)
    result = await _contract_diff.analyze(token_address, chain)
    return dataclass_to_dict(result)
