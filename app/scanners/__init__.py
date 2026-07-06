"""scanners - DEPRECATED shim. Use app.domains.scanners.

Phase 4.8 of AUDIT-2026-Q3.md moved app/scanners/ to app/domains/scanners/.
The 33 detection modules are IP and will be split to rmi-ip in Phase 6.

This shim aliases all submodules so legacy imports like
`from app.scanners.bundle_detector import *` keep working.
"""
import sys as _sys
import importlib as _importlib

_sys.modules["app.scanners.address_labeler"] = _importlib.import_module("app.domains.scanners.address_labeler")
_sys.modules["app.scanners.block_zero_sniper"] = _importlib.import_module("app.domains.scanners.block_zero_sniper")
_sys.modules["app.scanners.bundle_detector"] = _importlib.import_module("app.domains.scanners.bundle_detector")
_sys.modules["app.scanners.bytecode_similarity"] = _importlib.import_module("app.domains.scanners.bytecode_similarity")
_sys.modules["app.scanners.contract_authority"] = _importlib.import_module("app.domains.scanners.contract_authority")
_sys.modules["app.scanners.contract_diff"] = _importlib.import_module("app.domains.scanners.contract_diff")
_sys.modules["app.scanners.decompiler_analyzer"] = _importlib.import_module("app.domains.scanners.decompiler_analyzer")
_sys.modules["app.scanners.dev_reputation"] = _importlib.import_module("app.domains.scanners.dev_reputation")
_sys.modules["app.scanners.exchange_funder"] = _importlib.import_module("app.domains.scanners.exchange_funder")
_sys.modules["app.scanners.flash_loan_detector"] = _importlib.import_module("app.domains.scanners.flash_loan_detector")
_sys.modules["app.scanners.fund_flow_visualizer"] = _importlib.import_module("app.domains.scanners.fund_flow_visualizer")
_sys.modules["app.scanners.gas_trace"] = _importlib.import_module("app.domains.scanners.gas_trace")
_sys.modules["app.scanners.governance_attack"] = _importlib.import_module("app.domains.scanners.governance_attack")
_sys.modules["app.scanners.guilt_association"] = _importlib.import_module("app.domains.scanners.guilt_association")
_sys.modules["app.scanners.holder_analyzer"] = _importlib.import_module("app.domains.scanners.holder_analyzer")
_sys.modules["app.scanners.honeypot_detector"] = _importlib.import_module("app.domains.scanners.honeypot_detector")
_sys.modules["app.scanners.liquidity_verifier"] = _importlib.import_module("app.domains.scanners.liquidity_verifier")
_sys.modules["app.scanners.metadata_fingerprint"] = _importlib.import_module("app.domains.scanners.metadata_fingerprint")
_sys.modules["app.scanners.mev_detector"] = _importlib.import_module("app.domains.scanners.mev_detector")
_sys.modules["app.scanners.oracle_manipulation"] = _importlib.import_module("app.domains.scanners.oracle_manipulation")
_sys.modules["app.scanners.proxy_detector"] = _importlib.import_module("app.domains.scanners.proxy_detector")
_sys.modules["app.scanners.pump_dump_detector"] = _importlib.import_module("app.domains.scanners.pump_dump_detector")
_sys.modules["app.scanners.pumpfun_analyzer"] = _importlib.import_module("app.domains.scanners.pumpfun_analyzer")
_sys.modules["app.scanners.pumpfun_enhanced"] = _importlib.import_module("app.domains.scanners.pumpfun_enhanced")
_sys.modules["app.scanners.rag_citations"] = _importlib.import_module("app.domains.scanners.rag_citations")
_sys.modules["app.scanners.sentiment_analyzer"] = _importlib.import_module("app.domains.scanners.sentiment_analyzer")
_sys.modules["app.scanners.sentinel_pipeline"] = _importlib.import_module("app.domains.scanners.sentinel_pipeline")
_sys.modules["app.scanners.sleep_cycle_scanner"] = _importlib.import_module("app.domains.scanners.sleep_cycle_scanner")
_sys.modules["app.scanners.social_signals"] = _importlib.import_module("app.domains.scanners.social_signals")
_sys.modules["app.scanners.social_velocity"] = _importlib.import_module("app.domains.scanners.social_velocity")
_sys.modules["app.scanners.static_analyzer"] = _importlib.import_module("app.domains.scanners.static_analyzer")
_sys.modules["app.scanners.wash_trading"] = _importlib.import_module("app.domains.scanners.wash_trading")
