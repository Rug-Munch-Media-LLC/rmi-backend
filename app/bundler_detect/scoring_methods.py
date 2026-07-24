"""
Bundler Detector - Class-Level Scoring Methods
===============================================
Scoring methods extracted from BundlerDetector class.
These are pure functions; the detector passes parameters explicitly.
"""

from __future__ import annotations

from typing import Any

from app.bundler_detect.models import BUNDLER_SCORE_WEIGHTS, BundledBuy, BundlerReport, HolderCluster
from app.bundler_detect.scoring import entropy, gini_coefficient


def score_supply_concentration(holders: list[dict[str, Any]], top10_pct: float) -> float:
    """Score supply distribution risk (0-100)."""
    score = 0.0
    if top10_pct >= 90:
        score += 50
    elif top10_pct >= 75:
        score += 35
    elif top10_pct >= 50:
        score += 20
    elif top10_pct >= 30:
        score += 10

    amounts = [h.get("percentage", 0) for h in holders[:100] if h.get("percentage") is not None]
    gini_val = gini_coefficient(amounts)
    if gini_val >= 0.9:
        score += 40
    elif gini_val >= 0.8:
        score += 30
    elif gini_val >= 0.6:
        score += 15

    ent = entropy(amounts)
    if ent < 0.3:
        score += 15
    elif ent < 0.5:
        score += 8

    return min(score, 100)

def score_sniper_clusters(clusters: list[HolderCluster], bundled_buys: list[BundledBuy]) -> float:
    """Score sniper cluster risk (0-100)."""
    score = 0.0

    high_overlap = [c for c in clusters if c.funding_overlap_score > 0.6]
    if high_overlap:
        total_pct = sum(c.total_supply_pct for c in high_overlap)
        if total_pct >= 50:
            score += 50
        elif total_pct >= 30:
            score += 35
        elif total_pct >= 15:
            score += 20

    if bundled_buys:
        score += min(len(bundled_buys) * 5, 30)

    high_time = [c for c in clusters if c.buy_time_similarity > 0.7]
    if high_time:
        score += min(len(high_time) * 10, 25)

    return min(score, 100)

def score_launch_timing(
    timing_info: dict[str, Any],
    buys: list[dict[str, Any]],
    holders: list[dict[str, Any]],
) -> float:
    """Score launch timing anomalies (0-100)."""
    score = 0.0
    ratio = timing_info.get("buy_concentration_ratio", 0)
    if ratio >= 0.8:
        score += 50
    elif ratio >= 0.6:
        score += 35
    elif ratio >= 0.4:
        score += 20

    unique = timing_info.get("unique_buyers_first_block", 0)
    total = timing_info.get("total_buys_first_blocks", 0)
    if total > 0 and unique > 0:
        repeat_rate = total / max(1, unique)
        if repeat_rate >= 5:
            score += 30
        elif repeat_rate >= 3:
            score += 20

    holder_count = len(holders)
    if holder_count > 0 and total > 0:
        if total / holder_count >= 3:
            score += 15

    return min(score, 100)

def score_fund_flow(
    bundled_buys: list[BundledBuy],
    same_funding_count: int,
    clusters: list[HolderCluster],
) -> float:
    """Score fund flow risk (0-100)."""
    score = 0.0

    if same_funding_count >= 20:
        score += 45
    elif same_funding_count >= 10:
        score += 30
    elif same_funding_count >= 5:
        score += 15

    high_overlap = [c for c in clusters if c.funding_overlap_score > 0.7]
    if high_overlap:
        score += min(len(high_overlap) * 15, 30)

    if clusters:
        avg_overlap = sum(c.funding_overlap_score for c in clusters) / len(clusters)
        score += avg_overlap * 20

    return min(score, 100)

def compute_bundler_score(report: BundlerReport) -> float:
    """Weighted composite bundler score."""
    weights = BUNDLER_SCORE_WEIGHTS

    score = (
        report.supply_concentration_score * weights["supply_concentration"]
        + report.sniper_cluster_score * weights["sniper_cluster"]
        + report.launch_timing_anomaly_score * weights["launch_timing_anomaly"]
        + report.fund_flow_risk_score * weights["fund_flow_risk"]
    )

    return min(score, 100)
