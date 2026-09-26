"""
Security provider chains
========================

Security-domain provider chain builders, grouped into five focused modules
that each stay under the 200-line / 50-line-function limits:

===========================  ================================================
Module                       Chains
===========================  ================================================
``core``                     url_security_scan, scanner, scam_database,
                             spl_token_metadata
``token_security``           volume_authenticity, token_security,
                             token_launches, token_security_free
``rugcharts``                smart_money, whale_alerts, insider_detection,
                             liquidity_risk, holder_health, cross_chain_entity,
                             rug_patterns, dev_reputation, token_report
``chain_indexer``            lite_chain_indexer_patterns, deployer_reputation,
                             deployer_fingerprint, whale_detection,
                             volume_authenticity, holder_age, holder_clusters,
                             attack_detection, new_token_radar
``composite``                tier_comparison, threat_check, contract_scan,
                             sentinel_deep, rugmap
===========================  ================================================

``build_security_chains`` keeps the original single-entry signature (it is
invoked as ``build_security_chains(locals())`` by the registry) and the
original registration order of every chain.

Premium chains (premium scanner, token_security, volume_authenticity,
rugcharts_intel) are ALSO registered in ``_generated/chains/premium.py``
via ``build_premium_chains`` — kept here for backward compatibility until
the registry is fully deduplicated.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .chain_indexer import build_chain_indexer_chains
from .composite import build_composite_chains
from .core import build_core_chains
from .rugcharts import build_rugcharts_chains
from .token_security import build_token_security_chains

if TYPE_CHECKING:
    from ....base import ProviderChain


def build_security_chains(locals_dict: dict[str, Any]) -> dict[str, ProviderChain]:
    """Build security provider chains.

    ``locals_dict`` is the registry's own frame (``locals()``), which carries
    every provider function as a name — the builders read what they need from
    it and never import the chains directly. The optional databus modules
    (volume_authenticity, token_security, rugcharts_intel, data_quality) are
    imported inside their own builder so an absent module skips only its own
    chains instead of breaking the registry.
    """
    chains: dict[str, ProviderChain] = {}
    chains.update(build_core_chains(locals_dict))
    chains.update(build_token_security_chains(locals_dict))
    chains.update(build_rugcharts_chains())
    chains.update(build_chain_indexer_chains(locals_dict))
    chains.update(build_composite_chains(locals_dict))
    return chains


__all__ = [
    "build_chain_indexer_chains",
    "build_composite_chains",
    "build_core_chains",
    "build_rugcharts_chains",
    "build_security_chains",
    "build_token_security_chains",
]
