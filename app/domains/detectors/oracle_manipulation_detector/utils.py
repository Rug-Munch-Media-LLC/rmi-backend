"""
Oracle Manipulation Detector — Utility Functions
==================================================
Standalone heuristic helpers for quick checks and tests.
"""

from __future__ import annotations

import math

from .constants import MIN_TWAP_SAMPLES, TWAP_MANIPULATION_THRESHOLD_PCT
from .models import (
    ManipulationType,
    PriceManipulation,
    PriceSnapshot,
    Severity,
    TWAPWindow,
)


def detect_twap_manipulation(twap: TWAPWindow) -> PriceManipulation | None:
    """Quick TWAP manipulation check - usable without instantiating the detector."""
    risk = twap.manipulation_risk()
    if risk < TWAP_MANIPULATION_THRESHOLD_PCT * 2:
        return None

    severity = (
        Severity.CRITICAL
        if risk > 0.8
        else (Severity.HIGH if risk > 0.6 else (Severity.MEDIUM if risk > 0.4 else Severity.LOW))
    )

    return PriceManipulation(
        manipulation_type=ManipulationType.TWAP_POISONING,
        severity=severity,
        chain="ethereum",
        block_number=twap.end_block,
        pool_address=twap.pool_address,
        pair=twap.pair,
        oracle_type=twap.oracle_type,
        observed_price=twap.average_price,
        expected_price=twap.median_price,
        deviation_pct=twap.std_dev_pct * 100,
        description=f"TWAP manipulation risk: {risk:.1%} (std dev {twap.std_dev_pct:.2%})",
        evidence=[f"risk={risk:.4f}", f"samples={len(twap.samples)}"],
    )


def compute_deviation_pct(observed: float, expected: float) -> float:
    """Calculate percentage deviation between two prices."""
    if expected == 0:
        return 0.0
    return abs(observed - expected) / expected


def classify_severity(deviation_pct: float, is_flash_loan: bool = False) -> Severity:
    """Classify severity based on price deviation percentage."""
    if is_flash_loan:
        threshold_map = [
            (0.20, Severity.CRITICAL),
            (0.10, Severity.HIGH),
            (0.05, Severity.MEDIUM),
        ]
    else:
        threshold_map = [
            (0.30, Severity.CRITICAL),
            (0.15, Severity.HIGH),
            (0.08, Severity.MEDIUM),
            (0.03, Severity.LOW),
        ]

    for threshold, severity in threshold_map:
        if deviation_pct >= threshold:
            return severity
    return Severity.INFO


def is_sandwich_pattern(prices: list[float], window: int = 5) -> bool:
    """Detect sandwich-style price pattern: low → high → low or high → low → high."""
    if len(prices) < window * 2 + 1:
        return False

    mid = len(prices) // 2
    left_avg = sum(prices[:window]) / window
    mid_price = prices[mid]
    right_avg = sum(prices[-window:]) / window

    return (left_avg < mid_price * 0.95 and right_avg < mid_price * 0.95) or (
        left_avg > mid_price * 1.05 and right_avg > mid_price * 1.05
    )


def calculate_twap_from_samples(samples: list[PriceSnapshot]) -> TWAPWindow | None:
    """Compute TWAP statistics from a list of price samples."""
    if len(samples) < MIN_TWAP_SAMPLES:
        return None

    prices = [s.price for s in samples]
    avg_price = sum(prices) / len(prices)
    sorted_prices = sorted(prices)
    median_price = sorted_prices[len(sorted_prices) // 2]

    variance = sum((p - avg_price) ** 2 for p in prices) / len(prices)
    std_dev = math.sqrt(variance)
    std_dev_pct = std_dev / avg_price if avg_price > 0 else 0

    return TWAPWindow(
        start_block=samples[0].block_number,
        end_block=samples[-1].block_number,
        start_time=samples[0].timestamp,
        end_time=samples[-1].timestamp,
        average_price=round(avg_price, 6),
        median_price=round(median_price, 6),
        min_price=round(min(samples, key=lambda s: s.price).price, 6),
        max_price=round(max(samples, key=lambda s: s.price).price, 6),
        std_dev_pct=round(std_dev_pct, 6),
        samples=samples,
        pool_address=getattr(samples[0], "source", ""),
        pair="",
    )


__all__ = [
    "calculate_twap_from_samples",
    "classify_severity",
    "compute_deviation_pct",
    "detect_twap_manipulation",
    "is_sandwich_pattern",
]
