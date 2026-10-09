"""Lesson module content for the Advanced Forensics course."""

from __future__ import annotations

from . import (
    advanced_smart_contract_auditing,
    bridge_cross_chain_exploits,
    capstone_threat_intelligence_report,
    dark_web_offchain_intelligence,
    defi_protocol_risk_assessment,
    entity_attribution_clustering,
    flash_loan_attack_analysis,
    governance_attack_detection,
    mev_sandwich_attack_forensics,
    mixer_tumbler_detection,
    on_chain_fund_tracing,
    regulatory_compliance_landscape,
)

MODULES = [
    on_chain_fund_tracing.on_chain_fund_tracing,
    entity_attribution_clustering.entity_attribution_clustering,
    mixer_tumbler_detection.mixer_tumbler_detection,
    mev_sandwich_attack_forensics.mev_sandwich_attack_forensics,
    flash_loan_attack_analysis.flash_loan_attack_analysis,
    bridge_cross_chain_exploits.bridge_cross_chain_exploits,
    governance_attack_detection.governance_attack_detection,
    advanced_smart_contract_auditing.advanced_smart_contract_auditing,
    defi_protocol_risk_assessment.defi_protocol_risk_assessment,
    dark_web_offchain_intelligence.dark_web_offchain_intelligence,
    regulatory_compliance_landscape.regulatory_compliance_landscape,
    capstone_threat_intelligence_report.capstone_threat_intelligence_report,
]

__all__ = ["MODULES"]
