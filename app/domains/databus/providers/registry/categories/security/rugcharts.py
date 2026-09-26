"""
Security provider chains — rugcharts_intel signal + analysis cluster.

Chains: smart_money, whale_alerts, insider_detection, liquidity_risk, holder_health.

All five read from a single optional module (``app.databus.rugcharts_intel``)
and share ONE availability probe (``token_launch_scanner``): a missing module
skips the whole cluster, exactly as the pre-split builder did. The identity /
reputation chains of the same optional module live in the sibling
``rugcharts_identity`` module. Split from ``security.py``.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

from ....base import Provider, ProviderChain, ProviderTier
from .rugcharts_identity import build_rugcharts_identity_chains

logger = get_logger("security")


def build_rugcharts_chains() -> dict[str, ProviderChain]:
    """Build every chain backed by ``app.databus.rugcharts_intel``.

    Returns an empty dict when the optional module is not importable, so the
    rest of the security registry still builds. Logging of that skip lives in
    :func:`_probe`; the rest of the cluster shares the single probe's verdict.
    """
    try:
        from app.databus import rugcharts_intel
    except ImportError as e:
        return _probe(e)
    # Bind by attribute: one import statement keeps every lookup a real module
    # attribute, so the type checker sees the declared types instead of an
    # unused import plus a pile of unknown-typed aliases.
    track_cross_chain_entity = rugcharts_intel.cross_chain_entity
    get_dev_reputation = rugcharts_intel.developer_reputation
    check_holder_health = rugcharts_intel.holder_health_score
    detect_insiders = rugcharts_intel.insider_pattern_detector
    get_enhanced_token_report = rugcharts_intel.instant_token_report
    analyze_liquidity_risk = rugcharts_intel.liquidity_risk_monitor
    detect_rug_patterns = rugcharts_intel.rug_pattern_matcher
    get_smart_money = rugcharts_intel.smart_money_feed
    get_whale_alerts = rugcharts_intel.whale_alert_stream
    chains: dict[str, ProviderChain] = {}
    chains.update(_signal_chains(get_smart_money, get_whale_alerts, detect_insiders))
    chains.update(_analysis_chains(analyze_liquidity_risk, check_holder_health))
    chains.update(
        build_rugcharts_identity_chains(
            track_cross_chain_entity,
            detect_rug_patterns,
            get_dev_reputation,
            get_enhanced_token_report,
        )
    )
    return chains


def _probe(err: ImportError) -> dict[str, ProviderChain]:
    """Log the skipped cluster and return no chains."""
    logger.debug(f"build_security_chains failed: {err}")
    return {}


def _signal_chains(smart_money: Any, whale_alerts: Any, insiders: Any) -> dict[str, ProviderChain]:
    """Live signals: smart money, whale alerts, insider accumulation."""
    return {
        "smart_money": ProviderChain(
            "smart_money",
            description="Smart money feed",
            providers=[
                Provider(
                    "smart_money_tracker",
                    ProviderTier.LOCAL,
                    smart_money,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        ),
        "whale_alerts": ProviderChain(
            "whale_alerts",
            description="Real-time whale transaction detection",
            providers=[
                Provider(
                    "whale_detector",
                    ProviderTier.LOCAL,
                    whale_alerts,
                    weight=10.0,
                    rate_limit_rps=10.0,
                )
            ],
        ),
        "insider_detection": ProviderChain(
            "insider_detection",
            description="Pre-pump accumulation pattern detection",
            providers=[
                Provider(
                    "insider_detector",
                    ProviderTier.LOCAL,
                    insiders,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        ),
    }


def _analysis_chains(liquidity: Any, holders: Any) -> dict[str, ProviderChain]:
    """LP health monitor + holder distribution analysis."""
    return {
        "liquidity_risk": ProviderChain(
            "liquidity_risk",
            description="LP health monitor",
            providers=[
                Provider(
                    "liquidity_monitor",
                    ProviderTier.LOCAL,
                    liquidity,
                    weight=10.0,
                    rate_limit_rps=10.0,
                )
            ],
        ),
        "holder_health": ProviderChain(
            "holder_health",
            description="Holder distribution analysis",
            providers=[
                Provider(
                    "holder_analyzer",
                    ProviderTier.LOCAL,
                    holders,
                    weight=10.0,
                    rate_limit_rps=10.0,
                )
            ],
        ),
    }
