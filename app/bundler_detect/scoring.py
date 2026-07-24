"""
Bundler Detector - Scoring Utilities
=====================================
Pure scoring functions for supply concentration, time clustering,
funding overlap, entropy, and risk labeling.

These are standalone functions (no class dependency) used by the detector.
"""

import math
from typing import Any


def gini_coefficient(values: list[float]) -> float:
    """Compute Gini coefficient (0=equal, 1=max concentration)."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    cumulative = 0.0
    for i, v in enumerate(sorted_vals):
        cumulative += (i + 1) * v
    gini = (2 * cumulative) / (n * sum(sorted_vals)) - (n + 1) / n
    return max(0.0, min(gini, 1.0))


def entropy(values: list[float]) -> float:
    """Shannon entropy, normalized [0, 1]. 1 = uniform, 0 = concentrated."""
    total = sum(values)
    if total <= 0:
        return 0.0
    n = len(values)
    if n <= 1:
        return 1.0
    raw = 0.0
    for v in values:
        p = v / total
        if p > 0:
            raw -= p * math.log2(p)
    max_entropy = math.log2(n)
    return raw / max_entropy if max_entropy > 0 else 0.0


def time_cluster_similarity(timestamps: list[float]) -> float:
    """Score how tightly clustered timestamps are (0=spread, 1=all at once)."""
    if len(timestamps) < 2:
        return 0.0
    min_ts = min(timestamps)
    max_ts = max(timestamps)
    span = max_ts - min_ts
    if span == 0:
        return 1.0
    if span <= 60:
        return 1.0 - (span / 60) * 0.5
    if span <= 300:
        return 0.5 - (span - 60) / (300 - 60) * 0.3
    return max(0.0, 0.2 - (span - 300) / 3600)


def funding_overlap(funding_sources: list[str]) -> float:
    """Score how many wallets share the same funding source (0-1)."""
    if not funding_sources:
        return 0.0
    total = len(funding_sources)
    if total < 2:
        return 0.0
    from collections import Counter
    source_counts = Counter(funding_sources)
    shared = sum(c for c in source_counts.values() if c > 1)
    return shared / total


def label_risk(score: float) -> str:
    """Convert numeric score to risk label."""
    if score >= 75:
        return "critical"
    if score >= 50:
        return "high"
    if score >= 25:
        return "medium"
    if score > 0:
        return "low"
    return "none"
