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

from __future__ import annotations

from .address_labeler import AddressLabeler, AddressLabelReport
from .block_zero_sniper import BlockZeroReport, BlockZeroSniperAnalyzer
from .bundle_detector import BundleDetector
from .bytecode_similarity import (
    BytecodeSimilarityAnalyzer,
    BytecodeSimilarityReport,
    hash_bytecode,
)
from .contract_authority import (
    ContractAuthorityReport,
    ContractAuthorityScanner,
    run_contract_authority_scan,
)
from .contract_diff import ContractDiffAnalyzer, ContractDiffReport
from .cross_chain_trace import (
    CrossChainTraceAnalyzer,
    CrossChainTraceReport,
    analyze_wallet as run_cross_chain_trace,
)
from .decompiler_analyzer import DecompilerAnalyzer, DecompilerReport
from .dev_reputation import DevReputationEngine
from .exchange_funder import ExchangeFunderDetector
from .flash_loan_detector import FlashLoanDetector, FlashLoanReport
from .fund_flow_visualizer import FundFlowReport, FundFlowVisualizer
from .gas_trace import GasTraceAnalyzer, GasTraceReport, run_gas_trace_analysis
from .governance_attack import GovernanceAttackDetector, GovernanceAttackReport
from .guilt_association import (
    GuiltAssociationAnalyzer,
    GuiltAssociationReport,
    run_guilt_association,
)
from .holder_analyzer import HolderAnalyzer
from .honeypot_detector import HoneypotDetector, HoneypotReport
from .liquidity_verifier import LiquidityVerifier
from .metadata_fingerprint import MetadataFingerprinter
from .mev_detector import MEVBotActivity, MEVDetector, MEVReport, SandwichAttack
from .oracle_manipulation import OracleManipulationDetector, OracleManipulationReport
from .proxy_detector import ProxyDetector, ProxyReport
from .pump_dump_detector import PumpDumpDetector, PumpDumpReport
from .pumpfun_analyzer import PumpFunAnalyzer
from .rag_citations import (
    build_citation_string,
    query_address_rag,
    query_rag_citations,
)
from .sentiment_analyzer import SentimentAnalyzer

# Pipeline orchestrator
from .sentinel_pipeline import (
    SentinelReport,
    dataclass_to_dict,
    run_address_labels,
    run_bundle_detection,
    run_contract_authority,
    run_decompiler_analysis,
    run_dev_reputation,
    run_exchange_funding,
    run_flash_loan_detection,
    run_fund_flow,
    run_governance_attack,
    run_holder_analysis,
    run_honeypot_detection,
    run_liquidity_verification,
    run_metadata_fingerprint,
    run_mev_detection,
    run_oracle_manipulation,
    run_proxy_detection,
    run_pump_dump_detection,
    run_pumpfun_analysis,
    run_sentiment,
    run_sentinel_scan,
    run_static_analysis,
    run_wash_trading,
)
from .sleep_cycle_scanner import SleepCycleAnalyzer, SleepCycleReport
from .social_velocity import SocialVelocityAnalyzer, SocialVelocityReport
from .static_analyzer import StaticAnalysisReport, StaticAnalyzer
from .wash_trading import WashTradingDetector

__all__ = [
    "AddressLabelReport",
    # Address labeling
    "AddressLabeler",
    # Block-zero sniper
    "BlockZeroReport",
    "BlockZeroSniperAnalyzer",
    # Bundles
    "BundleDetector",
    # Bytecode similarity
    "BytecodeSimilarityAnalyzer",
    "BytecodeSimilarityReport",
    # Contract authority
    "ContractAuthorityReport",
    "ContractAuthorityScanner",
    # Contract diff
    "ContractDiffAnalyzer",
    "ContractDiffReport",
    # Cross-chain trace
    "CrossChainTraceAnalyzer",
    "CrossChainTraceReport",
    # Decompiler
    "DecompilerAnalyzer",
    "DecompilerReport",
    # Dev reputation
    "DevReputationEngine",
    # Exchange funding
    "ExchangeFunderDetector",
    # Flash loans
    "FlashLoanDetector",
    "FlashLoanReport",
    # Fund flow
    "FundFlowReport",
    "FundFlowVisualizer",
    # Gas trace
    "GasTraceAnalyzer",
    "GasTraceReport",
    # Governance attacks
    "GovernanceAttackDetector",
    "GovernanceAttackReport",
    # Guilt association
    "GuiltAssociationAnalyzer",
    "GuiltAssociationReport",
    # Holders
    "HolderAnalyzer",
    # Honeypots
    "HoneypotDetector",
    "HoneypotReport",
    # Liquidity
    "LiquidityVerifier",
    # MEV
    "MEVBotActivity",
    "MEVDetector",
    "MEVReport",
    # Metadata fingerprinting
    "MetadataFingerprinter",
    # Oracle manipulation
    "OracleManipulationDetector",
    "OracleManipulationReport",
    # Proxies
    "ProxyDetector",
    "ProxyReport",
    # Pump & dump
    "PumpDumpDetector",
    "PumpDumpReport",
    # Pump.fun
    "PumpFunAnalyzer",
    "SandwichAttack",
    # Sentiment
    "SentimentAnalyzer",
    # SENTINEL pipeline orchestrator
    "SentinelReport",
    # Sleep cycle
    "SleepCycleAnalyzer",
    "SleepCycleReport",
    # Social velocity
    "SocialVelocityAnalyzer",
    "SocialVelocityReport",
    # Static analysis
    "StaticAnalysisReport",
    "StaticAnalyzer",
    # Wash trading
    "WashTradingDetector",
    # RAG citations
    "build_citation_string",
    "dataclass_to_dict",
    "hash_bytecode",
    "query_address_rag",
    "query_rag_citations",
    "run_address_labels",
    "run_bundle_detection",
    "run_contract_authority",
    "run_contract_authority_scan",
    "run_cross_chain_trace",
    "run_decompiler_analysis",
    "run_dev_reputation",
    "run_exchange_funding",
    "run_flash_loan_detection",
    "run_fund_flow",
    "run_gas_trace_analysis",
    "run_governance_attack",
    "run_guilt_association",
    "run_holder_analysis",
    "run_honeypot_detection",
    "run_liquidity_verification",
    "run_metadata_fingerprint",
    "run_mev_detection",
    "run_oracle_manipulation",
    "run_proxy_detection",
    "run_pump_dump_detection",
    "run_pumpfun_analysis",
    "run_sentiment",
    "run_sentinel_scan",
    "run_static_analysis",
    "run_wash_trading",
]
