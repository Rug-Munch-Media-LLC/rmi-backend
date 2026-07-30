"""
Cross-Chain Entity Tracking Package
=====================================
Links wallets across blockchains using:
  1. Same-deployer detection (deployer active on 2+ chains)
  2. EVM address portability (same address across EVM chains)
  3. Bridge pattern detection (deposit/withdraw through bridges)
  4. Behavioral fingerprinting (same trading patterns)
  5. Fund flow tracing (follow funds between chains)

Architecture:
  chain-indexer deployer_events → ClickHouse → deployer_matcher.py
  → Neo4j SAME_AS edges → entity_resolver.py resolves entities
"""

from __future__ import annotations

from .deployer_matcher import DeployerMatcher
from .entity_tracker import CrossChainEntityTracker, get_entity_tracker
from .models import CrossChainMatch, CrossChainProfile

__all__ = [
    "CrossChainEntityTracker",
    "CrossChainMatch",
    "CrossChainProfile",
    "DeployerMatcher",
    "get_entity_tracker",
]
