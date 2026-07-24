"""
SENTINEL - Module Singleton Management
=======================================
Lazy-initialized module singletons and _ensure_modules() factory.
"""

from __future__ import annotations

from app.core.logging import get_logger

# Module scanner imports - all known scanners
from ..address_labeler import AddressLabeler
from ..block_zero_sniper import BlockZeroSniperAnalyzer
from ..bundle_detector import BundleDetector
from ..bytecode_similarity import BytecodeSimilarityAnalyzer
from ..contract_authority import ContractAuthorityScanner
from ..contract_diff import ContractDiffAnalyzer
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

# Wallet Memory Bank integration
try:
    from app.wallet_memory.engine import get_wallet_engine
    _WALLET_MEMORY_AVAILABLE = True
except ImportError:
    _WALLET_MEMORY_AVAILABLE = False

logger = get_logger("sentinel_pipeline")

# Default config for backward compatibility
_default_config: SentinelConfig = SentinelConfig()

# Module singletons - lazily initialized via _ensure_modules()
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

# New - Sleep Cycle + Social Velocity + Bytecode + Block Zero + Gas Trace
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
