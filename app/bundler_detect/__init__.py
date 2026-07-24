"""
Supply Manipulation / Bundler Detector
=======================================
Detects bundled token launches where insiders control disproportionate
supply through sniper-controlled wallet distributions.

Usage:
    from app.bundler_detect import BundlerDetector, BundlerReport
    from app.bundler_detect import label_risk, gini_coefficient
"""

from __future__ import annotations

from app.bundler_detect.detector import BundlerDetector
from app.bundler_detect.fetchers import (
    fetch_buys,
    fetch_holders,
    fetch_metadata,
)
from app.bundler_detect.models import (
    BUNDLER_SCORE_WEIGHTS,
    EVM_CHAINS,
    SUPPORTED_CHAINS,
    BundledBuy,
    BundlerReport,
    BundlerRisk,
    HolderCluster,
)
from app.bundler_detect.scoring import (
    entropy,
    gini_coefficient,
    label_risk,
    time_cluster_similarity,
)
from app.bundler_detect.scoring_methods import (
    compute_bundler_score,
    score_fund_flow,
    score_launch_timing,
    score_sniper_clusters,
    score_supply_concentration,
)

__all__ = [
    "BUNDLER_SCORE_WEIGHTS",
    "EVM_CHAINS",
    "SUPPORTED_CHAINS",
    "BundledBuy",
    "BundlerDetector",
    "BundlerReport",
    "BundlerRisk",
    "HolderCluster",
    "compute_bundler_score",
    "entropy",
    "fetch_buys",
    "fetch_holders",
    "fetch_metadata",
    "gini_coefficient",
    "label_risk",
    "score_fund_flow",
    "score_launch_timing",
    "score_sniper_clusters",
    "score_supply_concentration",
    "time_cluster_similarity",
]
