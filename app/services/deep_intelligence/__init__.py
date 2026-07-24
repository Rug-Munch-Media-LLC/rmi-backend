"""
Deep Intelligence Engine
=========================
12 Unaccounted Features: Deployer Reputation Score, Pre-flight Simulator,
Liquidity Lock Authenticity, Volume Authenticity Engine, Slow Rug Detector,
Funding Source Tracer, Ownership Theater Detector, Exit Liquidity Calculator,
Token Lifespan Predictor, Post-Purchase Monitor, Cross-Chain Identity Resolution,
Social-Onchain Correlation + Composite Report.

Backward-compatible re-export layer — imports from sub-modules.
"""

from __future__ import annotations

from typing import Any

from app.services.deep_intelligence.analyzers.cross_chain import cross_chain_identity

# Analyzers (safe — only depend on core)
from app.services.deep_intelligence.analyzers.deployer import deployer_reputation
from app.services.deep_intelligence.analyzers.exit_liquidity import exit_liquidity_calculator
from app.services.deep_intelligence.analyzers.funding import funding_source_tracer
from app.services.deep_intelligence.analyzers.lifespan import token_lifespan_predictor
from app.services.deep_intelligence.analyzers.liquidity import liquidity_lock_authenticity
from app.services.deep_intelligence.analyzers.monitor import post_purchase_monitor
from app.services.deep_intelligence.analyzers.ownership import ownership_theater_detector
from app.services.deep_intelligence.analyzers.preflight import preflight_simulate
from app.services.deep_intelligence.analyzers.slow_rug import slow_rug_detector
from app.services.deep_intelligence.analyzers.social import social_onchain_correlation
from app.services.deep_intelligence.analyzers.threat_intel import threat_intel_check
from app.services.deep_intelligence.analyzers.volume import volume_authenticity

# Core infrastructure (safe — no side-effect imports)
from app.services.deep_intelligence.core import (
    DeepIntelConfig,
    MultiChainClient,
    cfg,
    client,
    logger,
)

# Models (safe)
from app.services.deep_intelligence.models import (
    JsonDict,
    RiskFlags,
    RiskLevel,
)


# full_deep_intel — lazy wrapper to avoid triggering sweep_all at import time.
# The original deep_intelligence.py had `from app.sweep_all import results` at
# the top, which has the same pre-existing transitive import issue.
# This lazy approach preserves backward compatibility for callers while keeping
# imports of core/analyzers/models working perfectly.
async def full_deep_intel(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Composite deep intelligence report — lazily imported."""
    from app.services.deep_intelligence.processors.composite import full_deep_intel as _impl
    return await _impl(*args, **kwargs)

__all__ = [
    # Core
    "DeepIntelConfig",
    # Models
    "JsonDict",
    "MultiChainClient",
    "RiskFlags",
    "RiskLevel",
    "cfg",
    "client",
    "cross_chain_identity",
    # Analyzers
    "deployer_reputation",
    "exit_liquidity_calculator",
    # Composite (lazy)
    "full_deep_intel",
    "funding_source_tracer",
    "liquidity_lock_authenticity",
    "logger",
    "ownership_theater_detector",
    "post_purchase_monitor",
    "preflight_simulate",
    "slow_rug_detector",
    "social_onchain_correlation",
    "threat_intel_check",
    "token_lifespan_predictor",
    "volume_authenticity",
]
