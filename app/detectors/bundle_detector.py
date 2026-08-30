"""
Bundle Detection Engine - Atomic block co-occurrence analysis.
Detects Jito bundles, Flashbots bundles, and coordinated launches.
Implements: atomic-block grouping, common funder, temporal clustering,
distribution anomaly detection, holder concentration scoring.

References:
- Section 2.2, Bundle Detection Heuristics
- HNUT: 78% early activity was bundled transactions
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class BundleDetection:
    """Result of bundle detection on a token."""

    token_address: str
    chain: str
    is_bundled: bool
    confidence: float  # 0-1

    # Individual signals
    atomic_block_score: float = 0.0
    common_funder_score: float = 0.0
    temporal_score: float = 0.0
    distribution_anomaly_score: float = 0.0
    concentration_score: float = 0.0

    # Details
    earliest_block: int | None = None
    wallets_in_earliest_block: int = 0
    common_funder_address: str | None = None
    funded_wallets_count: int = 0
    time_window_seconds: float = 0
    identical_amount_count: int = 0
    round_amount_count: int = 0
    top10_holder_pct: float = 0.0
    top3_holder_pct: float = 0.0
    holder_count: int = 0

    risk_label: str = "unknown"  # critical, high, medium, low


class BundleDetector:
    """Multi-signal bundle detection for Solana tokens."""

    def __init__(self) -> None:
        self.min_holders_for_analysis = 5

    async def detect(
        self,
        token_address: str,
        chain: str = "solana",
        holders: list[dict[str, Any]] | None = None,
        transactions: list[dict[str, Any]] | None = None,
    ) -> BundleDetection:
        """Run all bundle detection signals and return combined result."""
        result = BundleDetection(token_address=token_address, chain=chain, is_bundled=False, confidence=0.0)

        if not holders or len(holders) < self.min_holders_for_analysis:
            result.risk_label = "unknown"
            return result

        result.holder_count = len(holders)

        # 1. Atomic block co-occurrence
        if transactions:
            self._atomic_block_signal(result, transactions)

        # 2. Common funding source
        if transactions:
            self._common_funder_signal(result, transactions)

        # 3. Temporal clustering
        if transactions:
            self._temporal_signal(result, transactions)

        # 4. Distribution anomaly detection
        self._distribution_anomaly_signal(result, holders)

        # 5. Holder concentration
        self._concentration_signal(result, holders)

        # Combine signals into final score
        result.confidence = self._combined_score(result)
        result.is_bundled = result.confidence >= 0.5
        result.risk_label = self._risk_label(result.confidence)

        return result

    def _atomic_block_signal(self, result: BundleDetection, txs: list[dict[str, Any]]) -> None:
        """Check if many holders acquired tokens in the same block (atomic bundle)."""
        block_wallets = defaultdict(set)
        for tx in txs:
            block = tx.get("blockNumber") or tx.get("slot")
            wallet = tx.get("from") or tx.get("signer") or tx.get("wallet")
            if block and wallet:
                block_wallets[block].add(wallet)

        if not block_wallets:
            return

        # Find block with most wallet activity
        max_block = max(block_wallets, key=lambda b: len(block_wallets[b]))
        max_wallets = len(block_wallets[max_block])

        result.earliest_block = max_block
        result.wallets_in_earliest_block = max_wallets

        if max_wallets >= 10:
            result.atomic_block_score = 0.9
        elif max_wallets >= 5:
            result.atomic_block_score = 0.7
        elif max_wallets >= 3:
            result.atomic_block_score = 0.4
        else:
            result.atomic_block_score = 0.1

    def _common_funder_signal(self, result: BundleDetection, txs: list[dict[str, Any]]) -> None:
        """Detect if multiple buyers were funded from the same source wallet."""
        funder_counts: defaultdict[str, int] = defaultdict(int)
        for tx in txs:
            funder = tx.get("from") or tx.get("signer")
            recipient = tx.get("to") or tx.get("recipient")
            if funder and recipient and funder != recipient:
                funder_counts[funder] += 1

        if not funder_counts:
            return

        top_funder = max(funder_counts, key=lambda k: funder_counts[k])
        top_count = funder_counts[top_funder]

        result.common_funder_address = top_funder
        result.funded_wallets_count = top_count

        if top_count >= 20:
            result.common_funder_score = 0.9
        elif top_count >= 10:
            result.common_funder_score = 0.7
        elif top_count >= 5:
            result.common_funder_score = 0.5
        elif top_count >= 3:
            result.common_funder_score = 0.3

    def _temporal_signal(self, result: BundleDetection, txs: list[dict[str, Any]]) -> None:
        """Check if wallets appeared within a narrow time window."""
        timestamps = []
        for tx in txs:
            ts = tx.get("timestamp") or tx.get("blockTime")
            if ts:
                timestamps.append(ts)

        if len(timestamps) < 2:
            return

        timestamps.sort()
        window = timestamps[-1] - timestamps[0]
        result.time_window_seconds = window

        if window <= 60:  # All within 1 minute
            result.temporal_score = 0.9
        elif window <= 300:  # 5 minutes
            result.temporal_score = 0.7
        elif window <= 900:  # 15 minutes
            result.temporal_score = 0.5
        elif window <= 3600:  # 1 hour
            result.temporal_score = 0.3

    def _distribution_anomaly_signal(self, result: BundleDetection, holders: list[dict[str, Any]]) -> None:
        """Check for flat/rounded amounts - hallmark of bundled distribution."""
        amounts = []
        for h in holders:
            amt = h.get("amount", 0)
            if isinstance(amt, (int, float)) and amt > 0:
                amounts.append(amt)

        if not amounts:
            return

        # Identical amounts
        from collections import Counter

        amount_counts = Counter(amounts)
        identical = sum(1 for count in amount_counts.values() if count >= 3)
        result.identical_amount_count = identical

        # Round number amounts (multiples of 100, 1000, 10000)
        round_count = sum(1 for a in amounts if a >= 100 and (a % 100 == 0 or a % 1000 == 0 or a % 10000 == 0))
        result.round_amount_count = round_count

        identical_pct = identical / len(amounts) if amounts else 0
        round_pct = round_count / len(amounts) if amounts else 0

        # Combine
        anomaly_pct = max(identical_pct, round_pct)
        if anomaly_pct > 0.5:
            result.distribution_anomaly_score = 0.9
        elif anomaly_pct > 0.3:
            result.distribution_anomaly_score = 0.7
        elif anomaly_pct > 0.15:
            result.distribution_anomaly_score = 0.5
        elif anomaly_pct > 0.05:
            result.distribution_anomaly_score = 0.3

    def _concentration_signal(self, result: BundleDetection, holders: list[dict[str, Any]]) -> None:
        """Check top-10 and top-3 holder concentration."""
        amounts = []
        for h in holders:
            amt = h.get("amount", 0)
            if isinstance(amt, (int, float)) and amt > 0:
                amounts.append(amt)

        if not amounts:
            return

        amounts.sort(reverse=True)
        total = sum(amounts)

        top3 = sum(amounts[:3]) / total if total > 0 else 0
        top10 = sum(amounts[: min(10, len(amounts))]) / total if total > 0 else 0

        result.top3_holder_pct = round(top3 * 100, 1)
        result.top10_holder_pct = round(top10 * 100, 1)

        if top3 > 0.5 or top10 > 0.8:
            result.concentration_score = 0.9
        elif top3 > 0.3 or top10 > 0.6:
            result.concentration_score = 0.7
        elif top3 > 0.15 or top10 > 0.4:
            result.concentration_score = 0.4
        elif top3 > 0.05:
            result.concentration_score = 0.2

    def _combined_score(self, r: BundleDetection) -> float:
        """Weighted combination of all signals."""
        scores = [
            (r.atomic_block_score, 0.30),  # Atomic block is strongest signal
            (r.common_funder_score, 0.25),  # Common funder second strongest
            (r.temporal_score, 0.15),
            (r.distribution_anomaly_score, 0.20),
            (r.concentration_score, 0.10),
        ]
        weighted = sum(s * w for s, w in scores)
        # Boost if multiple strong signals
        strong_signals = sum(1 for s, _ in scores if s >= 0.7)
        if strong_signals >= 3:
            weighted = min(1.0, weighted * 1.3)
        elif strong_signals >= 2:
            weighted = min(1.0, weighted * 1.15)
        return round(weighted, 4)

    def _risk_label(self, confidence: float) -> str:
        if confidence >= 0.8:
            return "critical"
        elif confidence >= 0.6:
            return "high"
        elif confidence >= 0.4:
            return "medium"
        return "low"


# Singleton
_detector: BundleDetector | None = None


def get_bundle_detector() -> BundleDetector:
    global _detector
    if _detector is None:
        _detector = BundleDetector()
    return _detector
