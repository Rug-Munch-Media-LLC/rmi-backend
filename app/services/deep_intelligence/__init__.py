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

# Core infrastructure (safe — no side-effect imports)
from app.services.deep_intelligence.core import (
    DeepIntelConfig,
    MultiChainClient,
    cfg,
    client,
    logger,
)

# Analyzers (safe — only depend on core)
from app.services.deep_intelligence.analyzers.deployer import deployer_reputation
from app.services.deep_intelligence.analyzers.preflight import preflight_simulate
from app.services.deep_intelligence.analyzers.liquidity import liquidity_lock_authenticity
from app.services.deep_intelligence.analyzers.volume import volume_authenticity
from app.services.deep_intelligence.analyzers.slow_rug import slow_rug_detector
from app.services.deep_intelligence.analyzers.funding import funding_source_tracer
from app.services.deep_intelligence.analyzers.ownership import ownership_theater_detector
from app.services.deep_intelligence.analyzers.exit_liquidity import exit_liquidity_calculator
from app.services.deep_intelligence.analyzers.lifespan import token_lifespan_predictor
from app.services.deep_intelligence.analyzers.monitor import post_purchase_monitor
from app.services.deep_intelligence.analyzers.cross_chain import cross_chain_identity
from app.services.deep_intelligence.analyzers.social import social_onchain_correlation
from app.services.deep_intelligence.analyzers.threat_intel import threat_intel_check

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
    from app.services.deep_intelligence.processors.composite import full_deep_intel as _impl  # noqa: PLC0415
    return await _impl(*args, **kwargs)

__all__ = [
    # Core
    "DeepIntelConfig",
    "MultiChainClient",
    "cfg",
    "client",
    "logger",
    # Analyzers
    "deployer_reputation",
    "preflight_simulate",
    "liquidity_lock_authenticity",
    "volume_authenticity",
    "slow_rug_detector",
    "funding_source_tracer",
    "ownership_theater_detector",
    "exit_liquidity_calculator",
    "token_lifespan_predictor",
    "post_purchase_monitor",
    "cross_chain_identity",
    "social_onchain_correlation",
    "threat_intel_check",
    # Composite (lazy)
    "full_deep_intel",
    # Models
    "JsonDict",
    "RiskFlags",
    "RiskLevel",
]
