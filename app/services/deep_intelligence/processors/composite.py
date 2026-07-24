"""

Composite Deep Intelligence Report
===================================
Run ALL 12+ features and produce a composite report.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.sweep_all import results

from app.core.logging import get_logger
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
from app.services.deep_intelligence.core import logger

logger = get_logger("deep_intel")

async def full_deep_intel(
    token_address: str, chain: str = "sol", wallet: str | None = None
) -> dict[str, Any]:
    """
    Run ALL 12 features and produce a composite deep intelligence report.
    """
    logger.info(f"Running full deep intel for {token_address} on {chain}")

    # Run all analyses in parallel
    [
        deployer_reputation(token_address, chain),
        preflight_simulate(
            token_address, wallet or "0x0000000000000000000000000000000000000000", 100, chain
        ),
        liquidity_lock_authenticity(token_address, chain),
        volume_authenticity(token_address, chain),
        slow_rug_detector(token_address, chain),
        funding_source_tracer(token_address, chain),
        ownership_theater_detector(token_address, chain),
        exit_liquidity_calculator(token_address, 1000, chain),
        token_lifespan_predictor(token_address, chain),
        post_purchase_monitor(token_address, chain),
        cross_chain_identity(token_address, chain),
        social_onchain_correlation(token_address, chain),
        threat_intel_check(token_address, chain),
    ]

    feature_names = [
        "deployer_reputation",
        "preflight_simulation",
        "liquidity_lock_authenticity",
        "volume_authenticity",
        "slow_rug_detector",
        "funding_source_tracer",
        "ownership_theater",
        "exit_liquidity",
        "lifespan_predictor",
        "post_purchase_monitor",
        "cross_chain_identity",
        "social_onchain_correlation",
        "threat_intel_check",
    ]

    report = {
        "token": token_address,
        "chain": chain,
        "timestamp": datetime.now(UTC).isoformat(),
        "features": {},
        "composite_score": 0,
        "composite_risk_level": "UNKNOWN",
        "summary": "",
        "top_flags": [],
    }

    all_flags = []
    scores = []

    for name, result in zip(feature_names, results, strict=False):
        if isinstance(result, Exception):
            report["features"][name] = {"error": str(result)}
        else:
            report["features"][name] = result

            if name == "deployer_reputation" and "risk_score" in result:
                scores.append(result["risk_score"])
            elif name == "volume_authenticity" and "wash_score" in result:
                scores.append(result["wash_score"])
            elif name == "slow_rug_detector" and "slow_rug_score" in result:
                scores.append(result["slow_rug_score"])
            elif name == "ownership_theater" and "theater_score" in result:
                scores.append(result["theater_score"])
            elif name == "lifespan_predictor" and "rug_probability_pct" in result:
                scores.append(result["rug_probability_pct"])

            for flag_key in ["flag_details", "flags", "risk_flags", "patterns", "alerts"]:
                if flag_key in result:
                    items = result[flag_key]
                    if isinstance(items, list):
                        for item in items:
                            if isinstance(item, dict):
                                all_flags.append(item.get("message", str(item)))
                            else:
                                all_flags.append(str(item))

    composite = sum(scores) / max(len(scores), 1) if scores else 0
    report["composite_score"] = round(composite, 1)
    report["composite_risk_level"] = (
        "EXTREME_DANGER"
        if composite >= 80
        else "HIGH_RISK"
        if composite >= 60
        else "ELEVATED_RISK"
        if composite >= 40
        else "MODERATE"
        if composite >= 25
        else "LOW_RISK"
        if composite >= 10
        else "SAFE"
    )

    unique_flags = list(dict.fromkeys(all_flags))[:10]
    report["top_flags"] = unique_flags
    report["summary"] = (
        f"Composite risk: {composite:.0f}/100. {len(unique_flags)} flag(s) detected across 12 intelligence modules."
    )

    return report
