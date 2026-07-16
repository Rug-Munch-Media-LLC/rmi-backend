"""
SENTINEL - Multi-Chain Token Security Scanner
=============================================
17 Detection Modules for comprehensive token security analysis.

Modules:
  - holder_analyzer: HHI calculation, fake diversification detection
  - bundle_detector: Enhanced bundle/sniper detection, funding chain analysis
  - exchange_funder: CEX-funded wallet detection
  - liquidity_verifier: Lock verification, fake locker detection, expiry monitoring
  - dev_reputation: Developer serial rugg detection, cross-chain portability
  - wash_trading: Circular transfer detection, cross-DEX loops
  - pumpfun_analyzer: Pump.fun bonding curve, bot detection, graduation monitoring
  - sentiment_analyzer: Sentiment scoring, bot campaign detection, pump probability
  - metadata_fingerprint: HTML structure hashing, description similarity, social overlap
  - honeypot_detector: Honeypot detection via buy/sell simulation, transfer tax analysis
  - contract_authority: Mint/freeze/update authority, proxy detection, ownership renunciation
  - mev_detector: MEV/sandwich attack detection, Jito bundle inspection, known bot tracking
  - flash_loan_detector: Flash loan attack detection, borrow-then-dump patterns
  - pump_dump_detector: Pump-and-dump lifecycle, coordinated shill, volume spike detection
  - oracle_manipulation: Oracle source/depth analysis, price manipulation vulnerability
  - governance_attack: Governance concentration, timelock/quorum risk detection
  - proxy_detector: Proxy resolution, implementation fingerprinting, upgrade risk
"""

from .address_labeler import AddressLabeler, AddressLabelReport  # noqa: F401
from .block_zero_sniper import BlockZeroReport, BlockZeroSniperAnalyzer  # noqa: F401
from .bundle_detector import BundleDetector  # noqa: F401
from .bytecode_similarity import (  # noqa: F401
    BytecodeSimilarityAnalyzer,
    BytecodeSimilarityReport,
    hash_bytecode,
)
from .contract_authority import (
    ContractAuthorityReport,  # noqa: F401
    ContractAuthorityScanner,  # noqa: F401
    run_contract_authority_scan,  # noqa: F401
)
from .contract_diff import ContractDiffAnalyzer, ContractDiffReport
from .decompiler_analyzer import DecompilerAnalyzer, DecompilerReport  # noqa: F401
from .dev_reputation import DevReputationEngine  # noqa: F401
from .exchange_funder import ExchangeFunderDetector  # noqa: F401
from .flash_loan_detector import FlashLoanDetector, FlashLoanReport  # noqa: F401
from .fund_flow_visualizer import FundFlowReport, FundFlowVisualizer  # noqa: F401
from .gas_trace import GasTraceAnalyzer, GasTraceReport, run_gas_trace_analysis  # noqa: F401
from .governance_attack import GovernanceAttackDetector, GovernanceAttackReport  # noqa: F401
from .guilt_association import (
    GuiltAssociationAnalyzer,  # noqa: F401
    GuiltAssociationReport,  # noqa: F401
    run_guilt_association,  # noqa: F401
)
from .holder_analyzer import HolderAnalyzer  # noqa: F401
from .honeypot_detector import HoneypotDetector, HoneypotReport  # noqa: F401
from .liquidity_verifier import LiquidityVerifier  # noqa: F401
from .metadata_fingerprint import MetadataFingerprinter  # noqa: F401
from .mev_detector import MEVBotActivity, MEVDetector, MEVReport, SandwichAttack  # noqa: F401
from .oracle_manipulation import OracleManipulationDetector, OracleManipulationReport  # noqa: F401
from .proxy_detector import ProxyDetector, ProxyReport  # noqa: F401
from .pump_dump_detector import PumpDumpDetector, PumpDumpReport  # noqa: F401
from .pumpfun_analyzer import PumpFunAnalyzer  # noqa: F401
from .rag_citations import (  # noqa: F401
    build_citation_string,
    query_address_rag,
    query_rag_citations,
)
from .sentiment_analyzer import SentimentAnalyzer  # noqa: F401

# Pipeline orchestrator
from .sentinel_pipeline import (
    SentinelReport,  # noqa: F401
    dataclass_to_dict,  # noqa: F401
    run_address_labels,  # noqa: F401
    run_bundle_detection,  # noqa: F401
    run_contract_authority,
    run_decompiler_analysis,  # noqa: F401
    run_dev_reputation,  # noqa: F401
    run_exchange_funding,  # noqa: F401
    run_flash_loan_detection,  # noqa: F401
    run_fund_flow,  # noqa: F401
    run_governance_attack,  # noqa: F401
    run_holder_analysis,  # noqa: F401
    run_honeypot_detection,
    run_liquidity_verification,  # noqa: F401
    run_metadata_fingerprint,  # noqa: F401
    run_mev_detection,
    run_oracle_manipulation,  # noqa: F401
    run_proxy_detection,  # noqa: F401
    run_pump_dump_detection,  # noqa: F401
    run_pumpfun_analysis,  # noqa: F401
    run_sentiment,  # noqa: F401
    run_sentinel_scan,  # noqa: F401
    run_static_analysis,  # noqa: F401
    run_wash_trading,  # noqa: F401
)
from .sleep_cycle_scanner import SleepCycleAnalyzer, SleepCycleReport  # noqa: F401
from .social_velocity import SocialVelocityAnalyzer, SocialVelocityReport  # noqa: F401
from .static_analyzer import StaticAnalysisReport, StaticAnalyzer  # noqa: F401
from .wash_trading import WashTradingDetector  # noqa: F401

__all__ = [
    "ContractDiffAnalyzer",
    "ContractDiffReport",
    "run_contract_authority",
    "run_honeypot_detection",
    "run_mev_detection",
]
