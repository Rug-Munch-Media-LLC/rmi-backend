"""
SENTINEL - Risk Score Extraction & Composite Scoring
======================================================
Helper functions to extract risk scores from module reports,
compute the weighted composite score, and collect red flags.
"""

from __future__ import annotations

from app.domains.scanners.bundle_detector import BundleReport
from app.domains.scanners.dev_reputation import DevReputationReport
from app.domains.scanners.exchange_funder import ExchangeFundReport
from app.domains.scanners.guilt_association import GuiltAssociationReport
from app.domains.scanners.holder_analyzer import HHIReport
from app.domains.scanners.liquidity_verifier import LockReport
from app.domains.scanners.metadata_fingerprint import FingerprintReport
from app.domains.scanners.pumpfun_analyzer import PumpFunReport
from app.domains.scanners.sentiment_analyzer import SentimentReport
from app.domains.scanners.wash_trading import WashTradingReport


def _extract_risk_hhi(report: HHIReport) -> float:
    """Map HHI value to 0-100 risk score."""
    hhi = report.hhi
    if hhi < 1500:
        return min(hhi / 1500 * 30, 30)
    elif hhi < 3000:
        return 30 + (hhi - 1500) / 1500 * 30
    elif hhi < 5000:
        return 60 + (hhi - 3000) / 2000 * 20
    else:
        return min(80 + (hhi - 5000) / 5000 * 20, 100)

def _extract_risk_bundle(report: BundleReport) -> float:
    """Bundle report risk score - use bundle_risk_score if available, else heuristic."""
    if hasattr(report, "risk_score"):
        return float(report.risk_score)
    bundle_count = len(report.bundles) if hasattr(report, "bundles") and report.bundles else 0
    return min(bundle_count * 15, 100)

def _extract_risk_exchange_fund(report: ExchangeFundReport) -> float:
    """Exchange funding risk - higher CEX-funded ratio = more suspicious."""
    if hasattr(report, "cex_funded_percentage"):
        return min(float(report.cex_funded_percentage), 100)
    if hasattr(report, "risk_score"):
        return float(report.risk_score)
    return 0.0

def _extract_risk_lock(report: LockReport) -> float:
    """Lock risk - unlocked/no lock = 80, locked = low risk."""
    if hasattr(report, "risk_score"):
        return float(report.risk_score)
    if hasattr(report, "is_locked") and not report.is_locked:
        return 80.0
    return 10.0

def _extract_risk_dev(report: DevReputationReport) -> float:
    """Dev reputation risk - serial ruggers = high risk."""
    if hasattr(report, "risk_score"):
        return float(report.risk_score)
    if hasattr(report, "rug_count"):
        return min(int(report.rug_count) * 25, 100)
    return 0.0

def _extract_risk_wash_trading(report: WashTradingReport) -> float:
    """Wash trading risk score."""
    if hasattr(report, "risk_score"):
        return float(report.risk_score)
    if hasattr(report, "wash_trading_probability"):
        return float(report.wash_trading_probability) * 100
    return 0.0

def _extract_risk_fingerprint(report: FingerprintReport) -> float:
    """Metadata fingerprint risk - high similarity = higher risk."""
    if hasattr(report, "risk_score"):
        return float(report.risk_score)
    if hasattr(report, "max_similarity"):
        return float(report.max_similarity) * 100
    return 0.0

def _extract_risk_sentiment(report: SentimentReport) -> float:
    """Sentiment risk - extreme hype = risky."""
    if hasattr(report, "risk_score"):
        return float(report.risk_score)
    return 30.0

def _extract_risk_pumpfun(report: PumpFunReport) -> float:
    """PumpFun risk - low graduation probability or high bot ratio = risky."""
    if hasattr(report, "risk_score"):
        return float(report.risk_score)
    if hasattr(report, "graduation_probability"):
        return (1.0 - float(report.graduation_probability)) * 100
    return 30.0

def _extract_risk_guilt_association(report: GuiltAssociationReport) -> float:
    """Guilt association risk - use guilt_score directly."""
    if hasattr(report, "guilt_score"):
        return float(report.guilt_score)
    if hasattr(report, "risk_score"):
        return float(report.risk_score)
    return 0.0

def _extract_risk_sleep_cycle(report) -> float:
    """Sleep cycle risk - use sleep_score directly."""
    if hasattr(report, "sleep_score"):
        return float(report.sleep_score)
    return 0.0

def _extract_risk_social_velocity(report) -> float:
    """Social velocity risk - use velocity_score directly."""
    if hasattr(report, "velocity_score"):
        return float(report.velocity_score)
    return 0.0

def _extract_risk_bytecode_similarity(report) -> float:
    """Bytecode similarity risk - exact match = 100."""
    if hasattr(report, "exact_match") and report.exact_match:
        return 100.0
    if hasattr(report, "similarity_score"):
        return float(report.similarity_score)
    return 0.0

def _extract_risk_block_zero(report) -> float:
    """Block zero sniper risk - farm confidence * 100."""
    if hasattr(report, "farm_confidence"):
        return float(report.farm_confidence) * 100
    return 0.0

def _extract_risk_gas_trace(report) -> float:
    """Gas trace - dead end = 100, cex funded = 0."""
    if hasattr(report, "risk_score"):
        return float(report.risk_score)
    if hasattr(report, "dead_end") and report.dead_end:
        return 100.0
    if hasattr(report, "cex_funded") and report.cex_funded:
        return 10.0
    return 30.0

def _compute_composite(scores: dict[str, float]) -> tuple:
    """Weighted composite risk score -> (score, level).

    Weights reflect each module's importance for rug-pull detection.
    """
    weights = {
        "holder_analysis": 0.20,
        "bundle_detection": 0.20,
        "exchange_funding": 0.10,
        "liquidity_verification": 0.15,
        "dev_reputation": 0.15,
        "wash_trading": 0.08,
        "metadata_fingerprint": 0.04,
        "sentiment": 0.04,
        "pumpfun_analysis": 0.04,
        "guilt_association": 0.06,
        "sleep_cycle": 0.04,
        "social_velocity": 0.03,
        "bytecode_similarity": 0.08,
        "block_zero_sniper": 0.08,
        "gas_trace": 0.06,
    }
    total = 0.0
    weight_used = 0.0
    for module, score in scores.items():
        w = weights.get(module, 0.05)
        total += score * w
        weight_used += w

    composite = total / weight_used if weight_used > 0 else 0.0

    if composite < 25:
        level = "low"
    elif composite < 50:
        level = "moderate"
    elif composite < 75:
        level = "high"
    else:
        level = "critical"

    return round(composite, 1), level

def _collect_red_flags(report_dict: dict | None, module_name: str) -> list[str]:
    """Pull top red-flag strings from each module's dict result."""
    flags: list[str] = []
    if not report_dict:
        return flags

    for key in ("red_flags", "warnings", "flags", "risks"):
        items = report_dict.get(key, [])
        if isinstance(items, (list, tuple)):
            for item in items[:5]:
                text = str(item) if not isinstance(item, str) else item
                flags.append(f"[{module_name}] {text}")

    rl = report_dict.get("risk_level", "")
    if rl in ("high", "critical"):
        flags.append(f"[{module_name}] risk_level={rl}")
    return flags
