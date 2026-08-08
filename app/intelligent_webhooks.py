"""
Intelligent Webhook Processors - Smart event analysis.
Processes Helius, Moralis, and custom webhook events with:
- Whale detection (large transfers)
- Cluster correlation (linked wallets)
- Scam pattern matching (known signatures)
- Cross-platform enrichment (RAG lookup)
- Alert prioritization (critical/high/medium/low)
"""

from __future__ import annotations
from typing import Any

import hashlib
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Alert Severity Levels ───────────────────────────────────

CRITICAL = "critical"  # Confirmed scam, large exploit
HIGH = "high"  # Whale movement, suspicious pattern
MEDIUM = "medium"  # Cluster detected, unusual activity
LOW = "low"  # Routine monitoring


@dataclass
class SmartAlert:
    """Intelligent alert with enrichment and scoring."""

    alert_id: str
    severity: str
    alert_type: str
    source: str  # helius, moralis, custom
    timestamp: datetime
    wallet: str
    chain: str
    description: str
    amount_usd: float = 0.0
    risk_score: float = 0.0
    cluster_id: str | None = None
    related_wallets: list[str] = field(default_factory=list)
    enriched_data: dict = field(default_factory=dict)
    actions: list[str] = field(default_factory=list)  # Recommended actions


class IntelligentWebhookProcessor:
    """Smart webhook event processor with multi-signal analysis."""

    # Thresholds
    WHALE_THRESHOLD_SOL = 100.0  # SOL
    WHALE_THRESHOLD_ETH = 50.0  # ETH
    WHALE_THRESHOLD_USD = 100000.0  # USD

    # Known scam patterns
    SCAM_PATTERNS = {
        "dust_attack": {
            "min_amount": 0.000001,
            "max_amount": 0.001,
            "pattern": "unsolicited_token",
        },
        "approval_drain": {"method": "setApprovalForAll", "risk": "high"},
        "permit2_phishing": {"method": "permit", "risk": "critical"},
    }

    def __init__(self) -> None:
        self._recent_events: dict[str, list[dict]] = defaultdict(list)  # wallet -> events
        self._cluster_cache: dict[str, str] = {}  # wallet -> cluster_id
        self._scam_registry: set[str] = set()  # known scam addresses

    def _generate_alert_id(self, source: str, wallet: str, ts: float) -> str:
        """Generate unique alert ID."""
        data = f"{source}:{wallet}:{int(ts)}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    async def _enrich_wallet(self, wallet: str, chain: str) -> dict:
        """Enrich wallet with RAG lookup and cluster data."""
        enriched = {
            "labels": [],
            "cluster_id": None,
            "is_scammer": False,
            "is_exchange": False,
            "risk_score": 0.0,
        }

        try:
            # Check cluster cache
            if wallet in self._cluster_cache:
                enriched["cluster_id"] = self._cluster_cache[wallet]

            # RAG lookup (if available)
            try:
                from app.chain_cache import get_chain_cache

                cache = get_chain_cache()
                labels = await cache.get("wallet_labels", wallet)
                if labels:
                    enriched["labels"] = labels if isinstance(labels, list) else [labels]
            except (AttributeError, ImportError):
                pass

            # Entity registry check
            try:
                from app.entity_registry import get_entity_registry

                registry = get_entity_registry()
                match = registry.classify_address(wallet)
                if match and match.entity_name:
                    enriched["is_exchange"] = match.entity_type in ("cex", "defi")
                    enriched["labels"].append(f"{match.entity_type}:{match.entity_name}")
            except (AttributeError, ImportError):
                pass

        except Exception as e:
            logger.debug(f"Wallet enrichment failed: {e}")

        return enriched

    async def _check_cluster(self, wallet: str, chain: str) -> str | None:
        """Check if wallet belongs to known cluster."""
        try:
            from app.cluster_detection import get_cluster_detector

            detector = get_cluster_detector()

            # Quick lookup (don't run full detection)
            if hasattr(detector, "_wallet_cluster_map"):
                cluster_id = detector._wallet_cluster_map.get(wallet)
                if cluster_id:
                    self._cluster_cache[wallet] = cluster_id
                    return cluster_id
        except (AttributeError, ImportError):
            pass
        return None

    async def _check_scam_registry(self, wallet: str) -> bool:
        """Check if wallet is in scam database."""
        try:
            # Spam registry
            from app.spam_registry import get_spam_registry

            sr = get_spam_registry()
            if wallet in sr._scam_addresses:
                return True
        except (AttributeError, ImportError):
            pass

        # Threat feeds
        try:
            from app.threat_feeds import get_threat_feeds

            feeds = get_threat_feeds()
            result = await feeds.check_cryptoscamdb(wallet)
            if result and result.get("is_scam"):
                return True
        except (AttributeError, ImportError):
            pass

        return False

    def _calculate_risk_score(self, event: dict, enriched: dict) -> float:
        """Calculate composite risk score (0-100)."""
        score = 0.0

        # Amount-based risk
        amount_usd = event.get("amount_usd", 0)
        if amount_usd > 1000000:
            score += 40
        elif amount_usd > 100000:
            score += 25
        elif amount_usd > 10000:
            score += 10

        # Scam registry
        if enriched.get("is_scammer"):
            score += 50

        # Cluster membership
        if enriched.get("cluster_id"):
            score += 20

        # Exchange wallet (lower risk)
        if enriched.get("is_exchange"):
            score *= 0.3

        # Pattern matching
        if event.get("is_dust_attack"):
            score += 60
        if event.get("is_approval"):
            score += 30

        return min(score, 100.0)

    def _determine_severity(self, risk_score: float, amount_usd: float) -> str:
        """Determine alert severity."""
        if risk_score >= 70 or amount_usd > 500000:
            return CRITICAL
        elif risk_score >= 50 or amount_usd > 100000:
            return HIGH
        elif risk_score >= 30 or amount_usd > 10000:
            return MEDIUM
        else:
            return LOW

    def _generate_actions(self, alert: SmartAlert) -> list[str]:
        """Generate recommended actions based on alert type."""
        actions = []

        if alert.severity == CRITICAL:
            actions.extend(
                [
                    "🚨 IMMEDIATE: Add to scam watchlist",
                    "🚨 Alert affected users if cluster detected",
                    "🚨 Freeze related accounts (if platform)",
                ]
            )
        elif alert.severity == HIGH:
            actions.extend(
                [
                    "⚠️ Monitor wallet for 24h",
                    "⚠️ Check for related cluster activity",
                    "⚠️ Log for pattern analysis",
                ]
            )
        elif alert.severity == MEDIUM:
            actions.extend(
                [
                    "📊 Add to monitoring queue",
                    "📊 Correlate with recent events",
                ]
            )
        else:
            actions.append("📝 Log for historical analysis")

        return actions

    async def process_helius_event(self, event: dict) -> SmartAlert | None:
        """Process Helius webhook event with intelligent analysis."""
        try:
            # Extract key data
            wallet = event.get("feePayer", "")
            signature = event.get("signature", "")
            description = event.get("description", "")
            timestamp = event.get("timestamp", 0)

            if not wallet:
                return None

            # Calculate amounts
            native_transfers = event.get("nativeTransfers", [])
            max_transfer = max(native_transfers, key=lambda t: t.get("amount", 0), default={})
            amount_sol = max_transfer.get("amount", 0) / 1e9
            amount_usd = amount_sol * 150.0  # Approx SOL price

            token_transfers = event.get("tokenTransfers", [])
            is_dust = any(float(tt.get("tokenAmount", {}).get("uiAmount", 0) or 0) < 0.001 for tt in token_transfers)

            # Enrich
            enriched = await self._enrich_wallet(wallet, "solana")
            is_scam = await self._check_scam_registry(wallet)
            enriched["is_scammer"] = is_scam

            # Risk score
            risk_score = self._calculate_risk_score(
                {
                    "amount_usd": amount_usd,
                    "is_dust_attack": is_dust,
                    "is_approval": "approval" in description.lower(),
                },
                enriched,
            )

            severity = self._determine_severity(risk_score, amount_usd)

            # Only create alert if meaningful
            if severity == LOW and risk_score < 20 and amount_usd < 1000:
                return None

            alert = SmartAlert(
                alert_id=self._generate_alert_id("helius", wallet, timestamp),
                severity=severity,
                alert_type="whale_transfer" if amount_sol > self.WHALE_THRESHOLD_SOL else "transfer",
                source="helius",
                timestamp=datetime.fromtimestamp(timestamp, tz=UTC) if timestamp else datetime.now(UTC),
                wallet=wallet,
                chain="solana",
                description=description[:200],
                amount_usd=amount_usd,
                risk_score=risk_score,
                enriched_data=enriched,
            )
            alert.actions = self._generate_actions(alert)

            # Store for correlation
            self._recent_events[wallet].append(
                {
                    "type": "helius",
                    "timestamp": timestamp,
                    "amount": amount_sol,
                    "signature": signature,
                }
            )
            # Keep only last 50 events per wallet
            self._recent_events[wallet] = self._recent_events[wallet][-50:]

            return alert

        except Exception as e:
            logger.warning(f"Helius event processing failed: {e}")
            return None

    async def process_moralis_event(self, event: dict) -> SmartAlert | None:
        """Process Moralis Stream event with intelligent analysis."""
        try:
            # Extract key data
            wallet = event.get("fromAddress", "")
            to_address = event.get("toAddress", "")
            tx_hash = event.get("transactionHash", "")
            chain_id = event.get("chainId", "0x1")
            timestamp = event.get("blockTimestamp", "")
            value = event.get("value", "0")

            if not wallet:
                return None

            # Map chain ID to name
            chain_map = {"0x1": "eth", "0x38": "bsc", "0x89": "polygon", "0xa86a": "avalanche"}
            chain = chain_map.get(chain_id, "eth")

            # Calculate amount
            amount_eth = float(value) / 1e18 if value else 0
            amount_usd = amount_eth * 2500.0  # Approx ETH price

            # Check for token transfers
            erc20_transfers = event.get("erc20Transfers", [])
            is_approval = any(
                t.get("value") == "0" and t.get("toAddress", "").lower() != wallet.lower() for t in erc20_transfers
            )

            # Enrich
            enriched = await self._enrich_wallet(wallet, chain)
            is_scam = await self._check_scam_registry(wallet)
            enriched["is_scammer"] = is_scam

            # Risk score
            risk_score = self._calculate_risk_score(
                {
                    "amount_usd": amount_usd,
                    "is_approval": is_approval,
                },
                enriched,
            )

            severity = self._determine_severity(risk_score, amount_usd)

            if severity == LOW and risk_score < 20 and amount_usd < 1000:
                return None

            alert = SmartAlert(
                alert_id=self._generate_alert_id("moralis", wallet, time.time()),
                severity=severity,
                alert_type="whale_transfer" if amount_eth > self.WHALE_THRESHOLD_ETH else "transfer",
                source="moralis",
                timestamp=datetime.fromisoformat(timestamp.replace("Z", "+00:00")) if timestamp else datetime.now(UTC),
                wallet=wallet,
                chain=chain,
                description=f"Transfer to {to_address[:12]}... ({amount_eth:.4f} ETH)",
                amount_usd=amount_usd,
                risk_score=risk_score,
                enriched_data=enriched,
                related_wallets=[to_address] if to_address else [],
            )
            alert.actions = self._generate_actions(alert)

            # Store for correlation
            self._recent_events[wallet].append(
                {
                    "type": "moralis",
                    "timestamp": time.time(),
                    "amount": amount_eth,
                    "tx_hash": tx_hash,
                }
            )
            self._recent_events[wallet] = self._recent_events[wallet][-50:]

            return alert

        except Exception as e:
            logger.warning(f"Moralis event processing failed: {e}")
            return None

    async def get_correlated_alerts(self, wallet: str, window_hours: int = 24) -> list[dict]:
        """Get correlated events for a wallet."""
        events = self._recent_events.get(wallet, [])
        cutoff = time.time() - (window_hours * 3600)
        return [e for e in events if e.get("timestamp", 0) > cutoff]


# Singleton
_processor: IntelligentWebhookProcessor | None = None


def get_intelligent_processor() -> IntelligentWebhookProcessor:
    global _processor
    if _processor is None:
        _processor = IntelligentWebhookProcessor()
    return _processor
