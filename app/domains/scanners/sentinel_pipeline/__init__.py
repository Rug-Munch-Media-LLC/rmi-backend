"""
SENTINEL - Unified Pipeline Orchestrator
==========================================
Package split into responsibility-focused modules:
  - config:    SentinelConfig, SentinelReport, dataclass_to_dict
  - modules:   Module singleton management (_ensure_modules)
  - risk:      Risk score extraction helpers, composite scoring, red flags
  - runners:   run_sentinel_scan + all standalone module runners
"""

from __future__ import annotations

from .config import SentinelConfig, SentinelReport, dataclass_to_dict
from .runners import (
    run_address_labels,
    run_bundle_detection,
    run_contract_authority,
    run_contract_diff,
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

# NOTE: module singletons (_holder_analyzer, ...) and risk helpers
# (_collect_red_flags, _compute_composite, ...) are intentionally NOT
# re-exported here. They are consumed via the submodules:
#   from app.domains.scanners.sentinel_pipeline import modules as sm
#   from app.domains.scanners.sentinel_pipeline import runners
# See app/domains/scanner/unified/sentinel_phase.py for the consumer.

__all__ = [
    "SentinelConfig",
    "SentinelReport",
    "dataclass_to_dict",
    "run_address_labels",
    "run_bundle_detection",
    "run_contract_authority",
    "run_contract_diff",
    "run_decompiler_analysis",
    "run_dev_reputation",
    "run_exchange_funding",
    "run_flash_loan_detection",
    "run_fund_flow",
    "run_governance_attack",
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
