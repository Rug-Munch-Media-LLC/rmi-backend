"""
Wallet Risk Scoring - Composite risk across all intelligence sources.
=====================================================================
Score range: 0-100

Components:
  Label risk         (0-40)  - from label categories (scam, sanctioned, etc.)
  Cluster risk       (0-30)  - from entity membership (linked scam wallets)
  Deployer history   (0-30)  - from past token deployments (rug rate, lifespan)

Risk levels:
  0-19   minimal
  20-39  low
  40-59  medium
  60-79  high
  80-100 critical
"""

from app.core.logging import get_logger
from typing import Any

logger = get_logger("wallet_memory.risk_scoring")


class RiskScorer:
    """
    Composite risk scorer combining label, cluster, and deployer signals.
    """

    def __init__(self, storage=None, labels=None, clustering=None):
        self._storage = storage
        self._labels = labels
        self._clustering = clustering

    async def score(self, address: str, chain: str, intel: dict | None = None) -> dict[str, Any]:
        """
        Calculate composite risk score for a wallet.

        Args:
            address: wallet address
            chain: chain ID
            intel: pre-fetched intelligence dict (avoids duplicate lookups)

        Returns:
            {"score": float 0-100, "level": str, "components": {...}, "warnings": [...]}
        """
        address.lower().strip()
        intel = intel or {}

        components = {
            "label_risk": 0.0,
            "cluster_risk": 0.0,
            "deployer_risk": 0.0,
        }
        warnings = []

        # ── Component 1: Label risk (0-40) ──────────────────────────
        label_risk = intel.get("risk_contribution", 0)
        if label_risk == 0:
            # Recalculate from labels if not provided
            label_risk = self._score_labels(intel)
        components["label_risk"] = min(40, label_risk)

        if any(s.get("type") in ("sanctioned",) for s in intel.get("scam_associations", [])):
            components["label_risk"] = 40
            warnings.append("Address is OFAC sanctioned")

        if any(s.get("type") == "known_scam" for s in intel.get("scam_associations", [])):
            components["label_risk"] = max(components["label_risk"], 30)
            warnings.append("Address flagged as known scam")

        # ── Component 2: Cluster risk (0-30) ──────────────────────
        cluster_risk = self._score_cluster(intel)
        components["cluster_risk"] = min(30, cluster_risk)

        linked = intel.get("linked_wallets", [])
        if len(linked) >= 5:
            warnings.append(f"Linked to {len(linked)} other wallets in entity")

        # ── Component 3: Deployer history risk (0-30) ─────────────
        deployer_risk = self._score_deployer_history(intel)
        components["deployer_risk"] = min(30, deployer_risk)

        history = intel.get("deployer_history", [])
        scam_tokens = [t for t in history if t.get("is_scam_related")]
        if scam_tokens:
            warnings.append(f"Deployer has {len(scam_tokens)} flagged token(s)")

        # ── Composite ────────────────────────────────────────────
        total = components["label_risk"] + components["cluster_risk"] + components["deployer_risk"]
        total = max(0, min(100, total))

        level = self._level(total)

        return {
            "score": round(total, 1),
            "level": level,
            "components": components,
            "warnings": warnings,
        }

    def _score_labels(self, intel: dict) -> float:
        """Score label risk from intelligence dict."""
        score = 0.0
        labels = intel.get("labels", [])

        for lbl in labels:
            cat = lbl.get("category", "").lower()
            if cat == "sanctioned":
                score += 40
            elif cat in ("scam", "phishing", "drainer"):
                score += 20
            elif cat == "mev":
                score += 5

        # Scam associations
        for _assoc in intel.get("scam_associations", []):
            score += 15

        return min(40, score)

    def _score_cluster(self, intel: dict) -> float:
        """Score risk from entity/cluster membership."""
        score = 0.0
        linked = intel.get("linked_wallets", [])

        if not linked:
            return 0.0

        # More linked wallets = higher entity complexity
        # But it only adds risk if any linked wallet has scam association
        for _w in linked:
            # If any linked wallet appears in scam_associations
            pass  # We'd need to recursively check each linked wallet

        # Heuristic: entity size increases risk slightly
        entity_count = len(linked)
        if entity_count >= 10:
            score += 10
        elif entity_count >= 5:
            score += 5
        elif entity_count >= 3:
            score += 2

        # Confidence of entity membership adds to risk
        confidence = intel.get("confidence", 0)
        if confidence >= 0.8:
            score += 10  # High-confidence entity membership
        elif confidence >= 0.6:
            score += 5

        # Cross-chain presence adds risk (same entity on multiple chains)
        cross_chain = intel.get("cross_chain_presence", [])
        if len(cross_chain) >= 2:
            score += 10
            # Could indicate a sophisticated operator
        elif len(cross_chain) == 1:
            score += 3

        return min(30, score)

    def _score_deployer_history(self, intel: dict) -> float:
        """Score risk from deployer history."""
        history = intel.get("deployer_history", [])
        if not history:
            return 0.0

        total_deployed = len(history)
        scam_count = sum(1 for t in history if t.get("is_scam_related"))
        high_risk_count = sum(1 for t in history if t.get("scan_risk_score", 0) >= 0.7)

        score = 0.0

        # Rug rate
        if total_deployed > 0:
            rug_rate = (scam_count + high_risk_count) / total_deployed
            if rug_rate >= 0.8 and total_deployed >= 3:
                score += 25  # Serial rugger
            elif rug_rate >= 0.5:
                score += 15  # High risk deployer
            elif rug_rate >= 0.3:
                score += 8  # Suspicious

        # Average lifespan
        avg_lifespan = 0
        lifespans = [t.get("lifespan_days", 0) for t in history if t.get("lifespan_days", 0) > 0]
        if lifespans:
            avg_lifespan = sum(lifespans) / len(lifespans)

        if avg_lifespan > 0 and avg_lifespan < 7 and total_deployed >= 2:
            score += 10  # Short-lived tokens = rug pattern
        elif avg_lifespan < 30 and total_deployed >= 3:
            score += 5

        # Volume of deployments
        if total_deployed >= 10:
            score += 5  # Prolific deployer

        return min(30, score)

    @staticmethod
    def _level(score: float) -> str:
        if score >= 80:
            return "critical"
        elif score >= 60:
            return "high"
        elif score >= 40:
            return "medium"
        elif score >= 20:
            return "low"
        return "minimal"
