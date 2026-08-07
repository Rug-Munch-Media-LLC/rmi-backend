"""
SENTINEL - Module Singleton Management
=======================================
Lazy-initialized module singletons and ``_ensure_modules()`` factory.

Modules are registered via the ``@sentinel_module`` decorator (imported
from ``._decorator``) which populates ``_SENTINEL_REGISTRY``.  New scanner
classes dropped into ``app/domains/scanners/`` are auto-discovered at import
time — no manual wiring of singletons, FREE/PREMIUM tuples, or descriptions
dicts required.

Backward Compat
---------------
The legacy manual singleton variables (``_holder_analyzer`` etc.) and the
hardcoded ``SENTINEL_FREE_MODULES`` / ``SENTINEL_PREMIUM_MODULES`` tuples
in ``sentinel_phase.py`` still work.  The registry is additive.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.domains.scanners._sentinel_registry import _SENTINEL_REGISTRY

# Module scanner imports - all known scanners (kept for backward compat;
# the import triggers ``@sentinel_module`` decorators to fire).
from ..address_labeler import AddressLabeler
from ..block_zero_sniper import BlockZeroSniperAnalyzer
from ..bundle_detector import BundleDetector
from ..bytecode_similarity import BytecodeSimilarityAnalyzer
from ..contract_authority import ContractAuthorityScanner
from ..contract_diff import ContractDiffAnalyzer
from ..cross_chain_trace import CrossChainTraceAnalyzer
from ..decompiler_analyzer import DecompilerAnalyzer
from ..dev_reputation import DevReputationEngine
from ..exchange_funder import ExchangeFunderDetector
from ..flash_loan_detector import FlashLoanDetector
from ..fund_flow_visualizer import FundFlowVisualizer
from ..gas_trace import GasTraceAnalyzer
from ..governance_attack import GovernanceAttackDetector
from ..guilt_association import GuiltAssociationAnalyzer
from ..holder_analyzer import HolderAnalyzer
from ..honeypot_detector import HoneypotDetector
from ..liquidity_verifier import LiquidityVerifier
from ..metadata_fingerprint import MetadataFingerprinter
from ..mev_detector import MEVDetector
from ..oracle_manipulation import OracleManipulationDetector
from ..proxy_detector import ProxyDetector
from ..pump_dump_detector import PumpDumpDetector
from ..pumpfun_analyzer import PumpFunAnalyzer
from ..sentiment_analyzer import SentimentAnalyzer
from ..sleep_cycle_scanner import SleepCycleAnalyzer
from ..social_velocity import SocialVelocityAnalyzer
from ..static_analyzer import StaticAnalyzer
from ..wash_trading import WashTradingDetector
from .config import SentinelConfig

# Wallet Memory Bank integration — import doubles as an availability probe;
# the engine is intentionally unused here (only _WALLET_MEMORY_AVAILABLE matters).
try:
    from app.wallet_memory.engine import get_wallet_engine as _get_wallet_engine

    _WALLET_MEMORY_AVAILABLE = True
except ImportError:
    _WALLET_MEMORY_AVAILABLE = False

logger = get_logger("sentinel_pipeline")

# Default config for backward compatibility
_default_config: SentinelConfig = SentinelConfig()

# ── Legacy singleton variables (backward compat) ────────────────────
# Populated by _ensure_modules().  Module keys map to these via
# _KEY_TO_SINGLETON_ATTR below.

_holder_analyzer = None
_bundle_detector = None
_exchange_funder = None
_liquidity_verifier = None
_dev_reputation = None
_wash_trading = None
_metadata_fingerprinter = None
_sentiment_analyzer = None
_pumpfun_analyzer = None
_honeypot_detector = None
_contract_authority = None
_mev_detector = None
_flash_loan_detector = None
_pump_dump_detector = None
_oracle_manipulation = None
_governance_attack = None
_proxy_detector = None
_static_analyzer = None
_decompiler_analyzer = None
_address_labeler = None
_fund_flow_visualizer = None
_contract_diff = None
_guilt_association = None
_sleep_cycle = None
_social_velocity = None
_bytecode_similarity = None
_block_zero_sniper = None
_gas_trace = None
_cross_chain_trace = None

# Map registry key → singleton attribute name on this module.
# Used by _ensure_modules() to set the correct variable.
_KEY_TO_SINGLETON_ATTR: dict[str, str] = {
    "holder_analysis": "_holder_analyzer",
    "bundle_detection": "_bundle_detector",
    "exchange_funding": "_exchange_funder",
    "liquidity_verification": "_liquidity_verifier",
    "dev_reputation": "_dev_reputation",
    "wash_trading": "_wash_trading",
    "metadata_fingerprint": "_metadata_fingerprinter",
    "sentiment": "_sentiment_analyzer",
    "pumpfun_analysis": "_pumpfun_analyzer",
    "honeypot_detection": "_honeypot_detector",
    "contract_authority": "_contract_authority",
    "mev_detection": "_mev_detector",
    "flash_loan_detection": "_flash_loan_detector",
    "pump_dump_detection": "_pump_dump_detector",
    "oracle_manipulation": "_oracle_manipulation",
    "governance_attack": "_governance_attack",
    "proxy_detection": "_proxy_detector",
    "static_analysis": "_static_analyzer",
    "decompiler_analysis": "_decompiler_analyzer",
    "address_labels": "_address_labeler",
    "fund_flow": "_fund_flow_visualizer",
    "contract_diff": "_contract_diff",
    "guilt_association": "_guilt_association",
    "sleep_cycle": "_sleep_cycle",
    "social_velocity": "_social_velocity",
    "bytecode_similarity": "_bytecode_similarity",
    "block_zero_sniper": "_block_zero_sniper",
    "gas_trace": "_gas_trace",
    "cross_chain_trace": "_cross_chain_trace",
}

# Modules that require constructor arguments beyond the default no-arg init.
# Format: key → factory function(config) → instance
_SPECIAL_CONSTRUCTORS: dict[str, object] = {}  # populated lazily below

# ── Tiered module selection ──────────────────────────────────────────
# Scan tiers for the unified scanner / SENTINEL pipeline.  The "quick" tier
# runs only free modules (always available, fast, no premium gating); the
# "deep" tier runs every SENTINEL module (free + premium deep-forensic).
TIERS: dict[str, str] = {
    "quick": (
        "Free-tier SENTINEL modules only — fast baseline safety scan "
        "(holder_analysis, contract_authority, liquidity_verification, "
        "metadata_fingerprint, proxy_detection, dev_reputation)."
    ),
    "deep": (
        "All SENTINEL modules — free + premium deep-forensic analysis "
        "(bundles, exchange funding, wash trading, MEV, flash loans, "
        "pump/dump, oracle manipulation, deployer forensics, sentiment)."
    ),
}

# Canonical module keys for the "quick" (free-tier) scan.
_QUICK_TIER_MODULES: tuple[str, ...] = (
    "holder_analysis",
    "contract_authority",
    "liquidity_verification",
    "metadata_fingerprint",
    "proxy_detection",
    "dev_reputation",
)


def get_modules_by_tier(tier: str) -> list[str]:
    """Return the SENTINEL module keys to run for a scan tier.

    * ``"quick"`` → free tier only (holder_analysis, contract_authority,
      liquidity_verification, metadata_fingerprint, proxy_detection,
      dev_reputation).
    * ``"deep"``  → all modules (free + premium), free keys first.

    Unknown/empty tiers fall back to the ``"quick"`` set so a scan never
    accidentally runs premium modules.  ``"deep"`` resolves the full set
    from the sentinel registry when populated, else falls back to every
    known module key.
    """
    tier = (tier or "quick").strip().lower()
    if tier == "deep":
        from app.domains.scanners._sentinel_registry import (
            get_free_module_keys,
            get_premium_module_keys,
        )

        free = get_free_module_keys()
        premium = get_premium_module_keys()
        if free or premium:
            return [*free, *premium]
        # Registry not yet populated — fall back to quick modules + every
        # other known registry key (premium set).
        known_premium = [k for k in _KEY_TO_SINGLETON_ATTR if k not in _QUICK_TIER_MODULES]
        return [*_QUICK_TIER_MODULES, *known_premium]
    return list(_QUICK_TIER_MODULES)


def _auto_discover_scanners() -> None:
    """Import every ``.py`` file under ``app/domains/scanners/``.

    Called on first import of this module so that any newly-dropped
    scanner classes with ``@sentinel_module`` fire their decorator.
    Already-imported modules are no-ops (Python caches them).
    """
    import importlib
    import pkgutil
    from pathlib import Path as _Path

    scanners_dir = _Path(__file__).resolve().parent.parent
    for _finder, _name, _ispkg in pkgutil.iter_modules([str(scanners_dir)]):
        if _name.startswith("_") or _name in ("sentinel_pipeline",):
            continue
        try:
            importlib.import_module(f"app.domains.scanners.{_name}")
        except ImportError:
            logger.debug("auto_discover_skip", module=_name)


def _ensure_modules(config: SentinelConfig | None = None) -> None:
    """Initialize or reinitialize module singletons with the given config.

    Iterates the ``_SENTINEL_REGISTRY`` (populated by ``@sentinel_module``
    decorators) to instantiate scanner classes.  Falls back to legacy
    manual instantiation for any singleton still ``None`` after the
    registry pass.
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
    global _block_zero_sniper, _gas_trace, _cross_chain_trace

    cfg = config or _default_config
    dex_client = cfg.get_dexscreener_client()

    # ── Auto-discover before first instantiation ──────────────────
    _auto_discover_scanners()

    # ── Modules that need shared clients / API keys (special constructors) ──
    # These must be set fresh every time _ensure_modules runs with a new
    # config (not guarded by ``is None``) so that key rotators work.
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

    # ── Registry-driven instantiation ────────────────────────────
    for _key, _entry in _SENTINEL_REGISTRY.items():
        _attr = _KEY_TO_SINGLETON_ATTR.get(_key)
        if _attr is None:
            continue  # key not mapped to a legacy singleton — skip
        _existing = getattr(__import__(__name__), _attr, None) if False else globals().get(_attr)  # type: ignore[arg-type]
        if _existing is not None:
            continue  # already initialized
        try:
            _cls = _entry["class"]
            _instance = _cls()
            globals()[_attr] = _instance  # type: ignore[index]
        except Exception:
            logger.debug("sentinel_init_failed", key=_key)

    # ── Legacy fallback: any singleton still None gets manual init ──
    _legacy_fallback_init()


def _legacy_fallback_init() -> None:
    """Manual instantiation for any singleton that is still ``None``.

    This is the original ``_ensure_modules`` body (minus the special-
    constructor modules).  Ensures backward compatibility when a module
    is imported but not decorated with ``@sentinel_module``.
    """
    global _holder_analyzer, _bundle_detector, _exchange_funder
    global _liquidity_verifier, _dev_reputation, _wash_trading
    global _honeypot_detector, _contract_authority, _mev_detector
    global _flash_loan_detector, _pump_dump_detector, _oracle_manipulation
    global _governance_attack, _proxy_detector
    global _static_analyzer, _decompiler_analyzer, _address_labeler
    global _fund_flow_visualizer, _contract_diff
    global _guilt_association
    global _sleep_cycle, _social_velocity, _bytecode_similarity
    global _block_zero_sniper, _gas_trace, _cross_chain_trace

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

    if _static_analyzer is None:
        _static_analyzer = StaticAnalyzer()
    if _decompiler_analyzer is None:
        _decompiler_analyzer = DecompilerAnalyzer()
    if _address_labeler is None:
        _address_labeler = AddressLabeler()

    if _fund_flow_visualizer is None:
        _fund_flow_visualizer = FundFlowVisualizer()
    if _contract_diff is None:
        _contract_diff = ContractDiffAnalyzer()

    if _guilt_association is None:
        _guilt_association = GuiltAssociationAnalyzer()

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

    if _cross_chain_trace is None:
        _cross_chain_trace = CrossChainTraceAnalyzer()
